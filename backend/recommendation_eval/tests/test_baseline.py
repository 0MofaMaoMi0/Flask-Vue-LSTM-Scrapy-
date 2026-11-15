import ast
import copy
import hashlib
import pathlib
import unittest

from recommendation_eval import baseline_v1, boards_reference


class BaselineTests(unittest.TestCase):
    def test_frozen_function_source_hash(self):
        path = pathlib.Path(baseline_v1.__file__).resolve()
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        nodes = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name == "recommend"]
        self.assertEqual(len(nodes), 1)
        segment = ast.get_source_segment(text, nodes[0])
        self.assertIsNotNone(segment)
        self.assertEqual(hashlib.sha256(segment.encode("utf-8")).hexdigest(),
                         baseline_v1.FUNCTION_SHA256)
        self.assertEqual(nodes[0].end_lineno - nodes[0].lineno,
                         baseline_v1.FUNCTION_LINES[1] - baseline_v1.FUNCTION_LINES[0])

    def test_provenance_and_scope_are_explicit(self):
        self.assertEqual(len(baseline_v1.SOURCE_SHA256), 64)
        self.assertEqual(baseline_v1.LICENSE_STATUS, "NOT_DECLARED_IN_REPO")
        self.assertIn("不能声称覆盖全部旧频道", baseline_v1.SCOPE_NOTE)
        self.assertTrue(any("public" in item for item in baseline_v1.NOT_COVERED))
        self.assertTrue(any("daily" in item for item in baseline_v1.NOT_COVERED))
        self.assertEqual(baseline_v1.PARAMETERS["limit"], 12)
        self.assertEqual(baseline_v1.PARAMETERS["baseline_field"], "demo_popularity")

    def test_frozen_golden_output_and_no_input_mutation(self):
        movies = [
            {"movie_id": 1, "title": "A", "genres": ["剧情"], "release_year": 2020,
             "original_language": "zh", "catalog_status": "active", "demo_popularity": 0.9},
            {"movie_id": 2, "title": "B", "genres": ["剧情"], "release_year": 2021,
             "original_language": "zh", "catalog_status": "active", "demo_popularity": 0.5},
            {"movie_id": 3, "title": "C", "genres": ["科幻"], "release_year": 2010,
             "original_language": "en", "catalog_status": "active", "demo_popularity": 0.7},
            {"movie_id": 4, "title": "D", "genres": ["剧情"], "release_year": 2019,
             "original_language": "zh", "catalog_status": "active", "demo_popularity": 0.2},
        ]
        state = {
            "1": {"favorite": True, "rating": 5.0},
            "2": {"want": True},
            "3": {"watched": True},
        }
        movies_before, state_before = copy.deepcopy(movies), copy.deepcopy(state)
        result = baseline_v1.recommend(movies, state, limit=3)
        self.assertEqual([item["movie_id"] for item in result["items"]], [2, 1, 4])
        self.assertAlmostEqual(result["items"][0]["score"], 7.36666667, places=8)
        self.assertEqual(result["items"][0]["components"].keys(),
                         {"baseline", "preference"})
        self.assertEqual(result["candidate_count"], 3)
        self.assertEqual(result["paid_model_tokens"], 0)
        self.assertEqual(movies, movies_before)
        self.assertEqual(state, state_before)

    def test_wrapper_delegates_to_frozen_function(self):
        movies = [{"movie_id": 1, "title": "A", "genres": [], "catalog_status": "active",
                   "demo_popularity": 0.5}]
        self.assertEqual(baseline_v1.recommend_baseline(movies, {}),
                         baseline_v1.recommend(movies, {}))


class BoardsReferenceTests(unittest.TestCase):
    def test_all_channels_are_recorded_without_overclaiming(self):
        self.assertEqual(len(boards_reference.CHANNEL_NAMES), 10)
        self.assertEqual(set(boards_reference.CHANNEL_NAMES),
                         {"popular", "trending_day", "trending_week", "daily", "weekly",
                          "top_rated", "revenue", "because", "watchlist", "personal"})
        self.assertIn("Baseline V1 只对应", boards_reference.SCOPE_NOTE)
        self.assertTrue(boards_reference.NOT_COVERED_BY_BASELINE)
        self.assertIn("paid_model_tokens", boards_reference.SHARED)
        self.assertEqual(boards_reference.describe("popular")["title"], "热门推荐")
        with self.assertRaises(KeyError):
            boards_reference.describe("not-a-channel")

    def test_boards_source_hash_is_a_frozen_metadata_value(self):
        self.assertEqual(len(boards_reference.SOURCE_SHA256), 64)
        self.assertEqual(boards_reference.BOARDS_VERSION, "boards-v1")
        self.assertTrue(all(isinstance(item, dict)
                            for item in boards_reference.CHANNELS.values()))


if __name__ == "__main__":
    unittest.main()