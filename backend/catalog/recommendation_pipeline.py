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

INTERNAL_FILTER_KEYS = frozenset({"_genre_groups", "_year_groups"})
_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")


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


def _id_set(value):
    if type(value) not in (list, tuple):
        return frozenset()
    result = set()
    for item in value:
        if type(item) is int and not isinstance(item, bool) and item >= 0:
            result.add(item)
    return frozenset(result)


def _overview_tokens(value):
    if type(value) is not str or not value:
        return frozenset()
    # Bound pathological text before tokenisation; this is only a lexical hint.
    return frozenset(_TOKEN_RE.findall(value[:MAX_OVERVIEW_TOKENS * 20].lower())[:MAX_OVERVIEW_TOKENS])


def _features(movie, *, similarity=True, overview=True, directors=True,
              actors=True):
    """Build per-movie sets lazily, only for fields this call will use.

    `similarity=False` returns only genres (the cold-start output/diversity
    needs); the expensive overview tokenisation and people sets are skipped.
    Missing fields stay empty/None and are never inferred.
    """
    genres = []
    raw_genres = movie.get("genres")
    if type(raw_genres) is list:
        for item in raw_genres:
            if type(item) is str and item.strip() and item.strip() not in genres:
                genres.append(item.strip())
    if not similarity:
        return {"genres": tuple(genres)}
    year = movie.get("release_year")
    if type(year) is not int or isinstance(year, bool) or not 1880 <= year <= 2100:
        year = None
    language = movie.get("original_language")
    if type(language) is not str or not language:
        language = None
    return {
        "genres": tuple(genres),
        "genre_set": frozenset(genres),
        "release_year": year,
        "original_language": language,
        "director_ids": _id_set(movie.get("director_ids")) if directors else frozenset(),
        "actor_ids": _id_set(movie.get("actor_ids")) if actors else frozenset(),
        "overview": _overview_tokens(movie.get("overview")) if overview else frozenset(),
    }


def _similarity(candidate, seed):
    """Return weighted similarity and actual matched fields.

    Missing values contribute zero; no value is inferred.
    """
    parts = []
    total = 0.0

    union = candidate["genre_set"] | seed["genre_set"]
    if union:
        common = candidate["genre_set"] & seed["genre_set"]
        value = len(common) / len(union)
        if value > 0:
            parts.append({
                "code": "GENRE_MATCH", "field": "genres", "weight": CONTENT_WEIGHTS["genres"],
                "value": value, "actual": sorted(common),
            })
            total += CONTENT_WEIGHTS["genres"] * value

    if candidate["release_year"] is not None and seed["release_year"] is not None:
        value = max(0.0, 1.0 - abs(candidate["release_year"] - seed["release_year"]) / 30.0)
        if value > 0:
            parts.append({
                "code": "YEAR_NEAR", "field": "release_year",
                "weight": CONTENT_WEIGHTS["release_year"], "value": value,
                "actual": [seed["release_year"], candidate["release_year"]],
            })
            total += CONTENT_WEIGHTS["release_year"] * value

    if (candidate["original_language"] is not None
            and candidate["original_language"] == seed["original_language"]):
        parts.append({
            "code": "LANGUAGE_MATCH", "field": "original_language",
            "weight": CONTENT_WEIGHTS["original_language"], "value": 1.0,
            "actual": candidate["original_language"],
        })
        total += CONTENT_WEIGHTS["original_language"]

    directors = candidate["director_ids"] & seed["director_ids"]
    if directors:
        parts.append({
            "code": "DIRECTOR_MATCH", "field": "director_ids",
            "weight": CONTENT_WEIGHTS["director_ids"], "value": 1.0,
            "actual": sorted(directors),
        })
        total += CONTENT_WEIGHTS["director_ids"]

    actors = candidate["actor_ids"] | seed["actor_ids"]
    if actors:
        common = candidate["actor_ids"] & seed["actor_ids"]
        value = len(common) / len(actors)
        if value > 0:
            parts.append({
                "code": "ACTOR_OVERLAP", "field": "actor_ids",
                "weight": CONTENT_WEIGHTS["actor_ids"], "value": value,
                "actual": sorted(common),
            })
            total += CONTENT_WEIGHTS["actor_ids"] * value

    overview_union = candidate["overview"] | seed["overview"]
    if overview_union:
        common = candidate["overview"] & seed["overview"]
        value = len(common) / len(overview_union)
        if value > 0:
            parts.append({
                "code": "OVERVIEW_LEXICAL_HINT", "field": "overview",
                "weight": CONTENT_WEIGHTS["overview"], "value": value,
                "actual": sorted(common)[:5],
                "note": "简介词面重合，仅为线索，不是语义理解或已核实氛围标签",
            })
            total += CONTENT_WEIGHTS["overview"] * value
    return total, parts


def _append_reason(reasons, codes, text, code):
    codes.add(code)
    if text not in reasons and len(reasons) < MAX_REASONS:
        reasons.append(text)


def _profile_seed_rows(profile_index, state, weights):
    """Collect only profile entries with an explicit positive/negative signal."""
    rows = []
    for movie_id in sorted(profile_index):
        movie = profile_index[movie_id]
        if not _active(movie):
            continue
        behaviour = state.get(movie_id, {})
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


def _score_candidate(movie_id, movie, candidate_features, positive, negative,
                     is_cold_start, is_rewatch):
    popularity_component, rating_component, popularity, rating, votes, confidence = _prior(movie)
    reasons, codes, evidence = [], set(), []
    content = 0.0
    positive_matches = 0

    for seed_id, seed_movie, seed_features, seed_weight, signals in positive:
        similarity, parts = _similarity(candidate_features, seed_features)
        if similarity <= 0:
            continue
        content += seed_weight * similarity
        seed_label = _display_title(seed_movie, seed_id)
        signal = signals[0] if signals else "PROFILE"
        for part in parts:
            contribution = seed_weight * part["weight"] * part["value"]
            if contribution <= 0:
                continue
            positive_matches += 1
            evidence.append({
                "source": "USER_BEHAVIOR", "field": part["field"],
                "value": part["actual"], "match": part["code"],
                "seed_movie_id": seed_id, "signal": signal, "signals": list(signals),
                "contribution": round(contribution, 8),
                **({"note": part["note"]} if "note" in part else {}),
            })
            if part["code"] == "GENRE_MATCH":
                text = "类型与%s重合：%s" % (seed_label, "、".join(part["actual"]))
            elif part["code"] == "YEAR_NEAR":
                text = "上映年代接近%s（%s/%s）" % (
                    seed_label, part["actual"][0], part["actual"][1])
            elif part["code"] == "LANGUAGE_MATCH":
                text = "语言与%s一致：%s" % (seed_label, part["actual"])
            elif part["code"] == "DIRECTOR_MATCH":
                text = "与%s由同一位导演执导" % seed_label
            elif part["code"] == "ACTOR_OVERLAP":
                text = "与%s有共同出演的演员" % seed_label
            else:
                text = "简介词面与%s重合：%s（仅为词面线索）" % (
                    seed_label, "、".join(part["actual"]))
            _append_reason(reasons, codes, text, part["code"])

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

    negative_similarity = 0.0
    for seed_id, seed_movie, seed_features, seed_weight, signals, value in negative:
        similarity, parts = _similarity(candidate_features, seed_features)
        if similarity <= 0:
            continue
        negative_similarity += seed_weight * similarity
        fields = [{"field": part["field"], "value": part["actual"]}
                  for part in parts]
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
        _append_reason(
            reasons, codes,
            "与负反馈影片%s内容相似，已降权" % _display_title(seed_movie, seed_id),
            "NEGATIVE_SIMILARITY")

    negative_penalty = NEGATIVE_SIMILARITY_WEIGHT * negative_similarity
    raw_score = popularity_component + rating_component + content - negative_penalty

    if is_cold_start:
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

    evidence.sort(key=lambda item: (
        -abs(item["contribution"]), str(item["source"]), str(item["field"]),
        item.get("seed_movie_id") or 0))
    return {
        "movie_id": movie_id,
        "title": movie.get("title") if type(movie.get("title")) is str else "",
        "score": round(raw_score, 8),
        "components": {
            "baseline": round(popularity_component, 8),
            "rating_confidence": round(rating_component, 8),
            "preference": round(content, 8),
            "negative": round(-negative_penalty, 8),
            "diversity": 0.0,
        },
        "reasons": reasons,
        "reason_codes": sorted(codes),
        "evidence": evidence[:MAX_EVIDENCE],
        "genres": list(candidate_features["genres"]),
        "diversity_penalty": 0.0,
        "selection_score": round(raw_score, 8),
        "recommendation_version": RECOMMENDATION_VERSION,
        "_raw_score": raw_score,
        "_genres": candidate_features["genres"],
    }


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
    for movie_id in sorted(movie_index):
        movie = movie_index[movie_id]
        if not _active(movie):
            continue
        behaviour = state_by_movie.get(movie_id, {})
        if behaviour.get("not_interested") is True:
            continue
        if behaviour.get("watched") is True:
            if not allow_watched or normal_filters.get("unwatched") is True:
                continue
        if not matches(_filter_safe(movie), normal_filters, behaviour):
            continue
        candidates.append((movie_id, movie, behaviour))

    seed_rows = _profile_seed_rows(profile_index, state_by_movie, normal_weights)
    if seed_rows:
        need_overview = any(
            type(row[1].get("overview")) is str and bool(row[1].get("overview").strip())
            for row in seed_rows)
        need_directors = any(_id_set(row[1].get("director_ids")) for row in seed_rows)
        need_actors = any(_id_set(row[1].get("actor_ids")) for row in seed_rows)
        candidate_features = {
            movie_id: _features(
                movie, overview=need_overview, directors=need_directors,
                actors=need_actors)
            for movie_id, movie, _ in candidates
        }
    else:
        candidate_features = {
            movie_id: _features(movie, similarity=False)
            for movie_id, movie, _ in candidates
        }
    positive, negative = _seed_profiles(seed_rows, candidate_features, movie_index)

    scored = [_score_candidate(
        movie_id, movie, candidate_features[movie_id], positive, negative,
        not positive, behaviour.get("watched") is True and allow_watched)
        for movie_id, movie, behaviour in candidates]

    selected = []
    genre_counts = {}
    remaining = scored
    while remaining and len(selected) < limit:
        for item in remaining:
            genres = item["_genres"]
            repeat = sum(genre_counts.get(genre, 0) for genre in genres)
            penalty = DIVERSITY_PENALTY_PER_REPEAT * repeat
            if not genres:
                penalty += MISSING_GENRE_PENALTY
            penalty = min(MAX_DIVERSITY_PENALTY, penalty)
            item["diversity_penalty"] = round(penalty, 8)
            item["selection_score"] = round(item["_raw_score"] - penalty, 8)
            item["components"]["diversity"] = round(penalty, 8)
        best = min(remaining, key=lambda item: (-item["selection_score"], item["movie_id"]))
        remaining.remove(best)
        if not best["_genres"]:
            codes = set(best["reason_codes"])
            _append_reason(
                best["reasons"], codes,
                "类型字段缺失，应用固定多样性保守惩罚",
                "MISSING_GENRE_PENALTY")
            best["reason_codes"] = sorted(codes)
        for genre in best["_genres"]:
            genre_counts[genre] = genre_counts.get(genre, 0) + 1
        best.pop("_raw_score", None)
        best.pop("_genres", None)
        selected.append(best)

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
