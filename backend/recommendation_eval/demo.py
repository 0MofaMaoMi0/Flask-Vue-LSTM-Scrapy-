"""One synthetic end-to-end demonstration; no network, DB or model calls."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import EVAL_SCHEMA_VERSION
from .adapters import DemoAdapter
from .evaluate import EvalConfig, evaluate
from .schema import (
    REQUIRED_CATEGORIES,
    SPLITS,
    load_json,
    validate_all,
)

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"


class DemoError(AssertionError):
    pass


def _check(results, name, condition, detail=None):
    results.append({
        "check": name,
        "pass": bool(condition),
        "detail": detail,
    })
    if not condition:
        raise DemoError(name)


def run_demo(*, collect_resources: bool = True) -> dict:
    snapshot_doc = load_json(FIXTURES / "synthetic_snapshot.json")
    state_doc = load_json(FIXTURES / "synthetic_state.json")
    scenarios_doc = load_json(FIXTURES / "synthetic_scenarios.json")
    snapshot, state, scenarios = validate_all(
        snapshot_doc, state_doc, scenarios_doc,
        require_categories=True, require_splits=True)

    engine = evaluate(
        snapshot, state, scenarios, DemoAdapter(),
        EvalConfig(metric_mode="synthetic_engine_check",
                   collect_resources=collect_resources))
    probe = evaluate(
        snapshot, state, scenarios, DemoAdapter(),
        EvalConfig(metric_mode="effectiveness",
                   collect_resources=False))

    checks = []
    _check(checks, "scenario_categories_cover_all_10",
           {item["category"] for item in scenarios} == set(REQUIRED_CATEGORIES))
    _check(checks, "scenario_splits_explicit_train_dev_test",
           {item["split"] for item in scenarios} == set(SPLITS))
    _check(checks, "all_scenarios_execute", engine["status"] == "OK",
           engine["scenario_summary"])
    _check(checks, "all_returned_ids_exist_in_active_snapshot",
           all(mid in set(snapshot.active_ids)
               for item in engine["scenarios"]
               for mid in item.get("ranked_ids", [])))
    _check(checks, "synthetic_labels_do_not_claim_real_effect",
           probe["ground_truth"]["effectiveness_available"] is False
           and probe["metrics"]["recall_at_10"]["value"] is None,
           probe["ground_truth"])
    _check(checks, "synthetic_engine_metrics_are_computed",
           engine["metrics"]["recall_at_10"]["value"] is not None
           and engine["metrics"]["ndcg_at_10"]["value"] is not None)
    _check(checks, "prefiltered_baseline_has_zero_hard_violations",
           engine["metrics"]["hard_condition_violation_rate"]["value"] == 0.0,
           engine["metrics"]["hard_condition_violation_rate"])
    _check(checks, "prefiltered_baseline_has_zero_negative_feedback",
           engine["metrics"]["negative_feedback_recommendation_rate"]["value"] == 0.0,
           engine["metrics"]["negative_feedback_recommendation_rate"])
    _check(checks, "no_paid_model_tokens",
           engine["resources"]["paid_model_tokens_total"] == 0)
    out_of_catalog = [item for item in engine["scenarios"]
                      if item["category"] == "out_of_catalog"][0]
    _check(checks, "external_result_has_no_fake_internal_movie_id",
           out_of_catalog["ranked_ids"] == []
           and all(item.get("external_id") for item in out_of_catalog["external_items"]))
    score_items = [item for scenario in engine["scenarios"]
                   for item in scenario.get("items", [])]
    _check(checks, "actual_ids_scores_and_components_present",
           bool(score_items) and all(
               type(item["movie_id"]) is int
               and (item["score"] is None or type(item["score"]) in (int, float))
               and type(item["components"]) is dict
               for item in score_items))

    return {
        "demo": "M10 Baseline V1 synthetic end-to-end",
        "schema_version": EVAL_SCHEMA_VERSION,
        "status": "PASS" if all(item["pass"] for item in checks) else "FAIL",
        "synthetic": True,
        "warning": "SYNTHETIC 仅验证接口与计算正确性，不能称为真实用户效果。",
        "dataset": {
            "dataset_id": snapshot.dataset_id,
            "source": snapshot.source,
            "movies_total": len(snapshot.movies),
            "catalog_movies": len(snapshot.active_ids),
            "scenarios": len(scenarios),
        },
        "checks": checks,
        "engine_check": engine,
        "effectiveness_probe": {
            "metric_mode": "effectiveness",
            "ground_truth": probe["ground_truth"],
            "metrics": {
                "recall_at_10": probe["metrics"]["recall_at_10"],
                "ndcg_at_10": probe["metrics"]["ndcg_at_10"],
                "reason": probe["ground_truth"]["effectiveness_reason"],
            },
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the M10 synthetic evaluation demo.")
    parser.add_argument("--output", required=True, help="JSON output path (non-C: drive).")
    args = parser.parse_args(argv)
    output = pathlib.Path(args.output).resolve()
    if output.drive.upper() == "C:":
        raise SystemExit("refusing C: output")
    result = run_demo(collect_resources=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    for check in result["checks"]:
        print("%s %s" % ("PASS" if check["pass"] else "FAIL", check["check"]))
    print("DEMO %s" % result["status"])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())