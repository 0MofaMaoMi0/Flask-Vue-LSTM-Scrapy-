"""Strict, dependency-free input contracts for the offline evaluator.

The evaluator accepts caller-supplied movie snapshots, one or more user
states, scenarios and relevance labels. It never reads a database. All
invalid-input rules below are explicit so that a synthetic fixture cannot be
mistaken for a real catalog or a real user judgement.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "m10-eval-v1"
REQUIRED_CATEGORIES = (
    "cold_start",
    "genre",
    "multi_genre",
    "watched",
    "not_interested",
    "multi_hard_conditions",
    "soft_preference",
    "niche",
    "no_results",
    "out_of_catalog",
)
SPLITS = ("train", "dev", "test")
LABEL_SOURCES = ("HUMAN", "HISTORICAL_HELDOUT", "SYNTHETIC", "NONE")
RELIABLE_LABEL_SOURCES = ("HUMAN", "HISTORICAL_HELDOUT")
CATALOG_STATUSES = ("active", "hidden", "inactive")

FILTER_KEYS = {
    "genre",
    "genres_any",
    "genres_all",
    "excluded_genres",
    "year_from",
    "year_to",
    "years_any",
    "year_ranges_any",
    "excluded_year_ranges",
    "runtime_max",
    "language",
    "unwatched",
}
STATE_KEYS = {"rating", "favorite", "want", "watched", "not_interested"}


class SchemaError(ValueError):
    """An input violates the documented M10 evaluation contract."""


@dataclass(frozen=True)
class Snapshot:
    dataset_id: str
    source: str
    frozen_at: str
    movies: tuple[dict, ...]
    by_id: dict[int, dict]
    active_ids: tuple[int, ...]
    duplicate_ids: tuple[int, ...]
    invalid_entries: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class State:
    states: dict[str, dict]
    as_of: str | None
    unknown_ids: tuple[int, ...]
    warnings: tuple[str, ...]


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _text(value: Any, label: str, maximum: int = 500) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise SchemaError("%s must be non-empty text <= %d chars" % (label, maximum))
    return value.strip()


def _int(value: Any, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise SchemaError("%s must be an int in [%d, %d]" % (label, low, high))
    return value


def _number(value: Any, label: str, low: float = 0.0, high: float | None = None) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < low:
        raise SchemaError("%s must be a finite number >= %s" % (label, low))
    if high is not None and value > high:
        raise SchemaError("%s must be <= %s" % (label, high))
    return float(value)


def aware_time(value: Any, label: str) -> str:
    if type(value) is not str:
        raise SchemaError("%s must be an ISO-8601 string with timezone" % label)
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SchemaError("%s must be a valid ISO-8601 timestamp" % label) from None
    if stamp.tzinfo is None:
        raise SchemaError("%s must include a timezone" % label)
    return stamp.isoformat()


def _movie(value: Any, index: int) -> dict:
    if type(value) is not dict:
        raise SchemaError("movies[%d] must be an object" % index)
    movie = dict(value)
    movie["movie_id"] = _int(value.get("movie_id"), "movies[%d].movie_id" % index, 1, 2 ** 62)
    movie["title"] = _text(value.get("title"), "movies[%d].title" % index, 300)
    status = value.get("catalog_status", "active")
    if status not in CATALOG_STATUSES:
        raise SchemaError("movies[%d].catalog_status is invalid" % index)
    movie["catalog_status"] = status
    adult = value.get("is_adult", False)
    if type(adult) is not bool:
        raise SchemaError("movies[%d].is_adult must be boolean" % index)
    movie["is_adult"] = adult
    genres = value.get("genres", [])
    if type(genres) is not list:
        raise SchemaError("movies[%d].genres must be a list" % index)
    seen = set()
    clean_genres = []
    for genre in genres:
        if type(genre) is not str or not 1 <= len(genre.strip()) <= 40:
            raise SchemaError("movies[%d].genres contains invalid text" % index)
        genre = genre.strip()
        if genre not in seen:
            seen.add(genre)
            clean_genres.append(genre)
    movie["genres"] = clean_genres
    year = value.get("release_year")
    if year is not None:
        movie["release_year"] = _int(year, "movies[%d].release_year" % index, 1880, 2100)
    runtime = value.get("runtime_minutes")
    if runtime is not None:
        movie["runtime_minutes"] = _int(runtime, "movies[%d].runtime_minutes" % index, 1, 600)
    language = value.get("original_language")
    if language is not None and (type(language) is not str or not 1 <= len(language) <= 20):
        raise SchemaError("movies[%d].original_language is invalid" % index)
    for field in ("catalog_popularity", "demo_popularity"):
        if value.get(field) is not None:
            movie[field] = _number(value[field], "movies[%d].%s" % (index, field))
    return movie


def validate_snapshot(doc: Any, *, strict: bool = True) -> Snapshot:
    """Validate an external movie snapshot.

    Duplicate movie_id: rejected in strict mode; the first row is kept in
    lenient mode and the duplicate id is recorded. Invalid rows are rejected
    in strict mode and skipped in lenient mode. A movie with
    catalog_status != 'active' or is_adult=True remains visible in the
    snapshot but is never a recommendation candidate.
    """
    if type(doc) is not dict:
        raise SchemaError("snapshot must be an object")
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise SchemaError("snapshot.schema_version must be %s" % SCHEMA_VERSION)
    dataset_id = _text(doc.get("dataset_id"), "snapshot.dataset_id", 200)
    source = _text(doc.get("source"), "snapshot.source", 100)
    frozen_at = aware_time(doc.get("frozen_at"), "snapshot.frozen_at")
    rows = doc.get("movies")
    if type(rows) is not list:
        raise SchemaError("snapshot.movies must be a list")

    movies, by_id = [], {}
    duplicates, invalid, warnings = [], [], []
    for index, raw in enumerate(rows):
        try:
            movie = _movie(raw, index)
        except SchemaError as exc:
            if strict:
                raise
            invalid.append(str(exc))
            continue
        mid = movie["movie_id"]
        if mid in by_id:
            if strict:
                raise SchemaError("duplicate movie_id in snapshot: %s" % mid)
            duplicates.append(mid)
            warnings.append("duplicate movie_id %s kept first row" % mid)
            continue
        by_id[mid] = movie
        movies.append(movie)
    active_ids = tuple(
        movie["movie_id"] for movie in movies
        if movie["catalog_status"] == "active" and not movie["is_adult"]
    )
    return Snapshot(
        dataset_id=dataset_id,
        source=source,
        frozen_at=frozen_at,
        movies=tuple(movies),
        by_id=by_id,
        active_ids=active_ids,
        duplicate_ids=tuple(duplicates),
        invalid_entries=tuple(invalid),
        warnings=tuple(warnings),
    )


def validate_state(doc: Any, snapshot: Snapshot, *, strict: bool = True,
                   default_as_of: str | None = None) -> State:
    """Validate one user's current state.

    Unknown movie ids are rejected in strict mode and dropped in lenient mode.
    State is never treated as a relevance label: watched/click/impression do
    not become "liked" anywhere in this package.
    """
    if type(doc) is not dict:
        raise SchemaError("state must be an object")
    if "states" in doc:
        raw_states = doc.get("states")
        as_of_raw = doc.get("state_as_of", default_as_of)
    else:
        raw_states = doc
        as_of_raw = default_as_of
    if type(raw_states) is not dict:
        raise SchemaError("state.states must be an object")
    as_of = aware_time(as_of_raw, "state.state_as_of") if as_of_raw is not None else None
    states, unknown, warnings = {}, [], []
    for raw_id, raw_value in raw_states.items():
        try:
            if type(raw_id) is bool:
                raise SchemaError("state key must be a positive integer id")
            if type(raw_id) is int:
                mid = raw_id
            elif type(raw_id) is str and raw_id.isdigit():
                mid = int(raw_id)
            else:
                raise SchemaError("state key must be a positive integer id")
            if mid < 1:
                raise SchemaError("state key must be a positive integer id")
        except SchemaError as exc:
            if strict:
                raise
            invalid_note = "state key %r skipped: %s" % (raw_id, exc)
            warnings.append(invalid_note)
            continue
        if mid not in snapshot.by_id:
            if strict:
                raise SchemaError("state references unknown movie_id %s" % mid)
            unknown.append(mid)
            warnings.append("state unknown movie_id %s dropped" % mid)
            continue
        if type(raw_value) is not dict:
            raise SchemaError("state[%s] must be an object" % mid)
        extra = set(raw_value) - STATE_KEYS
        if extra:
            raise SchemaError("state[%s] has unknown fields: %s" % (mid, sorted(extra)))
        value = {}
        for key in ("favorite", "want", "watched", "not_interested"):
            if key in raw_value:
                if type(raw_value[key]) is not bool:
                    raise SchemaError("state[%s].%s must be boolean" % (mid, key))
                value[key] = raw_value[key]
        if "rating" in raw_value:
            rating = raw_value["rating"]
            if (type(rating) not in (int, float) or not math.isfinite(rating)
                    or not 0.5 <= rating <= 5 or rating * 2 != int(rating * 2)):
                raise SchemaError("state[%s].rating must be 0.5..5 in 0.5 steps" % mid)
            value["rating"] = float(rating)
        states[str(mid)] = value
    return State(states=states, as_of=as_of, unknown_ids=tuple(unknown),
                 warnings=tuple(warnings))


def _string_list(value: Any, label: str, maximum_items: int = 32) -> list[str]:
    if type(value) is not list or len(value) > maximum_items:
        raise SchemaError("%s must be a list with <= %d items" % (label, maximum_items))
    result = []
    for item in value:
        if type(item) is not str or not 1 <= len(item.strip()) <= 40:
            raise SchemaError("%s contains invalid text" % label)
        item = item.strip()
        if item not in result:
            result.append(item)
    return result


def validate_hard_conditions(value: Any) -> dict:
    """Validate the same hard-filter shapes used by assistant.conditions."""
    if value is None:
        return {}
    if type(value) is not dict:
        raise SchemaError("hard_conditions must be an object")
    extra = set(value) - FILTER_KEYS
    if extra:
        raise SchemaError("hard_conditions has unknown keys: %s" % sorted(extra))
    filters = dict(value)
    for key in ("year_from", "year_to"):
        if key in filters:
            filters[key] = _int(filters[key], key, 1880, 2100)
    if "years_any" in filters:
        years = filters["years_any"]
        if type(years) is not list or not 1 <= len(years) <= 15:
            raise SchemaError("years_any must be a non-empty list <= 15")
        filters["years_any"] = [_int(y, "years_any[]", 1880, 2100) for y in years]
    for key in ("excluded_year_ranges", "year_ranges_any"):
        if key in filters:
            ranges = filters[key]
            if type(ranges) is not list or len(ranges) > 15:
                raise SchemaError("%s must be a list <= 15" % key)
            clean = []
            for pair in ranges:
                if type(pair) is not list or len(pair) != 2:
                    raise SchemaError("%s entries must be [from, to]" % key)
                lo = _int(pair[0], "%s[].from" % key, 1880, 2100)
                hi = _int(pair[1], "%s[].to" % key, 1880, 2100)
                if lo > hi:
                    raise SchemaError("%s has a reversed range" % key)
                clean.append([lo, hi])
            filters[key] = clean
    if "runtime_max" in filters:
        filters["runtime_max"] = _int(filters["runtime_max"], "runtime_max", 1, 600)
    for key in ("genre", "language"):
        if key in filters:
            filters[key] = _text(filters[key], key, 40)
    if "unwatched" in filters and type(filters["unwatched"]) is not bool:
        raise SchemaError("unwatched must be boolean")
    for key in ("genres_any", "genres_all", "excluded_genres"):
        if key in filters:
            filters[key] = _string_list(filters[key], key)
    if sum(bool(filters.get(key)) for key in ("genre", "genres_any", "genres_all")) > 1:
        raise SchemaError("use only one positive genre mode")
    if filters.get("year_from", 1880) > filters.get("year_to", 2100):
        raise SchemaError("contradictory year range")
    return filters


def genre_groups(filters: dict) -> list[list[str]]:
    if filters.get("genre"):
        return [[filters["genre"]]]
    if filters.get("genres_all"):
        return [[genre] for genre in filters["genres_all"]]
    if filters.get("genres_any"):
        return [list(filters["genres_any"])]
    return []


def condition_violations(movie: dict, state: dict, filters: dict) -> list[str]:
    """Return explicit hard-condition violation codes for one movie.

    Genre matching keeps assistant.conditions' substring semantics
    (for example "剧情" matches a stored label "剧情片").
    """
    problems = []
    year = movie.get("release_year")
    if filters.get("year_from") is not None and (year is None or year < filters["year_from"]):
        problems.append("YEAR_BELOW_MIN")
    if filters.get("year_to") is not None and (year is None or year > filters["year_to"]):
        problems.append("YEAR_ABOVE_MAX")
    if filters.get("years_any") is not None and year not in filters["years_any"]:
        problems.append("YEAR_NOT_ALLOWED")
    if filters.get("year_ranges_any") and (
            year is None or not any(lo <= year <= hi for lo, hi in filters["year_ranges_any"])):
        problems.append("YEAR_NOT_IN_RANGE")
    if filters.get("excluded_year_ranges") and (
            year is None or any(lo <= year <= hi for lo, hi in filters["excluded_year_ranges"])):
        problems.append("YEAR_EXCLUDED")
    runtime = movie.get("runtime_minutes")
    if filters.get("runtime_max") is not None and (runtime is None or runtime > filters["runtime_max"]):
        problems.append("RUNTIME_ABOVE_MAX")
    if filters.get("language") and movie.get("original_language") != filters["language"]:
        problems.append("LANGUAGE_MISMATCH")
    genres = movie.get("genres", [])
    for group in genre_groups(filters):
        if not any(any(needle in label for label in genres) for needle in group):
            problems.append("GENRE_REQUIRED_MISSING")
            break
    for excluded in filters.get("excluded_genres", []):
        if any(excluded in label for label in genres):
            problems.append("GENRE_EXCLUDED")
            break
    if filters.get("unwatched") and state.get("watched"):
        problems.append("WATCHED_BUT_UNWATCHED_REQUIRED")
    return sorted(set(problems))


def _labels(value: Any, snapshot: Snapshot, *, strict: bool) -> dict:
    if type(value) is not dict:
        raise SchemaError("labels must be an object")
    allowed = {"relevant_movie_ids", "label_source", "reliable", "label_window",
               "note", "evidence"}
    extra = set(value) - allowed
    if extra:
        raise SchemaError("labels has unknown fields: %s" % sorted(extra))
    source = value.get("label_source")
    if source not in LABEL_SOURCES:
        raise SchemaError("labels.label_source is invalid")
    relevant = value.get("relevant_movie_ids")
    if type(relevant) is not list:
        raise SchemaError("labels.relevant_movie_ids must be a list")
    reliable = value.get("reliable", False)
    if type(reliable) is not bool:
        raise SchemaError("labels.reliable must be boolean")
    if source == "SYNTHETIC" and reliable:
        raise SchemaError("SYNTHETIC labels cannot be marked reliable")
    if source == "NONE" and relevant:
        raise SchemaError("NONE labels must have an empty relevant_movie_ids list")
    clean, duplicates, invalid = [], [], []
    for item in relevant:
        if type(item) is not int or item < 1:
            if strict:
                raise SchemaError("labels.relevant_movie_ids must contain positive ints")
            invalid.append(item)
            continue
        if item in clean:
            duplicates.append(item)
            continue
        if item not in snapshot.by_id:
            if strict:
                raise SchemaError("label references unknown movie_id %s" % item)
            invalid.append(item)
            continue
        clean.append(item)
    window = value.get("label_window")
    if window is not None:
        if type(window) is not dict or set(window) != {"from", "to"}:
            raise SchemaError("labels.label_window must contain exactly from/to")
        window = {
            "from": aware_time(window["from"], "labels.label_window.from"),
            "to": aware_time(window["to"], "labels.label_window.to"),
        }
        if window["from"] > window["to"]:
            raise SchemaError("labels.label_window.from must be <= to")
    note = value.get("note")
    if note is not None and (type(note) is not str or len(note) > 500):
        raise SchemaError("labels.note must be text <= 500 chars")
    evidence = value.get("evidence")
    if evidence is not None and (type(evidence) is not str or len(evidence) > 500):
        raise SchemaError("labels.evidence must be text <= 500 chars")
    return {
        "relevant_movie_ids": clean,
        "label_source": source,
        "reliable": reliable,
        "label_window": window,
        "note": note,
        "evidence": evidence,
        "duplicate_ids": duplicates,
        "invalid_movie_ids": invalid,
    }


def validate_scenarios(doc: Any, snapshot: Snapshot, *, strict: bool = True,
                       require_categories: bool = False,
                       require_splits: bool = False) -> list[dict]:
    """Validate scenarios and their train/dev/test labels.

    For HISTORICAL_HELDOUT, state_as_of must be strictly earlier than the
    label window start, so current state cannot masquerade as future truth.
    """
    if type(doc) is not dict:
        raise SchemaError("scenarios document must be an object")
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise SchemaError("scenarios.schema_version must be %s" % SCHEMA_VERSION)
    rows = doc.get("scenarios")
    if type(rows) is not list:
        raise SchemaError("scenarios.scenarios must be a list")
    seen_ids = set()
    scenarios = []
    for index, raw in enumerate(rows):
        if type(raw) is not dict:
            raise SchemaError("scenarios[%d] must be an object" % index)
        scenario = dict(raw)
        sid = _text(raw.get("scenario_id"), "scenarios[%d].scenario_id" % index, 100)
        if sid in seen_ids:
            raise SchemaError("duplicate scenario_id %s" % sid)
        seen_ids.add(sid)
        scenario["scenario_id"] = sid
        category = raw.get("category")
        if category not in REQUIRED_CATEGORIES:
            raise SchemaError("scenarios[%d].category is invalid" % index)
        scenario["category"] = category
        split = raw.get("split")
        if split not in SPLITS:
            raise SchemaError("scenarios[%d].split is invalid" % index)
        scenario["split"] = split
        scenario["hard_conditions"] = validate_hard_conditions(raw.get("hard_conditions"))
        soft = raw.get("soft_preferences", {})
        if type(soft) is not dict:
            raise SchemaError("scenarios[%d].soft_preferences must be an object" % index)
        scenario["soft_preferences"] = dict(soft)
        if category == "soft_preference" and not soft:
            raise SchemaError("soft_preference scenario needs soft_preferences")
        allow_external = raw.get("allow_external", False)
        if type(allow_external) is not bool:
            raise SchemaError("scenarios[%d].allow_external must be boolean" % index)
        scenario["allow_external"] = allow_external
        expectation = raw.get("expectation")
        if expectation is not None and expectation not in (
                "RESULTS", "NO_RESULTS", "EXTERNAL_DISCOVERY"):
            raise SchemaError("scenarios[%d].expectation is invalid" % index)
        scenario["expectation"] = expectation
        state_as_of = raw.get("state_as_of", doc.get("state_as_of"))
        scenario["state_as_of_raw"] = state_as_of
        if "state" in raw:
            scenario["state"] = raw["state"]
        labels = _labels(raw.get("labels"), snapshot, strict=strict)
        if labels["duplicate_ids"]:
            scenario.setdefault("warnings", []).append(
                "duplicate label ids deduplicated: %s" % labels["duplicate_ids"])
        if labels["invalid_movie_ids"]:
            scenario.setdefault("warnings", []).append(
                "invalid label ids excluded: %s" % labels["invalid_movie_ids"])
        scenario["labels"] = labels
        if labels["label_source"] == "HISTORICAL_HELDOUT":
            if labels["label_window"] is None:
                raise SchemaError("HISTORICAL_HELDOUT requires label_window")
            if state_as_of is None:
                raise SchemaError("HISTORICAL_HELDOUT requires state_as_of")
            as_of = datetime.fromisoformat(
                aware_time(state_as_of, "scenario.state_as_of"))
            window_from = datetime.fromisoformat(labels["label_window"]["from"])
            if not window_from > as_of:
                raise SchemaError(
                    "HISTORICAL_HELDOUT label window must start after state_as_of")
        scenarios.append(scenario)
    categories = {item["category"] for item in scenarios}
    if require_categories:
        missing = sorted(set(REQUIRED_CATEGORIES) - categories)
        if missing:
            raise SchemaError("scenario coverage missing categories: %s" % missing)
    splits = {item["split"] for item in scenarios}
    if require_splits:
        missing = sorted(set(SPLITS) - splits)
        if missing:
            raise SchemaError("scenario coverage missing splits: %s" % missing)
    return scenarios


def validate_all(snapshot_doc: Any, state_doc: Any, scenarios_doc: Any,
                 *, strict: bool = True, require_categories: bool = False,
                 require_splits: bool = False) -> tuple[Snapshot, State, list[dict]]:
    snapshot = validate_snapshot(snapshot_doc, strict=strict)
    state = validate_state(state_doc, snapshot, strict=strict)
    scenarios = validate_scenarios(
        scenarios_doc, snapshot, strict=strict,
        require_categories=require_categories, require_splits=require_splits)
    return snapshot, state, scenarios


__all__ = [
    "SCHEMA_VERSION",
    "REQUIRED_CATEGORIES",
    "SPLITS",
    "LABEL_SOURCES",
    "RELIABLE_LABEL_SOURCES",
    "SchemaError",
    "Snapshot",
    "State",
    "load_json",
    "aware_time",
    "validate_snapshot",
    "validate_state",
    "validate_hard_conditions",
    "condition_violations",
    "validate_scenarios",
    "validate_all",
]