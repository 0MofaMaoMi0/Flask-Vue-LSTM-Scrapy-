"""Small in-memory fixtures for the offline evaluator's unit tests."""
from __future__ import annotations


def movie(movie_id, **overrides):
    data = {
        "movie_id": movie_id,
        "title": "SYNTHETIC %s" % movie_id,
        "genres": ["剧情"],
        "release_year": 2020,
        "runtime_minutes": 100,
        "original_language": "zh",
        "catalog_status": "active",
        "catalog_popularity": 0.5,
        "demo_popularity": 0.5,
    }
    data.update(overrides)
    return data


def snapshot_doc(movies=None, **overrides):
    data = {
        "schema_version": "m10-eval-v1",
        "dataset_id": "SYNTHETIC-UNIT",
        "source": "SYNTHETIC_FIXTURE",
        "frozen_at": "2026-11-15T00:00:00+08:00",
        "movies": list(movies if movies is not None else [movie(1), movie(2)]),
    }
    data.update(overrides)
    return data


def state_doc(states=None, *, as_of="2026-11-15T00:00:00+08:00", raw=False):
    if raw:
        return dict(states or {})
    data = {
        "schema_version": "m10-eval-v1",
        "state_as_of": as_of,
        "states": dict(states or {}),
    }
    return data


def scenario(scenario_id="S1", *, category="genre", split="test",
             relevant=None, source="HUMAN", reliable=True,
             hard_conditions=None, state=None, query="SYNTHETIC query",
             allow_external=False, expectation=None, label_window=None,
             state_as_of=None, soft_preferences=None):
    labels = {
        "relevant_movie_ids": list(relevant or []),
        "label_source": source,
        "reliable": reliable,
    }
    if label_window is not None:
        labels["label_window"] = label_window
    data = {
        "scenario_id": scenario_id,
        "category": category,
        "split": split,
        "query": query,
        "hard_conditions": dict(hard_conditions or {}),
        "soft_preferences": dict(soft_preferences or {}),
        "labels": labels,
    }
    if state is not None:
        data["state"] = dict(state)
    if state_as_of is not None:
        data["state_as_of"] = state_as_of
    if allow_external:
        data["allow_external"] = True
    if expectation is not None:
        data["expectation"] = expectation
    return data


def scenarios_doc(items):
    return {
        "schema_version": "m10-eval-v1",
        "state_as_of": "2026-11-15T00:00:00+08:00",
        "scenarios": list(items),
    }