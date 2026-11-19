import json
import unittest

from recommendation_eval.adapters import BaselineV1Adapter, CallableAdapter
from recommendation_eval.demo import run_demo
from recommendation_eval.evaluate import EvalConfig, EvaluationError, evaluate
from recommendation_eval.schema import validate_snapshot
from recommendation_eval.tests.support import (
    movie,
    scenario,
    scenarios_doc,
    snapshot_doc,
    state_doc,
)


def static(items):
    return CallableAdapter(lambda movies, state, scenario: items, name="static")


def catalog(count=3):
    return validate_snapshot(snapshot_doc([movie(index) for index in range(1, count + 1)]))


class LabelPolicyTests(unittest.TestCase):
    def test_synthetic_is_null_in_effectiveness_and_computed_in_engine_check(self):
        snapshot = catalog()
        synthetic = scenario("SYN", relevant=[1, 2], source="SYNTHETIC",
                             reliable=False, split="test")
        adapter = static([1])
        effective = evaluate(snapshot, state_doc({}), scenarios_doc([synthetic]),
                             adapter, EvalConfig(metric_mode="effectiveness"))
        self.assertIsNone(effective["metrics"]["recall_at_10"]["value"])
        self.assertEqual(effective["scenarios"][0]["labels"]["reason"],
                         "UNRELIABLE_LABEL_SOURCE")
        self.assertFalse(effective["ground_truth"]["effectiveness_available"])
        engine = evaluate(snapshot, state_doc({}), scenarios_doc([synthetic]),
                          adapter, EvalConfig(metric_mode="synthetic_engine_check"))
        self.assertEqual(engine["metrics"]["recall_at_10"]["value"], 0.5)
        self.assertEqual(engine["ground_truth"]["claim_scope"],
                         "SYNTHETIC_ENGINE_CHECK_ONLY")
        self.assertFalse(engine["ground_truth"]["effectiveness_available"])

    def test_reliable_human_test_label_is_computed(self):
        snapshot = catalog()
        human = scenario("HUMAN", relevant=[1, 2], source="HUMAN",
                         reliable=True, split="test")
        result = evaluate(snapshot, state_doc({}), scenarios_doc([human]), static([1]))
        self.assertEqual(result["metrics"]["recall_at_10"]["value"], 0.5)
        self.assertTrue(result["ground_truth"]["effectiveness_available"])
        self.assertEqual(result["ground_truth"]["claim_scope"],
                         "REAL_LABEL_EFFECTIVENESS")

    def test_train_split_is_not_used_for_effectiveness(self):
        snapshot = catalog()
        train = scenario("TRAIN", relevant=[1], source="HUMAN",
                         reliable=True, split="train")
        result = evaluate(snapshot, state_doc({}), scenarios_doc([train]), static([1]))
        self.assertIsNone(result["metrics"]["recall_at_10"]["value"])
        self.assertEqual(result["scenarios"][0]["labels"]["reason"], "NOT_TEST_SPLIT")

    def test_empty_labels_return_null_with_reason(self):
        snapshot = catalog()
        empty = scenario("EMPTY", relevant=[], source="NONE", reliable=False)
        result = evaluate(snapshot, state_doc({}), scenarios_doc([empty]), static([]))
        self.assertIsNone(result["metrics"]["recall_at_10"]["value"])
        self.assertEqual(result["scenarios"][0]["labels"]["reason"], "EMPTY_LABEL_SET")


class OutputContractTests(unittest.TestCase):
    def test_duplicate_invalid_and_truncated_output_semantics(self):
        snapshot = catalog(12)
        scenario_doc = scenario("TRUNC", relevant=[1], source="HUMAN", reliable=True)
        items = [1, 1, 999, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
        result = evaluate(snapshot, state_doc({}), scenarios_doc([scenario_doc]),
                          static(items))
        row = result["scenarios"][0]
        self.assertEqual(row["ranked_ids"], list(range(1, 11)))
        self.assertEqual(row["dropped_after_k"], [11])
        self.assertTrue(row["truncated"])
        self.assertEqual(row["duplicate_ids"], [1])
        self.assertEqual(row["invalid_items"], [
            {"raw_rank": 3, "movie_id": 999, "reason": "UNKNOWN_ID"}])
        self.assertEqual(row["returned_total"], 13)
        self.assertAlmostEqual(result["metrics"]["invalid_id_rate"]["value"], 1 / 13)
        self.assertAlmostEqual(result["metrics"]["duplicate_output_rate"]["value"], 1 / 13)
        self.assertAlmostEqual(result["metrics"]["catalog_coverage"]["value"], 10 / 12)

    def test_empty_result_is_zero_when_labels_exist_but_null_when_not(self):
        snapshot = catalog()
        with_labels = scenario("EMPTY-RESULT", relevant=[1], source="HUMAN",
                               reliable=True)
        result = evaluate(snapshot, state_doc({}), scenarios_doc([with_labels]),
                          static([]))
        row = result["scenarios"][0]
        self.assertEqual(row["metrics"]["recall_at_10"], 0.0)
        self.assertEqual(row["metrics"]["ndcg_at_10"], 0.0)
        self.assertIsNone(row["metrics"]["intra_list_genre_diversity_at_10"])
        self.assertIsNone(row["metrics"]["negative_feedback_recommendation_rate"])
        self.assertEqual(result["scenario_summary"]["empty_results"], 1)

    def test_hard_condition_and_negative_feedback_rates(self):
        movies = [
            movie(1, release_year=2010, genres=["剧情"]),
            movie(2, release_year=2021, genres=["剧情"]),
            movie(3, release_year=2021, genres=["科幻"]),
        ]
        snapshot = validate_snapshot(snapshot_doc(movies))
        row = scenario("HARD", relevant=[3], source="HUMAN", reliable=True,
                       hard_conditions={"year_from": 2020, "unwatched": True},
                       state={"2": {"watched": True}})
        result = evaluate(snapshot, state_doc({}), scenarios_doc([row]),
                          static([1, 2, 3]))
        metrics = result["metrics"]
        self.assertAlmostEqual(metrics["hard_condition_violation_rate"]["value"], 2 / 3)
        self.assertEqual(metrics["hard_condition_violation_rate"]["positions"], 3)
        self.assertAlmostEqual(metrics["negative_feedback_recommendation_rate"]["value"],
                               1 / 3)
        self.assertEqual(result["scenarios"][0]["metrics"]["negative_feedback_ids"], [2])

    def test_external_result_never_becomes_a_fake_internal_id(self):
        snapshot = catalog()
        allowed = scenario("EXT", relevant=[], source="NONE", reliable=False,
                           allow_external=True)
        external = {"movie_id": None, "external_id": "SYNTHETIC_EXT",
                    "title": "SYNTHETIC", "score": None}
        result = evaluate(snapshot, state_doc({}), scenarios_doc([allowed]),
                          static([external]))
        row = result["scenarios"][0]
        self.assertEqual(row["ranked_ids"], [])
        self.assertEqual(row["external_items"][0]["external_id"], "SYNTHETIC_EXT")
        self.assertTrue(row["external_items"][0]["allowed"])
        self.assertEqual(result["metrics"]["invalid_id_rate"]["value"], 0.0)

        disallowed = scenario("EXT-NO", relevant=[], source="NONE", reliable=False)
        denied = evaluate(snapshot, state_doc({}), scenarios_doc([disallowed]),
                          static([external]))
        denied_row = denied["scenarios"][0]
        self.assertFalse(denied_row["external_items"][0]["allowed"])
        self.assertEqual(denied_row["ranked_ids"], [])
        self.assertEqual(denied["metrics"]["invalid_id_rate"]["value"], 0.0)

    def test_adapter_error_is_isolated_and_can_fail_fast(self):
        snapshot = catalog()

        def flaky(movies, state, scenario_doc):
            if scenario_doc["scenario_id"] == "BAD":
                raise RuntimeError("synthetic adapter failure")
            return [1]

        doc = scenarios_doc([
            scenario("BAD", relevant=[1], source="HUMAN", reliable=True),
            scenario("GOOD", relevant=[1], source="HUMAN", reliable=True),
        ])
        result = evaluate(snapshot, state_doc({}), doc, CallableAdapter(flaky))
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["scenario_summary"]["errors"], 1)
        self.assertEqual(result["scenario_summary"]["ok"], 1)
        self.assertEqual(result["metrics"]["recall_at_10"]["value"], 1.0)
        with self.assertRaises(EvaluationError):
            evaluate(snapshot, state_doc({}), doc, CallableAdapter(flaky),
                     EvalConfig(fail_fast=True))

    def test_duplicate_invalid_and_truncated_ids_are_json_safe(self):
        snapshot = catalog(3)
        row = scenario("JSON", relevant=[1], source="HUMAN", reliable=True)
        result = evaluate(snapshot, state_doc({}), scenarios_doc([row]),
                          static([1, 1, 999]))
        text = json.dumps(result, ensure_ascii=False)
        self.assertIn('"invalid_items"', text)


class EmptyInputTests(unittest.TestCase):
    def test_empty_snapshot_and_empty_scenario_list_do_not_invent_denominators(self):
        snapshot = validate_snapshot(snapshot_doc([]))
        self.assertEqual(snapshot.active_ids, ())
        result = evaluate(snapshot, state_doc({}), scenarios_doc([]), static([]))
        self.assertEqual(result["scenario_summary"]["total"], 0)
        self.assertIsNone(result["metrics"]["catalog_coverage"]["value"])
        self.assertEqual(result["metrics"]["catalog_coverage"]["reason"], "EMPTY_CATALOG")
        self.assertIsNone(result["metrics"]["recall_at_10"]["value"])

    def test_not_interested_and_watched_are_both_negative(self):
        snapshot = catalog()
        row = scenario("NEG", relevant=[1], source="HUMAN", reliable=True,
                       state={"2": {"watched": True},
                              "3": {"not_interested": True}})
        result = evaluate(snapshot, state_doc({}), scenarios_doc([row]),
                          static([1, 2, 3]))
        self.assertEqual(result["scenarios"][0]["metrics"]["negative_feedback_ids"],
                         [2, 3])
        self.assertAlmostEqual(
            result["metrics"]["negative_feedback_recommendation_rate"]["value"], 2 / 3)


class BaselinePolicyTests(unittest.TestCase):
    def test_raw_baseline_does_not_apply_channel_hard_filters(self):
        movies = [movie(1, release_year=2010), movie(2, release_year=2021),
                  movie(3, release_year=2021)]
        snapshot = validate_snapshot(snapshot_doc(movies))
        row = scenario("RAW", relevant=[2], source="HUMAN", reliable=True,
                       hard_conditions={"year_from": 2020})
        raw = evaluate(snapshot, state_doc({}), scenarios_doc([row]),
                       BaselineV1Adapter(mode="raw", baseline_field="demo_popularity"))
        filtered = evaluate(snapshot, state_doc({}), scenarios_doc([row]),
                            BaselineV1Adapter(mode="prefiltered",
                                              baseline_field="demo_popularity"))
        self.assertGreater(raw["metrics"]["hard_condition_violation_rate"]["value"], 0.0)
        self.assertEqual(filtered["metrics"]["hard_condition_violation_rate"]["value"], 0.0)

    def test_deterministic_without_resource_collection(self):
        snapshot = catalog(5)
        doc = scenarios_doc([
            scenario("D1", relevant=[1, 2], source="HUMAN", reliable=True),
            scenario("D2", relevant=[3], source="HUMAN", reliable=True),
        ])
        adapter = static([2, 1, 3])
        first = evaluate(snapshot, state_doc({}), doc, adapter,
                        EvalConfig(collect_resources=False))
        second = evaluate(snapshot, state_doc({}), doc, adapter,
                         EvalConfig(collect_resources=False))
        self.assertEqual(json.dumps(first, sort_keys=True),
                         json.dumps(second, sort_keys=True))


class EndToEndTests(unittest.TestCase):
    def test_synthetic_demo_passes_its_own_checks(self):
        result = run_demo(collect_resources=False)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(result["checks"]), 11)
        self.assertTrue(all(check["pass"] for check in result["checks"]))
        engine = result["engine_check"]
        self.assertEqual(engine["scenario_summary"]["total"], 10)
        self.assertEqual(engine["scenario_summary"]["errors"], 0)
        self.assertIsNotNone(engine["metrics"]["recall_at_10"]["value"])
        self.assertIsNotNone(engine["metrics"]["ndcg_at_10"]["value"])
        self.assertEqual(engine["metrics"]["hard_condition_violation_rate"]["value"], 0.0)
        self.assertEqual(engine["metrics"]["negative_feedback_recommendation_rate"]["value"], 0.0)
        self.assertEqual(engine["resources"]["paid_model_tokens_total"], 0)
        self.assertFalse(result["effectiveness_probe"]["ground_truth"]["effectiveness_available"])


if __name__ == "__main__":
    unittest.main()