import pathlib
import unittest

from recommendation_eval.schema import (
    REQUIRED_CATEGORIES,
    SPLITS,
    SchemaError,
    load_json,
    validate_scenarios,
    validate_snapshot,
    validate_state,
)
from recommendation_eval.tests.support import (
    movie,
    scenario,
    scenarios_doc,
    snapshot_doc,
    state_doc,
)

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"


class SnapshotTests(unittest.TestCase):
    def test_duplicate_movie_id_strict_and_lenient(self):
        doc = snapshot_doc([movie(1), movie(1, title="SYNTHETIC duplicate")])
        with self.assertRaises(SchemaError):
            validate_snapshot(doc)
        snapshot = validate_snapshot(doc, strict=False)
        self.assertEqual(len(snapshot.movies), 1)
        self.assertEqual(snapshot.duplicate_ids, (1,))
        self.assertTrue(snapshot.warnings)

    def test_invalid_ids_are_rejected_then_recorded(self):
        doc = snapshot_doc([movie("1"), movie(-2), movie(True), movie(3)])
        with self.assertRaises(SchemaError):
            validate_snapshot(doc)
        snapshot = validate_snapshot(doc, strict=False)
        self.assertEqual([item["movie_id"] for item in snapshot.movies], [3])
        self.assertEqual(len(snapshot.invalid_entries), 3)

    def test_hidden_movies_are_not_catalog_candidates(self):
        snapshot = validate_snapshot(snapshot_doc([
            movie(1), movie(2, catalog_status="hidden")]))
        self.assertEqual(snapshot.active_ids, (1,))


class StateTests(unittest.TestCase):
    def test_unknown_movie_strict_and_lenient(self):
        snapshot = validate_snapshot(snapshot_doc([movie(1)]))
        with self.assertRaises(SchemaError):
            validate_state(state_doc({"2": {"favorite": True}}), snapshot)
        state = validate_state(state_doc({"2": {"favorite": True}}), snapshot,
                               strict=False)
        self.assertEqual(state.states, {})
        self.assertEqual(state.unknown_ids, (2,))

    def test_rating_and_field_validation(self):
        snapshot = validate_snapshot(snapshot_doc([movie(1)]))
        for bad in (0.49, 5.5, 4.25, True, "4"):
            with self.assertRaises(SchemaError):
                validate_state(state_doc({"1": {"rating": bad}}), snapshot)
        with self.assertRaises(SchemaError):
            validate_state(state_doc({"1": {"clicked": True}}), snapshot)
        good = validate_state(state_doc({"1": {"rating": 4.5, "watched": True}}),
                              snapshot)
        self.assertEqual(good.states["1"]["rating"], 4.5)
        self.assertTrue(good.states["1"]["watched"])


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = validate_snapshot(snapshot_doc([movie(1), movie(2), movie(3)]))

    def test_fixture_covers_every_category_and_split(self):
        fixture_snapshot = validate_snapshot(
            load_json(FIXTURES / "synthetic_snapshot.json"))
        scenarios = validate_scenarios(
            load_json(FIXTURES / "synthetic_scenarios.json"), fixture_snapshot,
            require_categories=True, require_splits=True)
        self.assertEqual({item["category"] for item in scenarios},
                         set(REQUIRED_CATEGORIES))
        self.assertEqual({item["split"] for item in scenarios}, set(SPLITS))
        self.assertEqual(len(scenarios), 10)

    def test_historical_labels_cannot_use_current_state_as_future_truth(self):
        bad = scenario(
            "HIST-BAD", source="HISTORICAL_HELDOUT", reliable=True, relevant=[1],
            label_window={"from": "2025-12-31T00:00:00+00:00",
                          "to": "2026-02-01T00:00:00+00:00"},
            state_as_of="2026-01-01T00:00:00+00:00")
        with self.assertRaises(SchemaError):
            validate_scenarios(scenarios_doc([bad]), self.snapshot)
        good = scenario(
            "HIST-GOOD", source="HISTORICAL_HELDOUT", reliable=True, relevant=[1],
            label_window={"from": "2026-01-02T00:00:00+00:00",
                          "to": "2026-02-01T00:00:00+00:00"},
            state_as_of="2026-01-01T00:00:00+00:00")
        accepted = validate_scenarios(scenarios_doc([good]), self.snapshot)
        self.assertEqual(accepted[0]["labels"]["label_source"], "HISTORICAL_HELDOUT")

    def test_historical_requires_window_and_state_cutoff(self):
        no_window = scenario("HIST-NO-WINDOW", source="HISTORICAL_HELDOUT",
                             reliable=True, relevant=[1],
                             state_as_of="2026-01-01T00:00:00+00:00")
        with self.assertRaises(SchemaError):
            validate_scenarios(scenarios_doc([no_window]), self.snapshot)
        no_cutoff = scenario("HIST-NO-CUTOFF", source="HISTORICAL_HELDOUT",
                             reliable=True, relevant=[1],
                             label_window={"from": "2026-01-02T00:00:00+00:00",
                                           "to": "2026-02-01T00:00:00+00:00"})
        with self.assertRaises(SchemaError):
            validate_scenarios(scenarios_doc([no_cutoff]), self.snapshot)

    def test_label_source_rules_and_empty_set(self):
        synthetic_reliable = scenario("SYN-RELIABLE", source="SYNTHETIC",
                                      reliable=True, relevant=[1])
        with self.assertRaises(SchemaError):
            validate_scenarios(scenarios_doc([synthetic_reliable]), self.snapshot)
        none_with_ids = scenario("NONE-IDS", source="NONE", reliable=False, relevant=[1])
        with self.assertRaises(SchemaError):
            validate_scenarios(scenarios_doc([none_with_ids]), self.snapshot)
        for forbidden in ("WATCHED", "CLICK", "IMPRESSION"):
            doc = scenario("BAD-SOURCE", source=forbidden, reliable=False, relevant=[])
            with self.assertRaises(SchemaError):
                validate_scenarios(scenarios_doc([doc]), self.snapshot)
        empty = scenario("EMPTY", source="NONE", reliable=False, relevant=[])
        accepted = validate_scenarios(scenarios_doc([empty]), self.snapshot)
        self.assertEqual(accepted[0]["labels"]["relevant_movie_ids"], [])

    def test_unknown_label_id_is_not_silently_treated_as_real(self):
        bad = scenario("UNKNOWN-LABEL", relevant=[999], source="HUMAN", reliable=True)
        with self.assertRaises(SchemaError):
            validate_scenarios(scenarios_doc([bad]), self.snapshot)
        lenient = validate_scenarios(scenarios_doc([bad]), self.snapshot, strict=False)
        self.assertEqual(lenient[0]["labels"]["invalid_movie_ids"], [999])
        self.assertEqual(lenient[0]["labels"]["relevant_movie_ids"], [])

    def test_schema_and_template_cover_categories_without_fake_ids(self):
        schema = load_json(FIXTURES / "scenarios.schema.json")
        self.assertEqual(schema["$defs"]["scenario"]["properties"]["category"]["enum"],
                         list(REQUIRED_CATEGORIES))
        template = load_json(FIXTURES / "scenarios.template.json")
        self.assertTrue(template["template"])
        self.assertEqual({item["category"] for item in template["scenarios"]},
                         set(REQUIRED_CATEGORIES))
        self.assertEqual({item["split"] for item in template["scenarios"]},
                         set(SPLITS))
        self.assertTrue(all(item["labels"]["relevant_movie_ids"] == []
                            for item in template["scenarios"]))

    def test_duplicate_scenario_id_rejected(self):
        doc = scenarios_doc([scenario("DUP"), scenario("DUP")])
        with self.assertRaises(SchemaError):
            validate_scenarios(doc, self.snapshot)


if __name__ == "__main__":
    unittest.main()