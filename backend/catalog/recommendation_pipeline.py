"""M10 controlled local ranking pipeline.

Pure, deterministic, zero-network ranking over an already-loaded catalog. It
does not read SQL, Flask config, user sessions, environment secrets or model
providers. The caller owns candidate loading and persistence; this module only
filters, scores and explains.

Public contract
---------------
rank_movies(movies, state, *, limit=12, filters=None, profile_movies=None,
            weights=None, allow_watched=False)

The input movies are dictionaries from the catalog reader. The function never
mutates them or the state mapping. Returned items always have an active,
non-adult movie_id from the input `movies` list; profile_movies may contain
additional active seed movies outside the candidate filters.

This is a rule-based v1, not a trained ranker. It does not claim an offline
improvement over the frozen recommend() baseline; that comparison belongs to a
separate integration run.
"""
from __future__ import annotations

import math
import re
import struct
from bisect import bisect_left
from collections import Counter, defaultdict
from functools import lru_cache, reduce
from itertools import chain, repeat
from operator import add, mul
from typing import NamedTuple

from assistant.conditions import FILTER_KEYS, matches, validate_filters

RECOMMENDATION_VERSION = "m10-local-pipeline-v1"
MODE = "M10_LOCAL_PIPELINE"

DEFAULT_WEIGHTS = {"favorite": 3.0, "want": 2.0, "rating": 1.0}
WEIGHT_KEYS = frozenset(DEFAULT_WEIGHTS)

# Central fixed mixture. Changing any value requires a new version string.
POPULARITY_WEIGHT = 0.15
RATING_CONFIDENCE_WEIGHT = 0.05
CONTENT_WEIGHTS = {
    "genres": 0.55,
    "release_year": 0.15,
    "original_language": 0.10,
    "director_ids": 0.10,
    "actor_ids": 0.05,
    "overview": 0.05,
}
NEGATIVE_SEED_WEIGHT = 2.0
NEGATIVE_SIMILARITY_WEIGHT = 0.60
DIVERSITY_PENALTY_PER_REPEAT = 0.10
MISSING_GENRE_PENALTY = 0.05
MAX_DIVERSITY_PENALTY = 0.50
MAX_REASONS = 8
MAX_EVIDENCE = 12
MAX_OVERVIEW_TOKENS = 300
_INDEXED_PAIR_THRESHOLD = 4096

INTERNAL_FILTER_KEYS = frozenset({"_genre_groups", "_year_groups"})
_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")
_ASCII_TOKEN_TABLE = bytes(
    ord(char.lower()) if char.isalnum() else ord(" ")
    for char in map(chr, range(128)))
_EMPTY_SET = frozenset()


def _finite(value, *, low=None, high=None):
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if low is not None and number < low:
        return None
    if high is not None and number > high:
        return None
    return number


def _rating(value):
    number = _finite(value, low=0.5, high=5.0)
    if number is None or number * 2 != int(number * 2):
        return None
    return number


def _normalise_weights(weights):
    if weights is None:
        return dict(DEFAULT_WEIGHTS)
    if type(weights) is not dict or set(weights) != WEIGHT_KEYS:
        raise ValueError("weights must contain exactly favorite/want/rating")
    result = {}
    for key in WEIGHT_KEYS:
        value = _finite(weights[key], low=0.0, high=10.0)
        if value is None:
            raise ValueError("weights values must be finite and 0..10")
        result[key] = value
    return result


def _normalise_filters(filters):
    if filters is None:
        return {}
    if type(filters) is not dict:
        raise ValueError("filters must be an object")
    unknown = set(filters) - FILTER_KEYS - INTERNAL_FILTER_KEYS
    if unknown:
        raise ValueError("unknown filter keys: %s" % sorted(str(key) for key in unknown))
    public = {key: value for key, value in filters.items()
              if key not in INTERNAL_FILTER_KEYS}
    validate_filters(public)
    cleaned = dict(public)
    if "_genre_groups" in filters:
        groups = filters["_genre_groups"]
        if type(groups) is not list or len(groups) > 16:
            raise ValueError("_genre_groups must be a list")
        cooked = []
        for group in groups:
            if type(group) is not list or not 1 <= len(group) <= 32:
                raise ValueError("_genre_groups entries must be non-empty lists")
            for item in group:
                if type(item) is not str or not 1 <= len(item.strip()) <= 20:
                    raise ValueError("_genre_groups contains invalid text")
            cooked.append([item.strip() for item in group])
        cleaned["_genre_groups"] = cooked
    if "_year_groups" in filters:
        groups = filters["_year_groups"]
        if type(groups) is not list or len(groups) > 16:
            raise ValueError("_year_groups must be a list")
        cooked = []
        for group in groups:
            if type(group) is not list or not 1 <= len(group) <= 15:
                raise ValueError("_year_groups entries must be non-empty lists")
            pairs = []
            for pair in group:
                if type(pair) is not list or len(pair) != 2:
                    raise ValueError("_year_groups entries must be [from, to]")
                if type(pair[0]) is not int or type(pair[1]) is not int:
                    raise ValueError("_year_groups entries must be integer ranges")
                if not 1880 <= pair[0] <= pair[1] <= 2100:
                    raise ValueError("_year_groups has an invalid range")
                pairs.append([pair[0], pair[1]])
            cooked.append(pairs)
        cleaned["_year_groups"] = cooked
    return cleaned


def _index_movies(movies, label):
    if type(movies) is not list:
        raise ValueError("%s must be a list" % label)
    index = {}
    for position, movie in enumerate(movies):
        if type(movie) is not dict:
            raise ValueError("%s[%d] must be an object" % (label, position))
        movie_id = movie.get("movie_id")
        if type(movie_id) is not int or movie_id < 1:
            raise ValueError("%s[%d].movie_id must be a positive int" % (label, position))
        if movie_id in index:
            raise ValueError("duplicate movie_id in %s: %s" % (label, movie_id))
        index[movie_id] = movie
    return index


def _state_lookup(state):
    if state is None:
        return {}
    if type(state) is not dict:
        raise ValueError("state must be an object")
    lookup = {}
    for key, value in state.items():
        if type(key) is int and key > 0:
            movie_id = key
        elif type(key) is str and key.isdigit() and int(key) > 0:
            movie_id = int(key)
        else:
            continue
        if type(value) is dict:
            lookup[movie_id] = value
    return lookup


def _active(movie):
    return movie.get("catalog_status") == "active" and movie.get("is_adult") is not True


def _filter_safe(movie):
    """Give conditions.matches typed/missing values instead of crashing.

    A missing or non-finite year/runtime/genre can never satisfy a hard filter;
    normalising it to None/[] makes matches() return False rather than compare
    arbitrary objects. The original movie object is never changed.
    """
    raw_genres = movie.get("genres")
    genres = []
    if type(raw_genres) is list:
        genres = [item for item in raw_genres if type(item) is str]
    year = movie.get("release_year")
    runtime = movie.get("runtime_minutes")
    language = movie.get("original_language")
    needs_copy = (
        type(raw_genres) is not list
        or len(genres) != (len(raw_genres) if type(raw_genres) is list else 0)
        or (year is not None and type(year) is not int)
        or (runtime is not None and type(runtime) is not int)
        or (language is not None and type(language) is not str)
    )
    if not needs_copy:
        return movie
    safe = dict(movie)
    safe["genres"] = genres
    safe["release_year"] = year if type(year) is int else None
    safe["runtime_minutes"] = runtime if type(runtime) is int else None
    safe["original_language"] = language if type(language) is str else None
    return safe


def _display_title(movie, movie_id):
    title = movie.get("title")
    if type(title) is str and title.strip():
        return "《%s》" % title.strip()
    return "movie_id=%s" % movie_id


def _id_key(value):
    if type(value) not in (list, tuple):
        return ()
    ids = []
    for item in value:
        if type(item) is int and item >= 0:
            ids.append(item)
    return tuple(ids)


def _overview_tokens(value):
    if type(value) is not str or not value:
        return ()
    # Bound pathological text before tokenisation; this is only a lexical hint.
    text = value[:MAX_OVERVIEW_TOKENS * 20]
    if text.isascii():
        tokens = text.translate(_ASCII_TOKEN_TABLE).split()
    else:
        tokens = _TOKEN_RE.findall(text.lower())
    return tuple(dict.fromkeys(tokens[:MAX_OVERVIEW_TOKENS]))


class _ImmutableMovieFeatures(NamedTuple):
    genres: tuple[str, ...]
    genre_set: frozenset[str]
    release_year: int | None
    original_language: str | None
    director_ids: frozenset[int]
    actor_ids: frozenset[int]
    overview: tuple[str, ...] | frozenset[str]


@lru_cache(maxsize=512)
def _genre_features(raw_genres):
    genres = tuple(dict.fromkeys(text for item in raw_genres if (text := item.strip())))
    return genres, frozenset(genres) if genres else _EMPTY_SET


@lru_cache(maxsize=8192)
def _cached_features(movie_id, raw_genres, year, language, directors, actors, overview):
    genres, genre_set = _genre_features(raw_genres)
    return _ImmutableMovieFeatures(
        genres, genre_set, year, language,
        frozenset(directors) if directors else _EMPTY_SET,
        frozenset(actors) if actors else _EMPTY_SET,
        _overview_tokens(overview) if overview else ())


def _features(movie, *, similarity=True, overview=True, directors=True, actors=True):
    """Cache by typed content so same-ID snapshots and mutable rows stay isolated."""
    raw_genres = movie.get("genres")
    genres = []
    if type(raw_genres) is list:
        for item in raw_genres:
            if type(item) is str:
                genres.append(item)
    genres = tuple(genres)
    if not similarity:
        return _cached_features(movie["movie_id"], genres, None, None, (), (), None)
    year = movie.get("release_year")
    if type(year) is not int or not 1880 <= year <= 2100:
        year = None
    language = movie.get("original_language")
    if type(language) is not str or not language:
        language = None
    text = movie.get("overview") if overview else None
    return _cached_features(
        movie["movie_id"], genres, year, language,
        _id_key(movie.get("director_ids")) if directors else (),
        _id_key(movie.get("actor_ids")) if actors else (),
        text[:MAX_OVERVIEW_TOKENS * 20] if type(text) is str and text else None)


def _with_overview_set(feature):
    # Keep the cached tokens compact. The pairwise path and final evidence
    # need set intersections, while the indexed path only iterates the tokens.
    return feature._replace(overview=frozenset(feature.overview) if feature.overview else _EMPTY_SET)


def _similarity(candidate, seed):
    """Return weighted similarity and actual matched fields.

    Missing values contribute zero; no value is inferred.
    """
    parts = []
    total = 0.0

    common = candidate.genre_set & seed.genre_set
    if common:
        value = len(common) / (len(candidate.genre_set) + len(seed.genre_set) - len(common))
        parts.append(("GENRE_MATCH", "genres", CONTENT_WEIGHTS["genres"], value, sorted(common)))
        total += CONTENT_WEIGHTS["genres"] * value

    if candidate.release_year is not None and seed.release_year is not None:
        value = max(0.0, 1.0 - abs(candidate.release_year - seed.release_year) / 30.0)
        if value > 0:
            parts.append(("YEAR_NEAR", "release_year", CONTENT_WEIGHTS["release_year"],
                          value, [seed.release_year, candidate.release_year]))
            total += CONTENT_WEIGHTS["release_year"] * value

    if (candidate.original_language is not None
            and candidate.original_language == seed.original_language):
        parts.append(("LANGUAGE_MATCH", "original_language", CONTENT_WEIGHTS["original_language"],
                      1.0, candidate.original_language))
        total += CONTENT_WEIGHTS["original_language"]

    directors = candidate.director_ids & seed.director_ids
    if directors:
        parts.append(("DIRECTOR_MATCH", "director_ids", CONTENT_WEIGHTS["director_ids"],
                      1.0, sorted(directors)))
        total += CONTENT_WEIGHTS["director_ids"]

    common = candidate.actor_ids & seed.actor_ids
    if common:
        value = len(common) / (len(candidate.actor_ids) + len(seed.actor_ids) - len(common))
        parts.append(("ACTOR_OVERLAP", "actor_ids", CONTENT_WEIGHTS["actor_ids"],
                      value, sorted(common)))
        total += CONTENT_WEIGHTS["actor_ids"] * value

    common = candidate.overview & seed.overview
    if common:
        value = len(common) / (len(candidate.overview) + len(seed.overview) - len(common))
        parts.append(("OVERVIEW_LEXICAL_HINT", "overview", CONTENT_WEIGHTS["overview"],
                      value, sorted(common)[:5]))
        total += CONTENT_WEIGHTS["overview"] * value
    return total, parts


def _append_reason(reasons, codes, text, code):
    codes.add(code)
    if len(reasons) < MAX_REASONS and text not in reasons:
        reasons.append(text)


def _profile_seed_rows(profile_index, state, weights):
    """Collect only profile entries with an explicit positive/negative signal."""
    rows = []
    for movie_id in sorted(state.keys() & profile_index.keys()):
        movie = profile_index[movie_id]
        if not _active(movie):
            continue
        behaviour = state[movie_id]
        signals = []
        positive_weight = 0.0
        rating = _rating(behaviour.get("rating"))
        blocked = behaviour.get("not_interested") is True
        if not blocked:
            if behaviour.get("favorite") is True:
                positive_weight += weights["favorite"]
                signals.append("FAVORITE")
            if behaviour.get("want") is True:
                positive_weight += weights["want"]
                signals.append("WANT")
            if rating is not None and rating > 3.0:
                positive_weight += weights["rating"] * (rating - 3.0)
                signals.append("HIGH_RATING")

        negative_weight = 0.0
        negative_signals = []
        negative_value = None
        if blocked:
            negative_weight = NEGATIVE_SEED_WEIGHT
            negative_signals.append("NOT_INTERESTED")
            negative_value = True
        elif rating is not None and rating < 3.0:
            negative_weight = min(NEGATIVE_SEED_WEIGHT, 3.0 - rating)
            negative_signals.append("LOW_RATING")
            negative_value = rating

        if positive_weight > 0 or negative_weight > 0:
            rows.append((
                movie_id, movie, positive_weight, signals, negative_weight,
                negative_signals, negative_value))
    return rows


def _seed_profiles(rows, candidate_features, movie_index):
    """Attach features only to behaviour seeds, reusing same-movie features."""
    positive, negative = [], []
    for movie_id, movie, positive_weight, signals, negative_weight, negative_signals, negative_value in rows:
        feature = candidate_features.get(movie_id)
        if feature is None or movie_index.get(movie_id) is not movie:
            feature = _features(movie)
        feature = _with_overview_set(feature)
        if positive_weight > 0:
            positive.append((movie_id, movie, feature, positive_weight, signals))
        if negative_weight > 0:
            negative.append((
                movie_id, movie, feature, negative_weight, negative_signals,
                negative_value))

    positive_total = sum(item[3] for item in positive)
    negative_total = sum(item[3] for item in negative)
    positive = [(mid, movie, feature, weight / positive_total, signals)
                for mid, movie, feature, weight, signals in positive]
    negative = [(mid, movie, feature, weight / negative_total, signals, value)
                for mid, movie, feature, weight, signals, value in negative]
    positive.sort(key=lambda item: (-item[3], item[0]))
    negative.sort(key=lambda item: (-item[3], item[0]))
    return positive, negative


def _prior(movie):
    popularity = _finite(movie.get("catalog_popularity"), low=0.0, high=1.0)
    rating = _finite(movie.get("tmdb_rating"), low=0.0, high=10.0)
    votes = movie.get("vote_count")
    if type(votes) is not int or isinstance(votes, bool) or votes < 0:
        votes = None
    popularity_component = POPULARITY_WEIGHT * popularity if popularity is not None else 0.0
    rating_component = 0.0
    confidence = None
    if rating is not None and votes is not None and votes > 0:
        try:
            confidence = votes / (votes + 100.0)
        except OverflowError:
            confidence = 1.0
        rating_component = RATING_CONFIDENCE_WEIGHT * (rating / 10.0) * confidence
    return popularity_component, rating_component, popularity, rating, votes, confidence


def _year_term(candidate_year, seed_year):
    if candidate_year is None or seed_year is None:
        return 0.0
    return CONTENT_WEIGHTS["release_year"] * max(
        0.0, 1.0 - abs(candidate_year - seed_year) / 30.0)


def _jaccard_term(common, total_size, weight):
    return weight * (common / (total_size - common)) if common else 0.0


def _numeric_similarity(candidate, seed):
    weights = CONTENT_WEIGHTS
    score = _jaccard_term(
        len(candidate.genre_set & seed.genre_set),
        len(candidate.genre_set) + len(seed.genre_set), weights["genres"])
    score += _year_term(candidate.release_year, seed.release_year)
    if candidate.original_language is not None and candidate.original_language == seed.original_language:
        score += weights["original_language"]
    if not candidate.director_ids.isdisjoint(seed.director_ids):
        score += weights["director_ids"]
    score += _jaccard_term(
        len(candidate.actor_ids & seed.actor_ids),
        len(candidate.actor_ids) + len(seed.actor_ids), weights["actor_ids"])
    score += _jaccard_term(
        len(candidate.overview & seed.overview),
        len(candidate.overview) + len(seed.overview), weights["overview"])
    return score


def _postings(seed_values):
    index = defaultdict(list)
    for position, values in enumerate(seed_values):
        for value in values:
            index[value].append(position)
    return dict(index)


def _hits(index, values):
    return chain.from_iterable(map(index.get, values, repeat(())))


class _IndexedSeedScores:
    """Request-local seed tables with exact, ordered accumulation.

    A pre-summed language/year bonus can only be returned as-is when there
    are no later matches. Otherwise we patch the affected seed terms and
    replay their original order to preserve IEEE 754 rounding and ties.
    """

    def __init__(self, positive, negative):
        seeds = positive + negative
        features = [row[2] for row in seeds]
        self.split = len(positive)
        self.weights = tuple(row[3] for row in seeds)
        self.genre_index = _postings(f.genre_set for f in features)
        self.director_index = _postings(f.director_ids for f in features)
        self.actor_index = _postings(f.actor_ids for f in features)
        self.directors = frozenset(self.director_index)
        self.actors = frozenset(self.actor_index)
        self.genres = frozenset(self.genre_index)
        self.overview_index = _postings(f.overview for f in features)
        self.genre_sizes = tuple(len(f.genre_set) for f in features)
        self.actor_sizes = tuple(len(f.actor_ids) for f in features)
        self.overview_sizes = tuple(len(f.overview) for f in features)
        self.overview_counters = {}
        if len(seeds) <= 256 and sum(self.overview_sizes) > 2 * len(self.overview_index):
            # Dense postings would visit the same seeds for many tokens.
            # Give each seed a separate counter in a packed integer. The
            # width bounds the maximum intersection, so addition cannot carry
            # into the next seed. Bytes decode all counters in seed order.
            width = 1 if max(self.overview_sizes) < 256 else 2
            shifts = [1 << (width * 8 * position) for position in range(len(seeds))]
            self.overview_counters = {
                word: sum(shifts[position] for position in positions)
                for word, positions in self.overview_index.items()}
            self.overview_bytes = width * len(seeds)
            self.overview_unpack = struct.Struct(f"<{len(seeds)}H").unpack if width == 2 else None
            self.overview_tables = {}
        self.zeros = (0.0,) * len(seeds)
        year_terms = tuple(_year_term(delta, 0) for delta in range(30))
        self.years = {
            year: tuple(
                year_terms[delta]
                if f.release_year is not None and (delta := abs(year - f.release_year)) < 30 else 0.0
                for f in features)
            for year in range(1880, 2101)
        }
        self.year_bonus = {year: self._totals(tuple(map(mul, terms, self.weights)))
                           for year, terms in self.years.items()}
        language_index = _postings((f.original_language,) for f in features)
        language_index.pop(None, None)
        self.languages = {}
        self.lang_pos_bonus = {}
        self.lang_neg_bonus = {}
        for language, positions in language_index.items():
            terms = list(self.zeros)
            for position in positions:
                terms[position] = CONTENT_WEIGHTS["original_language"]
            self.languages[language] = tuple(terms)
            self.lang_pos_bonus[language], self.lang_neg_bonus[language] = self._totals(
                tuple(map(mul, terms, self.weights)))
        self.genre_terms = {_EMPTY_SET: self.zeros}
        self.bases = {}
        self.year_language_bonus = {}
        # Bound retained request-local vectors even if the profile is large.
        self.cache_limit = min(2048, 100_000 // len(seeds))

    def _totals(self, weighted):
        # Python 3.12's sum(float_terms) is compensated. The v1 loop is not.
        return (reduce(add, weighted[:self.split], 0.0),
                reduce(add, weighted[self.split:], 0.0))

    def _base(self, feature):
        key = (feature.genre_set, feature.release_year, feature.original_language)
        cached = self.bases.get(key)
        if cached is not None:
            return cached
        genres = self.genre_terms.get(feature.genre_set)
        if genres is None:
            genre_hits = feature.genre_set & self.genres
            genres = self.zeros
            if genre_hits:
                terms = list(self.zeros)
                size = len(feature.genre_set)
                weight = CONTENT_WEIGHTS["genres"]
                for position, common in Counter(_hits(self.genre_index, genre_hits)).items():
                    terms[position] = _jaccard_term(common, size + self.genre_sizes[position], weight)
                genres = tuple(terms)
            if len(self.genre_terms) < self.cache_limit:
                self.genre_terms[feature.genre_set] = genres
        years = self.years.get(feature.release_year, self.zeros)
        languages = self.languages.get(feature.original_language, self.zeros)
        if genres is self.zeros and years is self.zeros:
            cached = languages
        elif genres is self.zeros and languages is self.zeros:
            cached = years
        else:
            cached = tuple(map(add, map(add, genres, years), languages))
        if len(self.bases) < self.cache_limit:
            self.bases[key] = cached
        return cached

    def scores(self, feature):
        terms = self._base(feature)
        weights = CONTENT_WEIGHTS
        updates = {}
        director_hits = self.directors & feature.director_ids
        if director_hits:
            for position in set(_hits(self.director_index, director_hits)):
                updates[position] = terms[position] + weights["director_ids"]
        actor_hits = self.actors & feature.actor_ids
        if actor_hits:
            size = len(feature.actor_ids)
            weight = weights["actor_ids"]
            counts = {}
            for position in _hits(self.actor_index, actor_hits):
                counts[position] = counts.get(position, 0) + 1
            for position, common in counts.items():
                updates[position] = updates.get(position, terms[position]) + _jaccard_term(
                    common, size + self.actor_sizes[position], weight)
        if feature.overview and self.overview_counters:
            counts = sum(map(self.overview_counters.get, feature.overview, repeat(0)))
            if counts:
                if updates:
                    terms = list(terms)
                    for position, value in updates.items():
                        terms[position] = value
                size = len(feature.overview)
                tables = self.overview_tables.get(size)
                if tables is None:
                    weight = weights["overview"]
                    tables = tuple(
                        tuple(_jaccard_term(common, size + seed_size, weight)
                              for common in range(min(size, seed_size) + 1))
                        for seed_size in self.overview_sizes)
                    self.overview_tables[size] = tables
                counts = counts.to_bytes(self.overview_bytes, "little")
                if self.overview_unpack is not None:
                    counts = self.overview_unpack(counts)
                weighted = [weight * (base + table[common])
                            for weight, base, table, common in zip(self.weights, terms, tables, counts)]
                return self._totals(weighted)
        elif feature.overview and self.overview_index:
            size = len(feature.overview)
            weight = weights["overview"]
            for position, common in Counter(_hits(self.overview_index, feature.overview)).items():
                updates[position] = updates.get(position, terms[position]) + _jaccard_term(
                    common, size + self.overview_sizes[position], weight)
        if not updates and feature.genre_set.isdisjoint(self.genres):
            year = feature.release_year
            language = feature.original_language
            if year is None:
                return self.lang_pos_bonus.get(language, 0.0), self.lang_neg_bonus.get(language, 0.0)
            if language not in self.languages:
                return self.year_bonus[year]
            key = (year, language)
            totals = self.year_language_bonus.get(key)
            if totals is None:
                totals = self._totals(tuple(map(mul, terms, self.weights)))
                if len(self.year_language_bonus) < self.cache_limit:
                    self.year_language_bonus[key] = totals
            return totals
        weighted = list(map(mul, terms, self.weights))
        for position, value in updates.items():
            weighted[position] = self.weights[position] * value
        return self._totals(weighted)


def _score_numeric(movie, feature, positive, negative, indexed):
    prior = _prior(movie)
    if indexed is None:
        content = 0.0
        negative_similarity = 0.0
        if positive or negative:
            feature = _with_overview_set(feature)
        for _, _, seed, weight, _ in positive:
            content += weight * _numeric_similarity(feature, seed)
        for _, _, seed, weight, _, _ in negative:
            negative_similarity += weight * _numeric_similarity(feature, seed)
    else:
        content, negative_similarity = indexed.scores(feature)
    negative_penalty = NEGATIVE_SIMILARITY_WEIGHT * negative_similarity
    raw_score = prior[0] + prior[1] + content - negative_penalty
    return raw_score, content, negative_penalty


def _build_explanation(movie_id, movie, candidate_features, positive, negative,
                       numeric, is_rewatch, penalty, selection_score):
    raw_score, content, negative_penalty = numeric
    popularity_component, rating_component, popularity, rating, votes, confidence = _prior(movie)
    penalty = round(penalty, 8)
    if positive or negative:
        candidate_features = _with_overview_set(candidate_features)
    reasons, codes, evidence = [], set(), []
    matches_evidence = []
    positive_matches = 0

    for seed_id, seed_movie, seed_features, seed_weight, signals in positive:
        similarity, parts = _similarity(candidate_features, seed_features)
        if similarity <= 0:
            continue
        signal = signals[0] if signals else "PROFILE"
        seed_label = None
        for code, field, weight, value, actual in parts:
            contribution = seed_weight * weight * value
            if contribution <= 0:
                continue
            positive_matches += 1
            matches_evidence.append((
                round(contribution, 8), "USER_BEHAVIOR", field, seed_id,
                (code, actual, signal, signals)))
            if len(reasons) == MAX_REASONS:
                codes.add(code)
                continue
            if seed_label is None:
                seed_label = _display_title(seed_movie, seed_id)
            if code == "GENRE_MATCH":
                text = "类型与%s重合：%s" % (seed_label, "、".join(actual))
            elif code == "YEAR_NEAR":
                text = "上映年代接近%s（%s/%s）" % (
                    seed_label, actual[0], actual[1])
            elif code == "LANGUAGE_MATCH":
                text = "语言与%s一致：%s" % (seed_label, actual)
            elif code == "DIRECTOR_MATCH":
                text = "与%s由同一位导演执导" % seed_label
            elif code == "ACTOR_OVERLAP":
                text = "与%s有共同出演的演员" % seed_label
            else:
                text = "简介词面与%s重合：%s（仅为词面线索）" % (
                    seed_label, "、".join(actual))
            _append_reason(reasons, codes, text, code)

    if popularity is not None:
        evidence.append({
            "source": "CATALOG", "field": "catalog_popularity", "value": popularity,
            "match": "POPULARITY_PRIOR", "seed_movie_id": None, "signal": "PRIOR",
            "contribution": round(popularity_component, 8),
        })
        _append_reason(
            reasons, codes,
            "库内热度先验：catalog_popularity=%.3f" % popularity,
            "POPULARITY_PRIOR")

    if rating is not None and confidence is not None:
        evidence.append({
            "source": "CATALOG", "field": "tmdb_rating", "value": rating,
            "vote_count": votes, "confidence": round(confidence, 8),
            "match": "TMDB_RATING_PRIOR", "seed_movie_id": None, "signal": "PRIOR",
            "contribution": round(rating_component, 8),
        })
        _append_reason(
            reasons, codes,
            "TMDB评分可信度先验：%.1f/10，%d票" % (rating, votes),
            "TMDB_RATING_PRIOR")

    for seed_id, seed_movie, seed_features, seed_weight, signals, value in negative:
        similarity, parts = _similarity(candidate_features, seed_features)
        if similarity <= 0:
            continue
        fields = [{"field": field, "value": actual} for _, field, _, _, actual in parts]
        signal = signals[0] if signals else "NEGATIVE"
        evidence.append({
            "source": "USER_BEHAVIOR",
            "field": "not_interested" if signal == "NOT_INTERESTED" else "rating",
            "value": value,
            "match": "NEGATIVE_SIMILARITY", "seed_movie_id": seed_id,
            "signal": signal, "signals": list(signals),
            "matched_fields": fields,
            "contribution": round(-NEGATIVE_SIMILARITY_WEIGHT * seed_weight * similarity, 8),
        })
        if len(reasons) < MAX_REASONS:
            _append_reason(
                reasons, codes,
                "与负反馈影片%s内容相似，已降权" % _display_title(seed_movie, seed_id),
                "NEGATIVE_SIMILARITY")
        else:
            codes.add("NEGATIVE_SIMILARITY")

    if not positive:
        if popularity is not None or rating_component > 0:
            _append_reason(
                reasons, codes,
                "冷启动：无正向偏好，使用库内热度/评分可信度先验",
                "COLD_START_PRIOR")
        else:
            _append_reason(
                reasons, codes,
                "冷启动：无正向偏好且无可靠热度/评分资料，按 movie_id 稳定排序",
                "COLD_START_NO_EVIDENCE")
    elif positive_matches == 0:
        _append_reason(
            reasons, codes,
            "已有偏好种子，但候选在类型/年代/语言/导演/演员/简介上均无匹配",
            "NO_MATCHING_FEATURES")

    if is_rewatch:
        evidence.append({
            "source": "USER_BEHAVIOR", "field": "watched", "value": True,
            "match": "REWATCH_ALLOWED", "seed_movie_id": movie_id,
            "signal": "REWATCH_ALLOWED", "contribution": 0.0,
        })
        _append_reason(
            reasons, codes,
            "该片已看，调用方显式允许重温",
            "REWATCH_ALLOWED")

    if not reasons:
        _append_reason(
            reasons, codes,
            "满足硬条件，但没有可验证的偏好或可靠资料证据",
            "NO_EVIDENCE")

    if not candidate_features.genres:
        _append_reason(
            reasons, codes,
            "类型字段缺失，应用固定多样性保守惩罚",
            "MISSING_GENRE_PENALTY")

    # Preserve the original stable evidence order. Render positive records
    # only after truncation to the evidence limit.
    matches_evidence.extend(
        (item["contribution"], item["source"], item["field"], item["seed_movie_id"] or 0, item)
        for item in evidence)
    matches_evidence.sort(key=lambda item: (-abs(item[0]), item[1], item[2], item[3]))
    evidence = []
    for contribution, source, field, seed_id, detail in matches_evidence[:MAX_EVIDENCE]:
        if type(detail) is dict:
            evidence.append(detail)
            continue
        code, actual, signal, signals = detail
        record = {
            "source": source, "field": field, "value": actual, "match": code,
            "seed_movie_id": seed_id, "signal": signal, "signals": list(signals),
            "contribution": contribution,
        }
        if code == "OVERVIEW_LEXICAL_HINT":
            record["note"] = "简介词面重合，仅为线索，不是语义理解或已核实氛围标签"
        evidence.append(record)
    return {
        "movie_id": movie_id,
        "title": movie.get("title") if type(movie.get("title")) is str else "",
        "score": round(raw_score, 8),
        "components": {
            "baseline": round(popularity_component, 8),
            "rating_confidence": round(rating_component, 8),
            "preference": round(content, 8),
            "negative": round(-negative_penalty, 8),
            "diversity": penalty,
        },
        "reasons": reasons,
        "reason_codes": sorted(codes),
        "evidence": evidence,
        "genres": list(candidate_features.genres),
        "diversity_penalty": penalty,
        "selection_score": selection_score,
        "recommendation_version": RECOMMENDATION_VERSION,
    }


def _select_movies(groups, limit):
    # Genre order does not affect diversity. Grouping gives all movies with
    # the same genres one penalty and avoids rescanning them after saturation.
    for group in groups.values():
        group.sort()
    ordered_groups = sorted(
        ((round(group[-1][0], 8), genres, group) for genres, group in groups.items()),
        key=lambda row: row[0], reverse=True)
    genre_counts = {}
    for _ in range(limit):
        best_key = (-math.inf, 0)
        for upper_bound, genres, group in ordered_groups:
            if upper_bound < best_key[0]:
                break
            if not group:
                continue
            penalty = MISSING_GENRE_PENALTY
            if genres:
                repeat = sum(genre_counts.get(genre, 0) for genre in genres)
                penalty = min(MAX_DIVERSITY_PENALTY, DIVERSITY_PENALTY_PER_REPEAT * repeat)
            position = len(group) - 1
            raw, negative_id, _, _ = group[position]
            score = round(raw - penalty, 8)
            if score < best_key[0]:
                continue
            # Selection compares rounded scores. A lower raw score can still
            # tie and win by ID. Equal raw scores are already ordered by ID.
            tied = position
            while tied and round(group[tied - 1][0] - penalty, 8) == score:
                tied_raw = group[tied][0]
                tied = bisect_left(group, (tied_raw,), hi=tied) - 1
                if tied < 0 or round(group[tied][0] - penalty, 8) != score:
                    break
                if group[tied][1] > negative_id:
                    position = tied
                    negative_id = group[tied][1]
            key = (score, negative_id)
            if key > best_key:
                best_key = key
                best_group, best_position, best_penalty, best_genres = group, position, penalty, genres
        raw, negative_id, content, negative_penalty = best_group.pop(best_position)
        for genre in best_genres:
            genre_counts[genre] = genre_counts.get(genre, 0) + 1
        yield -negative_id, (raw, content, negative_penalty), best_penalty, best_key[0]


def rank_movies(movies, state, *, limit=12, filters=None, profile_movies=None,
                weights=None, allow_watched=False):
    """Rank active, non-adult movies with a deterministic local rule mix.

    `filters` is the internal trusted condition object used by
    assistant.conditions.matches; it may include `_genre_groups` /
    `_year_groups` produced by merge_for_tool. `profile_movies` may include
    active seeds outside the candidate filters. `allow_watched=True` permits
    watched items only when filters.unwatched is not explicitly True;
    not_interested is never re-enabled.

    `candidate_count` counts active, non-adult rows that pass the watched /
    not_interested policy and hard filters, before `limit` is applied. Item
    `score` is the raw score; `diversity_penalty` is subtracted separately to
    produce `selection_score`, and `components["diversity"]` mirrors it.
    """
    if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise ValueError("limit must be an int in 1..50")
    if type(allow_watched) is not bool:
        raise ValueError("allow_watched must be boolean")
    movie_index = _index_movies(movies, "movies")
    profile_index = movie_index if profile_movies is None else _index_movies(
        profile_movies, "profile_movies")
    state_by_movie = _state_lookup(state)
    normal_weights = _normalise_weights(weights)
    normal_filters = _normalise_filters(filters)

    candidates = []
    no_behaviour = {}
    for movie_id in sorted(movie_index):
        movie = movie_index[movie_id]
        if not _active(movie):
            continue
        behaviour = state_by_movie.get(movie_id, no_behaviour)
        if behaviour.get("not_interested") is True:
            continue
        if behaviour.get("watched") is True:
            if not allow_watched or normal_filters.get("unwatched") is True:
                continue
        if normal_filters:
            if not matches(_filter_safe(movie), normal_filters, behaviour):
                continue
        elif behaviour.get("not_interested"):
            continue
        candidates.append(movie_id)

    seed_rows = _profile_seed_rows(profile_index, state_by_movie, normal_weights)
    if seed_rows:
        need_overview = any(
            type(row[1].get("overview")) is str and bool(row[1].get("overview").strip())
            for row in seed_rows)
        need_directors = any(_id_key(row[1].get("director_ids")) for row in seed_rows)
        need_actors = any(_id_key(row[1].get("actor_ids")) for row in seed_rows)
        candidate_features = {
            movie_id: _features(
                movie_index[movie_id], overview=need_overview, directors=need_directors,
                actors=need_actors)
            for movie_id in candidates
        }
    else:
        candidate_features = {
            movie_id: _features(movie_index[movie_id], similarity=False)
            for movie_id in candidates
        }
    positive, negative = _seed_profiles(seed_rows, candidate_features, movie_index)

    indexed = None
    if (len(candidates) >= 64
            and len(candidates) * (len(positive) + len(negative)) >= _INDEXED_PAIR_THRESHOLD):
        indexed = _IndexedSeedScores(positive, negative)
    groups = defaultdict(list)
    for movie_id, feature in candidate_features.items():
        raw, content, negative_penalty = _score_numeric(
            movie_index[movie_id], feature, positive, negative, indexed)
        groups[feature.genre_set].append((raw, -movie_id, content, negative_penalty))

    selected = [
        _build_explanation(
            movie_id, movie_index[movie_id], candidate_features[movie_id], positive, negative, numeric,
            allow_watched and state_by_movie.get(movie_id, no_behaviour).get("watched") is True,
            penalty, selection_score)
        for movie_id, numeric, penalty, selection_score in _select_movies(groups, min(limit, len(candidates)))
    ]

    return {
        "items": selected,
        "candidate_count": len(candidates),
        "mode": MODE,
        "paid_model_tokens": 0,
        "recommendation_version": RECOMMENDATION_VERSION,
        "profile_seed_count": len(positive),
        "negative_seed_count": len(negative),
        "cold_start": not positive,
    }


__all__ = [
    "RECOMMENDATION_VERSION",
    "MODE",
    "DEFAULT_WEIGHTS",
    "rank_movies",
]
