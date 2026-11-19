"""Pure offline tests for the M10 local ranking pipeline.

All movies/behaviour here are synthetic fixtures (movie_id 1..99; titles are
marked 合成). No Flask app, database, network, model or real user data is used.
"""
from __future__ import annotations

import copy
import math
import unittest
from unittest import mock

from catalog import recommendation_pipeline as p


def movie(movie_id, **overrides):
    row = {
        "movie_id": movie_id,
        "title": "合成电影%s" % movie_id,
        "catalog_status": "active",
        "is_adult": False,
        "genres": ["剧情"],
        "release_year": 2000 + movie_id if type(movie_id) is int else 2000,
        "runtime_minutes": 90,
        "original_language": "zh",
        "catalog_popularity": 0.5,
        "tmdb_rating": 8.0,
        "vote_count": 1000,
        "director_ids": [],
        "actor_ids": [],
        "overview": "",
    }
    row.update(overrides)
    return row


def ids(result):
    return [item["movie_id"] for item in result["items"]]


def item_for(result, movie_id):
    return next(item for item in result["items"] if item["movie_id"] == movie_id)


class PipelineRankingTests(unittest.TestCase):
    def test_different_positive_signals_produce_explainable_difference(self):
        sci_seed = movie(
            1, genres=["科幻"], director_ids=[7], actor_ids=[9],
            release_year=2010, original_language="en", overview="space travel")
        drama_seed = movie(
            4, genres=["剧情"], director_ids=[8], release_year=2012,
            original_language="zh", overview="family")
        rows = [
            movie(2, genres=["科幻"], director_ids=[7], actor_ids=[9],
                  release_year=2010, original_language="en", overview="space travel"),
            movie(3, genres=["剧情"], director_ids=[8], release_year=2012,
                  original_language="zh", overview="family"),
        ]
        favourite = p.rank_movies(
            rows, {"1": {"favorite": True}},
            profile_movies=[sci_seed, drama_seed], limit=2)
        wanted = p.rank_movies(
            rows, {"4": {"want": True}},
            profile_movies=[sci_seed, drama_seed], limit=2)
        self.assertEqual(ids(favourite), [2, 3])
        self.assertEqual(ids(wanted), [3, 2])
        first = favourite["items"][0]
        self.assertIn("DIRECTOR_MATCH", first["reason_codes"])
        self.assertIn("ACTOR_OVERLAP", first["reason_codes"])
        self.assertTrue(any("同一位导演" in reason for reason in first["reasons"]))
        self.assertGreater(first["components"]["preference"], 0.0)

    def test_high_rating_is_a_positive_signal(self):
        seed = movie(1, genres=["科幻"], overview="space travel")
        rows = [movie(2, genres=["科幻"], overview="space travel")]
        result = p.rank_movies(
            rows, {"1": {"rating": 4.5}}, profile_movies=[seed], limit=1)
        signals = {ev.get("signal") for ev in result["items"][0]["evidence"]}
        self.assertIn("HIGH_RATING", signals)
        self.assertGreater(result["items"][0]["components"]["preference"], 0.0)

    def test_low_rating_is_negative_and_does_not_become_positive(self):
        seed = movie(1, genres=["科幻"], director_ids=[7], overview="space travel")
        rows = [
            movie(2, genres=["科幻"], director_ids=[7], overview="space travel"),
            movie(3, genres=["剧情"], director_ids=[8], overview="family"),
        ]
        result = p.rank_movies(
            rows, {"1": {"rating": 2.0}}, profile_movies=[seed], limit=2)
        self.assertEqual(ids(result), [3, 2])
        low = item_for(result, 2)
        self.assertIn("NEGATIVE_SIMILARITY", low["reason_codes"])
        negative = next(ev for ev in low["evidence"] if ev["signal"] == "LOW_RATING")
        self.assertEqual(negative["field"], "rating")
        self.assertEqual(negative["value"], 2.0)
        self.assertLess(negative["contribution"], 0.0)

    def test_not_interested_is_negative_and_cannot_be_rewatched(self):
        seed = movie(1, genres=["科幻"], director_ids=[7], overview="space travel")
        rows = [
            movie(1, genres=["科幻"], director_ids=[7], overview="space travel"),
            movie(2, genres=["科幻"], director_ids=[7], overview="space travel"),
            movie(3, genres=["剧情"], director_ids=[8], overview="family"),
        ]
        result = p.rank_movies(
            rows, {"1": {"not_interested": True, "watched": True}},
            profile_movies=[seed], allow_watched=True, limit=3)
        self.assertNotIn(1, ids(result))
        self.assertEqual(ids(result)[0], 3)
        negative = next(
            ev for ev in item_for(result, 2)["evidence"]
            if ev["signal"] == "NOT_INTERESTED")
        self.assertEqual(negative["field"], "not_interested")
        self.assertIs(negative["value"], True)

    def test_not_interested_dominates_conflicting_positive_flags(self):
        seed = movie(1, genres=["科幻"], director_ids=[7])
        rows = [
            movie(2, genres=["科幻"], director_ids=[7]),
            movie(3, genres=["剧情"], director_ids=[8]),
        ]
        result = p.rank_movies(
            rows, {"1": {"not_interested": True, "favorite": True}},
            profile_movies=[seed], limit=2)
        self.assertEqual(result["profile_seed_count"], 0)
        self.assertEqual(result["negative_seed_count"], 1)
        self.assertEqual(ids(result)[0], 3)
        self.assertLess(item_for(result, 2)["score"], item_for(result, 3)["score"])

    def test_watched_default_and_explicit_rewatch(self):
        rows = [movie(1), movie(2)]
        state = {"1": {"watched": True}}
        default = p.rank_movies(rows, state, limit=2)
        allowed = p.rank_movies(rows, state, allow_watched=True, limit=2)
        self.assertEqual(ids(default), [2])
        self.assertEqual(ids(allowed), [1, 2])
        self.assertTrue(allowed["cold_start"])
        self.assertEqual(allowed["profile_seed_count"], 0)
        rew = item_for(allowed, 1)
        self.assertIn("REWATCH_ALLOWED", rew["reason_codes"])
        self.assertTrue(any("显式允许重温" in reason for reason in rew["reasons"]))
        blocked = p.rank_movies(
            rows, state, allow_watched=True, filters={"unwatched": True}, limit=2)
        self.assertEqual(ids(blocked), [2])

    def test_clicks_and_impressions_are_not_positive_signals(self):
        rows = [movie(1, genres=["科幻"]), movie(2, genres=["剧情"])]
        result = p.rank_movies(
            rows, {"1": {"click": True, "impression": 10}}, limit=2)
        self.assertTrue(result["cold_start"])
        self.assertEqual(result["profile_seed_count"], 0)
        self.assertEqual(result["candidate_count"], 2)

    def test_multiple_hard_conditions_are_combined(self):
        rows = [
            movie(1, genres=["科幻"], release_year=1995, runtime_minutes=90,
                  original_language="en"),
            movie(2, genres=["科幻"], release_year=2020, runtime_minutes=90,
                  original_language="en"),
            movie(3, genres=["剧情"], release_year=1995, runtime_minutes=90,
                  original_language="en"),
            movie(4, genres=["科幻"], release_year=1995, runtime_minutes=120,
                  original_language="en"),
        ]
        result = p.rank_movies(rows, {}, filters={
            "genres_any": ["科幻"], "year_from": 1990, "year_to": 2000,
            "runtime_max": 100, "language": "en",
        }, limit=10)
        self.assertEqual(ids(result), [1])
        self.assertEqual(result["candidate_count"], 1)

    def test_structured_genre_and_year_groups_use_real_matches(self):
        rows = [
            movie(5, genres=["科幻", "冒险"], release_year=1993),
            movie(6, genres=["科幻"], release_year=1993),
            movie(7, genres=["科幻", "冒险"], release_year=2001),
            movie(8, genres=["科幻", "冒险"], release_year=1998),
        ]
        result = p.rank_movies(rows, {}, filters={
            "_genre_groups": [["科幻"], ["冒险"]],
            "_year_groups": [[[1990, 1995], [2000, 2005]]],
        }, limit=10)
        self.assertEqual(ids(result), [5, 7])

    def test_seed_outside_candidate_filter_still_contributes(self):
        seed = movie(1, genres=["科幻"], director_ids=[7], release_year=2010,
                     overview="space travel")
        rows = [
            movie(2, genres=["科幻", "剧情"], director_ids=[7], release_year=2010,
                  overview="space travel"),
            movie(3, genres=["剧情"], director_ids=[8], release_year=2010,
                  overview="family"),
        ]
        result = p.rank_movies(
            rows, {"1": {"favorite": True}}, profile_movies=[seed],
            filters={"genres_any": ["剧情"]}, limit=2)
        self.assertEqual(ids(result), [2, 3])
        self.assertIn(1, {ev["seed_movie_id"] for ev in result["items"][0]["evidence"]})

    def test_missing_and_nonfinite_fields_are_not_fabricated(self):
        rows = [movie(
            2, title=None, genres=None, release_year=float("nan"),
            catalog_popularity=float("nan"), tmdb_rating=float("inf"),
            vote_count=float("nan"), director_ids=None, actor_ids=None,
            overview=None)]
        result = p.rank_movies(rows, {}, limit=1)
        item = result["items"][0]
        self.assertEqual(item["title"], "")
        self.assertEqual(item["genres"], [])
        self.assertEqual(item["components"]["baseline"], 0.0)
        self.assertEqual(item["components"]["rating_confidence"], 0.0)
        self.assertEqual(item["score"], 0.0)
        self.assertIn("COLD_START_NO_EVIDENCE", item["reason_codes"])
        self.assertIn("MISSING_GENRE_PENALTY", item["reason_codes"])
        self.assertEqual(item["evidence"], [])

    def test_missing_fields_cannot_satisfy_hard_filters(self):
        rows = [
            movie(1, genres=None, release_year=float("nan"),
                  runtime_minutes="bad", original_language=None),
            movie(2, genres=["剧情"], release_year=1995,
                  runtime_minutes=90, original_language="zh"),
        ]
        by_genre = p.rank_movies(rows, {}, filters={"genres_any": ["剧情"]}, limit=2)
        self.assertEqual(ids(by_genre), [2])
        by_year = p.rank_movies(rows, {}, filters={"year_from": 1990}, limit=2)
        self.assertEqual(ids(by_year), [2])
        by_runtime = p.rank_movies(rows, {}, filters={"runtime_max": 100}, limit=2)
        self.assertEqual(ids(by_runtime), [2])

    def test_catalog_priors_need_valid_values_and_votes(self):
        rows = [
            movie(1, catalog_popularity=0.9, tmdb_rating=8.0, vote_count=1000),
            movie(2, catalog_popularity=float("nan"), tmdb_rating=8.0, vote_count=0),
        ]
        result = p.rank_movies(rows, {}, limit=2)
        first = item_for(result, 1)
        second = item_for(result, 2)
        self.assertGreater(first["components"]["baseline"], 0.0)
        self.assertGreater(first["components"]["rating_confidence"], 0.0)
        self.assertEqual(second["components"]["baseline"], 0.0)
        self.assertEqual(second["components"]["rating_confidence"], 0.0)
        self.assertIn("POPULARITY_PRIOR", first["reason_codes"])
        self.assertIn("TMDB_RATING_PRIOR", first["reason_codes"])
        self.assertNotIn("POPULARITY_PRIOR", second["reason_codes"])

    def test_no_positive_profile_is_cold_start(self):
        rows = [movie(1)]
        result = p.rank_movies(rows, {}, limit=1)
        self.assertTrue(result["cold_start"])
        self.assertEqual(result["profile_seed_count"], 0)
        self.assertIn("COLD_START_PRIOR", result["items"][0]["reason_codes"])

    def test_profile_omitted_uses_full_input_catalog(self):
        seed = movie(1, genres=["科幻"], director_ids=[7])
        rows = [seed, movie(2, genres=["科幻"], director_ids=[7])]
        result = p.rank_movies(rows, {"1": {"favorite": True}}, limit=2)
        self.assertFalse(result["cold_start"])
        self.assertEqual(result["profile_seed_count"], 1)
        self.assertEqual(result["candidate_count"], 2)
        self.assertIn(2, ids(result))

    def test_profile_and_state_are_not_mutated(self):
        seed = movie(1, genres=["科幻"], director_ids=[7], overview="space")
        rows = [movie(2, genres=["科幻"], director_ids=[7], overview="space")]
        state = {"1": {"favorite": True}}
        before_rows, before_seed = copy.deepcopy(rows), copy.deepcopy(seed)
        before_state = copy.deepcopy(state)
        p.rank_movies(rows, state, profile_movies=[seed], limit=1)
        self.assertEqual(rows, before_rows)
        self.assertEqual(seed, before_seed)
        self.assertEqual(state, before_state)

    def test_empty_profile_does_not_fall_back_to_full_catalog(self):
        rows = [movie(2, genres=["科幻"])]
        result = p.rank_movies(
            rows, {"1": {"favorite": True}}, profile_movies=[], limit=1)
        self.assertTrue(result["cold_start"])
        self.assertEqual(result["items"][0]["components"]["preference"], 0.0)
        self.assertIn("COLD_START_PRIOR", result["items"][0]["reason_codes"])

    def test_zero_weight_profile_is_cold_start(self):
        rows = [movie(2, genres=["科幻"])]
        result = p.rank_movies(
            rows, {"1": {"favorite": True}},
            profile_movies=[movie(1, genres=["科幻"])],
            weights={"favorite": 0.0, "want": 0.0, "rating": 0.0}, limit=1)
        self.assertTrue(result["cold_start"])
        self.assertEqual(result["profile_seed_count"], 0)

    def test_state_change_affects_next_independent_call(self):
        seed = movie(1, genres=["科幻"], director_ids=[7])
        rows = [
            movie(2, genres=["科幻"], director_ids=[7]),
            movie(3, genres=["剧情"], director_ids=[8]),
        ]
        first = p.rank_movies(rows, {"1": {"favorite": True}},
                              profile_movies=[seed], limit=2)
        second = p.rank_movies(
            rows, {"1": {"favorite": True}, "2": {"not_interested": True}},
            profile_movies=[seed], limit=2)
        self.assertIn(2, ids(first))
        self.assertNotIn(2, ids(second))
        self.assertEqual(ids(second), [3])

    def test_ties_are_stable_by_movie_id_and_inputs_are_not_mutated(self):
        rows = [
            movie(9, catalog_popularity=None, tmdb_rating=None, vote_count=None),
            movie(2, catalog_popularity=None, tmdb_rating=None, vote_count=None),
            movie(5, catalog_popularity=None, tmdb_rating=None, vote_count=None),
        ]
        before_rows = copy.deepcopy(rows)
        before_state = {"1": {"favorite": True}}
        result = p.rank_movies(rows, before_state, limit=3)
        self.assertEqual(ids(result), [2, 5, 9])
        self.assertEqual(rows, before_rows)
        self.assertEqual(before_state, {"1": {"favorite": True}})

    def test_active_non_adult_only_and_candidate_count(self):
        rows = [
            movie(1), movie(2, catalog_status="hidden"),
            movie(3, is_adult=True), movie(4),
        ]
        result = p.rank_movies(rows, {}, limit=10)
        self.assertEqual(ids(result), [1, 4])
        self.assertEqual(result["candidate_count"], 2)

    def test_filters_and_limit_reduce_candidate_count(self):
        rows = [movie(1), movie(2, genres=["科幻"]), movie(3), movie(4, genres=["喜剧"])]
        result = p.rank_movies(
            rows, {}, filters={"genres_any": ["剧情"]}, limit=1)
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(len(result["items"]), 1)
        self.assertIn(result["items"][0]["movie_id"], {1, 3})

    def test_empty_genres_get_a_bounded_missing_penalty(self):
        rows = [
            movie(1, genres=["剧情"], catalog_popularity=None, tmdb_rating=None,
                  vote_count=None),
            movie(2, genres=["剧情"], catalog_popularity=None, tmdb_rating=None,
                  vote_count=None),
            movie(3, genres=[], catalog_popularity=None, tmdb_rating=None,
                  vote_count=None),
        ]
        result = p.rank_movies(rows, {}, limit=2)
        self.assertEqual(ids(result), [1, 3])
        empty = item_for(result, 3)
        self.assertAlmostEqual(empty["diversity_penalty"],
                               p.MISSING_GENRE_PENALTY, places=8)
        self.assertIn("MISSING_GENRE_PENALTY", empty["reason_codes"])
        self.assertLessEqual(item_for(result, 1)["diversity_penalty"],
                             p.MAX_DIVERSITY_PENALTY)

    def test_output_contract_and_bounded_components(self):
        rows = [movie(2, catalog_popularity=1.0, tmdb_rating=10.0,
                      vote_count=10 ** 9, genres=["科幻"])]
        result = p.rank_movies(rows, {}, limit=1)
        self.assertEqual(result["mode"], p.MODE)
        self.assertEqual(result["paid_model_tokens"], 0)
        self.assertEqual(result["recommendation_version"], p.RECOMMENDATION_VERSION)
        item = result["items"][0]
        for key in ("movie_id", "title", "score", "components", "reasons",
                    "reason_codes", "evidence", "genres", "diversity_penalty",
                    "selection_score", "recommendation_version"):
            self.assertIn(key, item)
        self.assertIsInstance(item["reason_codes"], list)
        self.assertIsInstance(item["evidence"], list)
        self.assertTrue(all(math.isfinite(value) for value in item["components"].values()))
        self.assertLessEqual(item["components"]["preference"], 1.0 + 1e-9)
        self.assertGreaterEqual(item["components"]["negative"], -0.6 - 1e-9)
        self.assertLessEqual(item["diversity_penalty"], p.MAX_DIVERSITY_PENALTY)
        self.assertGreaterEqual(item["score"], -1.1)
        self.assertLessEqual(item["score"], 1.2)

    def test_huge_numeric_values_stay_finite(self):
        rows = [movie(
            1, catalog_popularity=10 ** 400, tmdb_rating=8.0,
            vote_count=10 ** 400, overview="x" * 5000)]
        result = p.rank_movies(rows, {}, limit=1)
        item = result["items"][0]
        self.assertEqual(item["components"]["baseline"], 0.0)
        self.assertAlmostEqual(item["components"]["rating_confidence"], 0.04, places=8)
        self.assertTrue(math.isfinite(item["score"]))
        self.assertTrue(all(math.isfinite(value) for value in item["components"].values()))
        prior = next(ev for ev in item["evidence"] if ev["match"] == "TMDB_RATING_PRIOR")
        self.assertEqual(prior["confidence"], 1.0)

    def test_evidence_fields_values_and_seed_links_are_real(self):
        seed = movie(
            1, genres=["科幻"], director_ids=[7], actor_ids=[9],
            release_year=2010, original_language="en", overview="space travel")
        candidate = movie(
            2, genres=["科幻"], director_ids=[7], actor_ids=[9],
            release_year=2010, original_language="en", overview="space travel")
        result = p.rank_movies(
            [candidate], {"1": {"favorite": True}},
            profile_movies=[seed], limit=1)
        item = result["items"][0]
        by_field = {ev["field"]: ev for ev in item["evidence"]
                    if ev.get("seed_movie_id") == 1}
        self.assertIn("科幻", by_field["genres"]["value"])
        self.assertEqual(by_field["release_year"]["value"], [2010, 2010])
        self.assertEqual(by_field["director_ids"]["value"], [7])
        self.assertEqual(by_field["actor_ids"]["value"], [9])
        self.assertTrue(by_field["overview"]["value"])
        self.assertIn("线索", by_field["overview"]["note"])
        self.assertTrue(all(ev["source"] in ("CATALOG", "USER_BEHAVIOR")
                            for ev in item["evidence"]))
        self.assertTrue(all(math.isfinite(ev["contribution"])
                            for ev in item["evidence"]))
        self.assertTrue(any("同一位导演" in reason for reason in item["reasons"]))
        self.assertTrue(any("简介词面" in reason for reason in item["reasons"]))
        self.assertNotIn("user_id", str(item["evidence"]))
        self.assertNotIn("password", str(item["evidence"]).lower())

    def test_score_and_evidence_contributions_are_consistent(self):
        seed = movie(1, genres=["科幻"], director_ids=[7], overview="space travel")
        candidate = movie(
            2, genres=["科幻"], director_ids=[7], overview="space travel")
        result = p.rank_movies(
            [candidate], {"1": {"favorite": True}},
            profile_movies=[seed], limit=1)
        item = result["items"][0]
        self.assertAlmostEqual(
            sum(ev["contribution"] for ev in item["evidence"]),
            item["score"], places=6)

    def test_profile_features_only_for_behaviour_seeds_and_reused(self):
        seed = movie(1, genres=["科幻"], overview="space travel")
        idle = movie(50, genres=["恐怖"], overview="unused profile tokens")
        rows = [
            movie(2, genres=["科幻"], overview="space travel"),
            movie(3, genres=["剧情"], overview="family"),
        ]
        with mock.patch.object(p, "_features", wraps=p._features) as wrapped:
            result = p.rank_movies(
                rows, {"1": {"favorite": True}},
                profile_movies=[seed, idle], limit=2)
        titles = [call.args[0]["title"] for call in wrapped.call_args_list]
        self.assertNotIn(idle["title"], titles)
        self.assertEqual(titles.count(seed["title"]), 1)
        self.assertEqual(result["profile_seed_count"], 1)

    def test_cold_start_skips_similarity_and_overview_tokenisation(self):
        rows = [
            movie(1, overview="space travel"),
            movie(2, overview="family"),
        ]
        with mock.patch.object(
                p, "_overview_tokens",
                side_effect=AssertionError("cold start tokenised overview")):
            result = p.rank_movies(rows, {}, limit=2)
        self.assertTrue(result["cold_start"])
        self.assertEqual(result["items"][0]["components"]["preference"], 0.0)
        self.assertIn("genres", result["items"][0])


class PipelineValidationTests(unittest.TestCase):
    def test_invalid_ids_and_duplicates_are_rejected(self):
        for bad_id in (0, -1, "1", True):
            with self.subTest(movie_id=bad_id):
                with self.assertRaises(ValueError):
                    p.rank_movies([movie(bad_id)], {}, limit=1)
        with self.assertRaises(ValueError):
            p.rank_movies([movie(1), movie(1)], {}, limit=1)
        with self.assertRaises(ValueError):
            p.rank_movies([], {"1": {"favorite": True}},
                          profile_movies=[movie(1), movie(1)], limit=1)

    def test_invalid_parameters_are_rejected(self):
        rows = [movie(1)]
        cases = [
            {"limit": 0}, {"limit": 51}, {"limit": True},
            {"weights": {}}, {"weights": {"favorite": 1, "want": 2}},
            {"weights": {"favorite": float("nan"), "want": 2, "rating": 1}},
            {"weights": {"favorite": 11, "want": 2, "rating": 1}},
            {"filters": {"unknown": 1}},
            {"filters": {"_genre_groups": [[]]}},
            {"filters": {"_year_groups": [[[2000, 1990]]]}},
            {"allow_watched": 1},
        ]
        for kw in cases:
            with self.subTest(kw=kw):
                with self.assertRaises(ValueError):
                    p.rank_movies(rows, {}, limit=1 if "limit" not in kw else kw["limit"], **{
                        key: value for key, value in kw.items() if key != "limit"})

    def test_non_object_movies_state_and_profile_are_rejected(self):
        with self.assertRaises(ValueError):
            p.rank_movies({}, {}, limit=1)
        with self.assertRaises(ValueError):
            p.rank_movies([movie(1)], [], limit=1)
        with self.assertRaises(ValueError):
            p.rank_movies([movie(1)], {}, profile_movies={}, limit=1)

    def test_malformed_state_entries_are_ignored(self):
        rows = [movie(1), movie(2)]
        result = p.rank_movies(
            rows, {"1": "bad", "bad": {"favorite": True}}, limit=2)
        self.assertEqual(result["profile_seed_count"], 0)
        self.assertEqual(result["candidate_count"], 2)


if __name__ == "__main__":
    unittest.main()