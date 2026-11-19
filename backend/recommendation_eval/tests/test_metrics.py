import math
import unittest

from recommendation_eval.metrics import (
    catalog_coverage,
    genre_coverage,
    intra_list_genre_diversity,
    ndcg_at_k,
    rate_or_none,
    recall_at_k,
)
from recommendation_eval.schema import condition_violations, validate_hard_conditions


class RankingMetricTests(unittest.TestCase):
    def test_recall_denominator_is_full_relevant_set(self):
        self.assertAlmostEqual(recall_at_k([1, 5, 3], {1, 3, 4}, 10), 2 / 3)
        self.assertEqual(recall_at_k([1, 2], {1, 2}, 1), 0.5)
        self.assertIsNone(recall_at_k([1], set(), 10))

    def test_ndcg_uses_ideal_positions_not_list_length(self):
        value = ndcg_at_k([1, 2, 3], {1, 3}, 10)
        expected = 1.5 / (1 + 1 / math.log2(3))
        self.assertAlmostEqual(value, expected, places=12)
        self.assertEqual(ndcg_at_k([2, 2], {1}, 10), 0.0)
        self.assertIsNone(ndcg_at_k([], set(), 10))

    def test_duplicate_ranked_ids_do_not_take_extra_slots(self):
        self.assertAlmostEqual(recall_at_k([1, 1, 2], {2}, 2), 1.0)

    def test_coverage_excludes_unknown_by_contract(self):
        self.assertEqual(catalog_coverage([1, 3, 99], {1, 2, 3, 4}), 0.5)
        self.assertIsNone(catalog_coverage([1], set()))
        self.assertEqual(genre_coverage(["剧情", "未知"], ["剧情", "科幻"]), 0.5)
        self.assertIsNone(genre_coverage(["剧情"], []))


class DiversityTests(unittest.TestCase):
    def test_pairwise_jaccard_distance(self):
        value = intra_list_genre_diversity([["a", "b"], ["b", "c"], ["a", "b"]])
        self.assertAlmostEqual(value, 4 / 9, places=12)

    def test_fewer_than_two_genre_bearing_items_is_null(self):
        self.assertIsNone(intra_list_genre_diversity([["a"]]))
        self.assertIsNone(intra_list_genre_diversity([[], []]))
        self.assertEqual(intra_list_genre_diversity([["a"], ["a"]]), 0.0)

    def test_rate_denominator_guard(self):
        self.assertIsNone(rate_or_none(0, 0))
        self.assertEqual(rate_or_none(1, 4), 0.25)


class HardConditionTests(unittest.TestCase):
    def test_all_supported_conditions_report_explicit_codes(self):
        filters = validate_hard_conditions({
            "year_from": 2020,
            "year_to": 2022,
            "runtime_max": 120,
            "language": "zh",
            "genres_any": ["剧情"],
            "excluded_genres": ["恐怖"],
            "unwatched": True,
        })
        movie = {
            "movie_id": 1, "release_year": 2019, "runtime_minutes": 150,
            "original_language": "en", "genres": ["剧情片", "恐怖"],
        }
        codes = condition_violations(movie, {"watched": True}, filters)
        self.assertEqual(codes, [
            "GENRE_EXCLUDED", "LANGUAGE_MISMATCH", "RUNTIME_ABOVE_MAX",
            "WATCHED_BUT_UNWATCHED_REQUIRED", "YEAR_BELOW_MIN",
        ])

    def test_genre_substring_semantics_and_year_ranges(self):
        filters = validate_hard_conditions({
            "year_ranges_any": [[2018, 2020]],
            "excluded_year_ranges": [[2019, 2019]],
            "genres_all": ["剧情", "家庭"],
        })
        self.assertEqual(condition_violations(
            {"release_year": 2021, "genres": ["剧情片", "家庭"]}, {}, filters),
            ["YEAR_NOT_IN_RANGE"])
        self.assertEqual(condition_violations(
            {"release_year": 2019, "genres": ["剧情片", "家庭"]}, {}, filters),
            ["YEAR_EXCLUDED"])
        self.assertEqual(condition_violations(
            {"release_year": 2018, "genres": ["剧情片"]}, {}, filters),
            ["GENRE_REQUIRED_MISSING"])

    def test_contradictory_or_unknown_filters_rejected(self):
        for bad in ({"year_from": 2025, "year_to": 2020},
                    {"genre": "剧情", "genres_any": ["科幻"]},
                    {"unknown": 1}):
            with self.assertRaises(ValueError):
                validate_hard_conditions(bad)


if __name__ == "__main__":
    unittest.main()