"""Offline evaluator: adapter in, per-scenario evidence and metrics out."""
from __future__ import annotations

import copy
import math
import time
import tracemalloc
from dataclasses import asdict, dataclass

from . import METRIC_VERSION
from .metrics import (
    catalog_coverage,
    genre_coverage,
    intra_list_genre_diversity,
    mean_or_none,
    ndcg_at_k,
    rate_or_none,
    recall_at_k,
    unique_ids,
)
from .schema import (
    RELIABLE_LABEL_SOURCES,
    SchemaError,
    Snapshot,
    State,
    condition_violations,
    validate_scenarios,
    validate_snapshot,
    validate_state,
)

METRIC_DEFINITIONS = {
    "recall_at_10": (
        "Macro average over label-eligible test scenarios: |top10 ∩ relevant| / "
        "|all labelled relevant|. Relevant-set denominator includes movies never "
        "returned; empty label sets are null, not zero."
    ),
    "ndcg_at_10": (
        "Macro average of binary NDCG@10; DCG=sum(rel/log2(rank+1)); IDCG uses "
        "min(10, |relevant|) ideal positions; empty label sets are null."
    ),
    "catalog_coverage": (
        "Unique valid internal movie ids in top-10 across all OK scenarios / "
        "unique active non-adult catalog ids. External/unknown ids are excluded."
    ),
    "genre_coverage": (
        "Unique genre labels in top-10 recommendations / unique genre labels in "
        "the active catalog; empty catalog genre set is null."
    ),
    "intra_list_genre_diversity_at_10": (
        "Mean of per-scenario pairwise 1-Jaccard genre distance. Scenarios with "
        "fewer than two genre-bearing items are excluded; none left -> null."
    ),
    "hard_condition_violation_rate": (
        "Violating top-10 positions / top-10 positions in scenarios that declare "
        "hard conditions. Scenarios without hard conditions are not in the "
        "denominator; zero returned positions -> null."
    ),
    "negative_feedback_recommendation_rate": (
        "Top-10 positions whose movie is watched or not_interested / all top-10 "
        "positions across OK scenarios; zero positions -> null."
    ),
    "invalid_id_rate": (
        "Returned entries with unknown/non-active/missing ids / all returned "
        "entries (before top-k truncation). External entries are excluded and "
        "reported separately."
    ),
    "duplicate_output_rate": (
        "Repeated valid ids after their first occurrence / all returned entries."
    ),
}


class EvaluationError(ValueError):
    pass


class AdapterOutputError(EvaluationError):
    pass


@dataclass(frozen=True)
class EvalConfig:
    k: int = 10
    metric_mode: str = "effectiveness"  # or synthetic_engine_check
    collect_resources: bool = True
    fail_fast: bool = False
    metric_version: str = METRIC_VERSION

    def __post_init__(self):
        if type(self.k) is not int or not 1 <= self.k <= 50:
            raise ValueError("k must be 1..50")
        if self.metric_mode not in ("effectiveness", "synthetic_engine_check"):
            raise ValueError("metric_mode must be effectiveness or synthetic_engine_check")


def _json_safe(value, depth=0):
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None
    if depth >= 5:
        return repr(value)
    if type(value) is list:
        return [_json_safe(item, depth + 1) for item in value]
    if type(value) is tuple:
        return [_json_safe(item, depth + 1) for item in value]
    if type(value) is dict:
        return {str(key): _json_safe(item, depth + 1) for key, item in value.items()}
    return repr(value)


def _score(value, raw_rank):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value):
        raise AdapterOutputError("item %d has a non-finite score" % raw_rank)
    return float(value)


def _normalise_output(raw, scenario, snapshot, config):
    if type(raw) is list:
        payload = {"items": raw}
    elif type(raw) is dict:
        payload = dict(raw)
    else:
        raise AdapterOutputError("adapter must return a list or an object with items")
    raw_items = payload.get("items")
    if type(raw_items) is not list:
        raise AdapterOutputError("adapter result.items must be a list")
    candidate_count = payload.get("candidate_count")
    if candidate_count is not None and (type(candidate_count) is not int or candidate_count < 0):
        raise AdapterOutputError("candidate_count must be a non-negative int")
    paid = payload.get("paid_model_tokens", 0)
    if type(paid) is not int or paid < 0:
        raise AdapterOutputError("paid_model_tokens must be a non-negative int")
    metadata = payload.get("metadata", {})
    if metadata is None:
        metadata = {}
    if type(metadata) is not dict:
        raise AdapterOutputError("metadata must be an object")

    entries = []
    canonical = []
    seen = set()
    for raw_rank, value in enumerate(raw_items, 1):
        entry = {
            "raw_rank": raw_rank,
            "movie_id": None,
            "external_id": None,
            "title": None,
            "score": None,
            "components": {},
            "reasons": [],
            "valid": False,
            "external": False,
            "external_allowed": False,
            "duplicate": False,
            "invalid_reason": None,
        }
        if type(value) is int:
            movie_id = value
        elif type(value) is dict:
            movie_id = value.get("movie_id")
            external_id = value.get("external_id")
            entry["external_id"] = external_id if type(external_id) is str and external_id else None
            entry["title"] = value.get("title") if type(value.get("title")) is str else None
            entry["score"] = _score(value.get("score"), raw_rank)
            components = value.get("components", {})
            if components is not None and type(components) is not dict:
                raise AdapterOutputError("item %d components must be an object" % raw_rank)
            entry["components"] = _json_safe(components or {})
            reasons = value.get("reasons", [])
            if reasons is None:
                reasons = []
            if type(reasons) is not list or any(type(item) is not str for item in reasons):
                raise AdapterOutputError("item %d reasons must be a list of strings" % raw_rank)
            entry["reasons"] = list(reasons)
            if movie_id is None:
                if entry["external_id"]:
                    entry["external"] = True
                    entry["external_allowed"] = bool(scenario.get("allow_external"))
                    if not entry["external_allowed"]:
                        entry["invalid_reason"] = "EXTERNAL_NOT_ALLOWED"
                else:
                    entry["invalid_reason"] = "MISSING_ID"
                entries.append(entry)
                continue
        else:
            entry["invalid_reason"] = "NON_OBJECT_ENTRY"
            entries.append(entry)
            continue

        if type(movie_id) is not int or movie_id < 1:
            entry["invalid_reason"] = "INVALID_ID_TYPE"
            entries.append(entry)
            continue
        entry["movie_id"] = movie_id
        movie = snapshot.by_id.get(movie_id)
        if movie is None:
            entry["invalid_reason"] = "UNKNOWN_ID"
            entries.append(entry)
            continue
        if movie.get("catalog_status") != "active" or movie.get("is_adult") is True:
            entry["invalid_reason"] = "NOT_ACTIVE"
            entries.append(entry)
            continue
        if movie_id in seen:
            entry["duplicate"] = True
            entries.append(entry)
            continue
        seen.add(movie_id)
        entry["valid"] = True
        entry["title"] = entry["title"] or movie.get("title")
        entries.append(entry)
        canonical.append(entry)

    top = canonical[:config.k]
    for rank, entry in enumerate(top, 1):
        entry["rank"] = rank
    dropped = [entry["movie_id"] for entry in canonical[config.k:]]
    external = [entry for entry in entries if entry["external"]]
    invalid = [entry for entry in entries if entry["invalid_reason"] and not entry["external"]]
    duplicates = [entry for entry in entries if entry["duplicate"]]
    return {
        "items": top,
        "entries": entries,
        "canonical_ids": [entry["movie_id"] for entry in canonical],
        "ranked_ids": [entry["movie_id"] for entry in top],
        "dropped_after_k": dropped,
        "external_items": external,
        "invalid_items": invalid,
        "duplicate_items": duplicates,
        "returned_total": len(raw_items),
        "valid_total": len(canonical),
        "duplicate_count": len(duplicates),
        "invalid_count": len(invalid),
        "external_count": len(external),
        "external_disallowed_count": sum(
            1 for entry in external if not entry["external_allowed"]),
        "truncated": len(canonical) > config.k,
        "short_list": len(canonical) < config.k,
        "candidate_count": candidate_count,
        "paid_model_tokens": paid,
        "adapter_metadata": _json_safe(metadata),
    }


def _label_status(scenario, config):
    labels = scenario["labels"]
    if labels.get("invalid_movie_ids"):
        return False, "INVALID_LABEL_IDS"
    if not labels["relevant_movie_ids"]:
        return False, "EMPTY_LABEL_SET"
    if scenario["split"] != "test":
        return False, "NOT_TEST_SPLIT"
    source = labels["label_source"]
    reliable = labels["reliable"]
    if config.metric_mode == "effectiveness":
        if source not in RELIABLE_LABEL_SOURCES:
            return False, "UNRELIABLE_LABEL_SOURCE"
        if not reliable:
            return False, "LABELS_NOT_MARKED_RELIABLE"
        return True, "REAL_LABEL_EFFECTIVENESS"
    if source == "SYNTHETIC":
        return True, "SYNTHETIC_ENGINE_CHECK_ONLY"
    if source in RELIABLE_LABEL_SOURCES and reliable:
        return True, "REAL_LABEL_EFFECTIVENESS"
    return False, "UNRELIABLE_LABEL_SOURCE"


def _scenario_metrics(normalised, scenario, snapshot, scenario_state, config):
    top = normalised["items"]
    relevant = scenario["labels"]["relevant_movie_ids"]
    eligible, claim = _label_status(scenario, config)
    recall = recall_at_k(normalised["ranked_ids"], relevant, config.k) if eligible else None
    ndcg = ndcg_at_k(normalised["ranked_ids"], relevant, config.k) if eligible else None

    conditions = scenario["hard_conditions"]
    hard_positions = 0
    hard_violations = []
    negative_positions = 0
    negative_ids = []
    item_details = []
    for entry in top:
        movie = snapshot.by_id[entry["movie_id"]]
        behaviour = scenario_state.get(str(entry["movie_id"]), {})
        violations = condition_violations(movie, behaviour, conditions) if conditions else []
        watched = bool(behaviour.get("watched"))
        negative = watched or bool(behaviour.get("not_interested"))
        if conditions:
            hard_positions += 1
            if violations:
                hard_violations.append({"movie_id": entry["movie_id"], "codes": violations})
        if negative:
            negative_positions += 1
            negative_ids.append(entry["movie_id"])
        item_details.append({
            "rank": entry["rank"],
            "movie_id": entry["movie_id"],
            "title": entry.get("title"),
            "score": entry.get("score"),
            "components": entry.get("components"),
            "reasons": entry.get("reasons"),
            "hard_conditions": violations,
            "watched": watched,
            "not_interested": bool(behaviour.get("not_interested")),
        })

    diversity = intra_list_genre_diversity(
        snapshot.by_id[entry["movie_id"]].get("genres", []) for entry in top)
    hard_rate = rate_or_none(len(hard_violations), hard_positions) if conditions else None
    negative_rate = rate_or_none(negative_positions, len(top)) if top else None
    return {
        "label_eligible": eligible,
        "claim_scope": claim if eligible else None,
        "label_reason": None if eligible else claim,
        "recall_at_10": recall,
        "ndcg_at_10": ndcg,
        "intra_list_genre_diversity_at_10": diversity,
        "hard_condition_violation_rate": hard_rate,
        "hard_condition_violations": hard_violations,
        "hard_condition_positions": hard_positions,
        "negative_feedback_recommendation_rate": negative_rate,
        "negative_feedback_ids": negative_ids,
        "items": item_details,
    }


def _run_adapter(adapter, movies, state, scenario, config):
    elapsed_ms = cpu_ms = peak = None
    if config.collect_resources:
        tracemalloc.start()
        wall_start = time.perf_counter()
        cpu_start = time.process_time()
    try:
        output = adapter.rank(movies, state, scenario)
    finally:
        if config.collect_resources:
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            elapsed_ms = (time.perf_counter() - wall_start) * 1000
            cpu_ms = (time.process_time() - cpu_start) * 1000
    return output, {
        "elapsed_ms": round(elapsed_ms, 3) if elapsed_ms is not None else None,
        "cpu_ms": round(cpu_ms, 3) if cpu_ms is not None else None,
        "peak_traced_bytes": peak,
    }


def evaluate(snapshot, state, scenarios, adapter, config=None):
    """Run one adapter over validated inputs and return JSON-serialisable evidence."""
    config = config or EvalConfig()
    if not isinstance(snapshot, Snapshot):
        snapshot = validate_snapshot(snapshot)
    if not isinstance(state, State):
        state = validate_state(state, snapshot)
    if not isinstance(scenarios, list):
        scenarios = validate_scenarios(scenarios, snapshot)

    results = []
    for scenario in scenarios:
        if "state" in scenario:
            try:
                scenario_state = validate_state(
                    {"states": scenario["state"], "state_as_of": scenario.get("state_as_of_raw")},
                    snapshot,
                    default_as_of=state.as_of,
                ).states
            except SchemaError as exc:
                if config.fail_fast:
                    raise EvaluationError("%s state: %s" % (scenario["scenario_id"], exc)) from None
                results.append({
                    "scenario_id": scenario["scenario_id"],
                    "category": scenario["category"],
                    "split": scenario["split"],
                    "status": "ERROR",
                    "error": "INPUT_STATE_INVALID: %s" % exc,
                    "labels": {
                        "source": scenario["labels"]["label_source"],
                        "reliable": scenario["labels"]["reliable"],
                        "relevant_count": len(scenario["labels"]["relevant_movie_ids"]),
                        "used_for_metrics": False,
                        "reason": "SCENARIO_STATE_INVALID",
                    },
                    "metrics": {},
                    "resources": {},
                })
                continue
        else:
            scenario_state = state.states

        movies_copy = copy.deepcopy(list(snapshot.movies))
        state_copy = copy.deepcopy(scenario_state)
        scenario_copy = copy.deepcopy(scenario)
        try:
            output, resources = _run_adapter(
                adapter, movies_copy, state_copy, scenario_copy, config)
            normalised = _normalise_output(output, scenario, snapshot, config)
            metrics = _scenario_metrics(
                normalised, scenario, snapshot, scenario_state, config)
        except Exception as exc:
            if config.fail_fast:
                raise EvaluationError("%s: %s: %s" % (
                    scenario["scenario_id"], type(exc).__name__, exc)) from exc
            results.append({
                "scenario_id": scenario["scenario_id"],
                "category": scenario["category"],
                "split": scenario["split"],
                "status": "ERROR",
                "error": "%s: %s" % (type(exc).__name__, exc),
                "labels": {
                    "source": scenario["labels"]["label_source"],
                    "reliable": scenario["labels"]["reliable"],
                    "relevant_count": len(scenario["labels"]["relevant_movie_ids"]),
                    "used_for_metrics": False,
                    "reason": "SCENARIO_ERROR",
                },
                "metrics": {},
                "resources": {},
            })
            continue

        label_eligible = metrics["label_eligible"]
        results.append({
            "scenario_id": scenario["scenario_id"],
            "category": scenario["category"],
            "split": scenario["split"],
            "query": scenario.get("query"),
            "status": "OK",
            "expectation": scenario.get("expectation"),
            "labels": {
                "source": scenario["labels"]["label_source"],
                "reliable": scenario["labels"]["reliable"],
                "relevant_count": len(scenario["labels"]["relevant_movie_ids"]),
                "used_for_metrics": label_eligible,
                "reason": metrics["label_reason"],
                "label_window": scenario["labels"].get("label_window"),
            },
            "ranked_ids": normalised["ranked_ids"],
            "items": metrics["items"],
            "dropped_after_k": normalised["dropped_after_k"],
            "truncated": normalised["truncated"],
            "short_list": normalised["short_list"],
            "returned_total": normalised["returned_total"],
            "valid_total": normalised["valid_total"],
            "duplicate_ids": [entry["movie_id"] for entry in normalised["duplicate_items"]],
            "invalid_items": [{
                "raw_rank": entry["raw_rank"],
                "movie_id": entry.get("movie_id"),
                "reason": entry["invalid_reason"],
            } for entry in normalised["invalid_items"]],
            "external_items": [{
                "raw_rank": entry["raw_rank"],
                "external_id": entry["external_id"],
                "title": entry.get("title"),
                "allowed": entry["external_allowed"],
            } for entry in normalised["external_items"]],
            "metrics": {
                "claim_scope": metrics["claim_scope"],
                "recall_at_10": metrics["recall_at_10"],
                "ndcg_at_10": metrics["ndcg_at_10"],
                "intra_list_genre_diversity_at_10": metrics["intra_list_genre_diversity_at_10"],
                "hard_condition_violation_rate": metrics["hard_condition_violation_rate"],
                "hard_condition_violations": metrics["hard_condition_violations"],
                "hard_condition_positions": metrics["hard_condition_positions"],
                "negative_feedback_recommendation_rate": metrics["negative_feedback_recommendation_rate"],
                "negative_feedback_ids": metrics["negative_feedback_ids"],
            },
            "resources": {
                **resources,
                "candidate_count": normalised["candidate_count"],
                "paid_model_tokens": normalised["paid_model_tokens"],
                "adapter_metadata": normalised["adapter_metadata"],
            },
            "warnings": scenario.get("warnings", []),
        })

    ok = [item for item in results if item["status"] == "OK"]
    label_metrics = [item for item in ok if item["labels"]["used_for_metrics"]]
    recalls = [item["metrics"]["recall_at_10"] for item in label_metrics
               if item["metrics"]["recall_at_10"] is not None]
    ndcgs = [item["metrics"]["ndcg_at_10"] for item in label_metrics
             if item["metrics"]["ndcg_at_10"] is not None]
    diversities = [item["metrics"]["intra_list_genre_diversity_at_10"] for item in ok
                   if item["metrics"]["intra_list_genre_diversity_at_10"] is not None]

    recommended_ids = unique_ids(
        movie_id for item in ok for movie_id in item["ranked_ids"])
    recommended_genres = [
        genre for item in ok for movie_id in item["ranked_ids"]
        for genre in snapshot.by_id[movie_id].get("genres", [])
    ]
    catalog_genres = [
        genre for movie_id in snapshot.active_ids
        for genre in snapshot.by_id[movie_id].get("genres", [])
    ]

    hard_scenarios = [item for item in ok if item["metrics"]["hard_condition_positions"] > 0]
    hard_denominator = sum(item["metrics"]["hard_condition_positions"] for item in hard_scenarios)
    hard_numerator = sum(len(item["metrics"]["hard_condition_violations"]) for item in hard_scenarios)
    negative_denominator = sum(len(item["ranked_ids"]) for item in ok)
    negative_numerator = sum(len(item["metrics"]["negative_feedback_ids"]) for item in ok)
    raw_entries = sum(item["returned_total"] for item in ok)
    invalid_entries = sum(len(item["invalid_items"]) for item in ok)
    duplicate_entries = sum(len(item["duplicate_ids"]) for item in ok)

    label_reason_counts = {}
    for item in results:
        reason = item["labels"].get("reason")
        if reason:
            label_reason_counts[reason] = label_reason_counts.get(reason, 0) + 1
    claims = {item["metrics"].get("claim_scope") if "metrics" in item else None
              for item in ok if item["labels"]["used_for_metrics"]}
    claims.discard(None)
    if not claims:
        claim_scope = None
    elif claims == {"REAL_LABEL_EFFECTIVENESS"}:
        claim_scope = "REAL_LABEL_EFFECTIVENESS"
    elif claims == {"SYNTHETIC_ENGINE_CHECK_ONLY"}:
        claim_scope = "SYNTHETIC_ENGINE_CHECK_ONLY"
    else:
        claim_scope = "MIXED_LABEL_SOURCES"

    errors = [item for item in results if item["status"] == "ERROR"]
    by_split = {split: sum(1 for item in results if item["split"] == split)
                for split in ("train", "dev", "test")}
    by_category = {}
    for item in results:
        by_category[item["category"]] = by_category.get(item["category"], 0) + 1

    metrics_doc = {
        "recall_at_10": {
            "value": mean_or_none(recalls),
            "scenarios": len(recalls),
            "relevant_labels": sum(item["labels"]["relevant_count"] for item in label_metrics),
            "aggregation": "MACRO_AVERAGE_PER_SCENARIO",
            "reason": None if recalls else "NO_LABEL_ELIGIBLE_TEST_SCENARIOS",
        },
        "ndcg_at_10": {
            "value": mean_or_none(ndcgs),
            "scenarios": len(ndcgs),
            "aggregation": "MACRO_AVERAGE_PER_SCENARIO",
            "reason": None if ndcgs else "NO_LABEL_ELIGIBLE_TEST_SCENARIOS",
        },
        "catalog_coverage": {
            "value": catalog_coverage(recommended_ids, snapshot.active_ids),
            "recommended_unique": len(set(recommended_ids) & set(snapshot.active_ids)),
            "catalog_movies": len(snapshot.active_ids),
            "scope": "ALL_OK_SCENARIOS_TOP_K",
            "reason": None if snapshot.active_ids else "EMPTY_CATALOG",
        },
        "genre_coverage": {
            "value": genre_coverage(recommended_genres, catalog_genres),
            "recommended_genres": len(set(recommended_genres) & set(catalog_genres)),
            "catalog_genres": len(set(catalog_genres)),
            "scope": "ALL_OK_SCENARIOS_TOP_K",
            "reason": None if catalog_genres else "EMPTY_CATALOG_GENRES",
        },
        "intra_list_genre_diversity_at_10": {
            "value": mean_or_none(diversities),
            "scenarios": len(diversities),
            "reason": None if diversities else "FEWER_THAN_TWO_GENRE_BEARING_ITEMS",
        },
        "hard_condition_violation_rate": {
            "value": rate_or_none(hard_numerator, hard_denominator),
            "violations": hard_numerator,
            "positions": hard_denominator,
            "scenarios_with_hard_conditions": len(hard_scenarios),
            "reason": None if hard_denominator else "NO_RETURNED_POSITIONS_WITH_HARD_CONDITIONS",
        },
        "negative_feedback_recommendation_rate": {
            "value": rate_or_none(negative_numerator, negative_denominator),
            "positions": negative_numerator,
            "returned_positions": negative_denominator,
            "reason": None if negative_denominator else "NO_RETURNED_POSITIONS",
        },
        "invalid_id_rate": {
            "value": rate_or_none(invalid_entries, raw_entries),
            "invalid_entries": invalid_entries,
            "returned_entries": raw_entries,
            "reason": None if raw_entries else "NO_RETURNED_ENTRIES",
        },
        "duplicate_output_rate": {
            "value": rate_or_none(duplicate_entries, raw_entries),
            "duplicate_entries": duplicate_entries,
            "returned_entries": raw_entries,
            "reason": None if raw_entries else "NO_RETURNED_ENTRIES",
        },
    }
    resource_values = [item["resources"] for item in ok if item["resources"]]
    latencies = [item["elapsed_ms"] for item in resource_values
                 if item.get("elapsed_ms") is not None]
    peaks = [item["peak_traced_bytes"] for item in resource_values
             if item.get("peak_traced_bytes") is not None]

    return {
        "schema_version": "m10-eval-result-v1",
        "metric_version": METRIC_VERSION,
        "status": "OK" if not errors else "PARTIAL",
        "config": asdict(config),
        "dataset": {
            "dataset_id": snapshot.dataset_id,
            "source": snapshot.source,
            "frozen_at": snapshot.frozen_at,
            "movies_total": len(snapshot.movies),
            "catalog_movies": len(snapshot.active_ids),
            "duplicate_ids": list(snapshot.duplicate_ids),
            "invalid_entries": list(snapshot.invalid_entries),
        },
        "scenario_summary": {
            "total": len(results),
            "ok": len(ok),
            "errors": len(errors),
            "by_split": by_split,
            "by_category": dict(sorted(by_category.items())),
            "empty_results": sum(1 for item in ok if not item["ranked_ids"]),
            "label_eligible": len(label_metrics),
            "label_reasons": dict(sorted(label_reason_counts.items())),
        },
        "ground_truth": {
            "label_sources_in_metrics": sorted({
                item["labels"]["source"] for item in label_metrics}),
            "claim_scope": claim_scope,
            "effectiveness_available": claim_scope == "REAL_LABEL_EFFECTIVENESS",
            "effectiveness_reason": (
                None if claim_scope == "REAL_LABEL_EFFECTIVENESS"
                else "NO_RELIABLE_TEST_LABELS" if not label_metrics
                else "SYNTHETIC_ONLY" if claim_scope == "SYNTHETIC_ENGINE_CHECK_ONLY"
                else "MIXED_LABEL_SOURCES"),
            "online_effect": "NOT_PROVEN",
        },
        "metrics": metrics_doc,
        "metric_definitions": dict(METRIC_DEFINITIONS),
        "resources": {
            "scenarios_measured": len(resource_values),
            "elapsed_ms_sum": round(sum(latencies), 3) if latencies else None,
            "elapsed_ms_mean": round(sum(latencies) / len(latencies), 3) if latencies else None,
            "peak_traced_bytes_max": max(peaks) if peaks else None,
            "paid_model_tokens_total": sum(
                item["resources"].get("paid_model_tokens", 0) for item in ok
                if item["resources"]),
        },
        "warnings": list(snapshot.warnings) + list(state.warnings),
        "scenarios": results,
    }


__all__ = [
    "EvalConfig",
    "EvaluationError",
    "AdapterOutputError",
    "METRIC_DEFINITIONS",
    "evaluate",
]