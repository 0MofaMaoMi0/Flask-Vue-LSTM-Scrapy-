"""Offline contract tests for tmdb_supplement.normalize (no network/database)."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog.tmdb_supplement.normalize import (  # noqa: E402
    IdentityConflict,
    InvalidInput,
    NonJsonNumber,
    UpstreamError,
    normalize_movie,
)

FETCHED = "2026-11-16T12:00:00+08:00"
JOB = "synthetic-normalize-001"


def full_payload():
    return {
        "id": 100,
        "title": "Synthetic Film",
        "original_title": "Synthetic Film Original",
        "release_date": "2026-01-02",
        "runtime": 120,
        "original_language": "en",
        "overview": "Synthetic overview.",
        "genres": [{"id": 18, "name": "剧情"}],
        "imdb_id": "tt1234567",
        "poster_path": "/poster.jpg",
        "backdrop_path": "/backdrop.png",
        "adult": False,
        "budget": 1000,
        "revenue": 2000,
        "production_countries": [
            {"iso_3166_1": "US", "name": "United States"},
            {"iso_3166_1": "GB", "name": "United Kingdom"},
        ],
        "production_companies": [
            {"id": 10, "name": "Studio A", "origin_country": "US", "logo_path": "/logo.png"}
        ],
        "spoken_languages": [
            {"iso_639_1": "en", "english_name": "English", "name": "English"},
            {"iso_639_1": "fr", "english_name": "French", "name": "Français"},
        ],
        "keywords": {
            "keywords": [
                {"id": 11, "name": "prison"},
                {"id": 12, "name": "hope"},
            ]
        },
        "release_dates": {
            "results": [
                {
                    "iso_3166_1": "US",
                    "release_dates": [
                        {
                            "certification": "R",
                            "note": "",
                            "iso_639_1": "en",
                            "release_date": "2026-01-02T03:04:05+08:00",
                            "type": 3,
                        },
                        {
                            "certification": "R",
                            "note": "",
                            "iso_639_1": "en",
                            "release_date": "2026-01-02T03:04:05+08:00",
                            "type": 3,
                        },
                    ],
                },
                {
                    "iso_3166_1": "GB",
                    "release_dates": [
                        {
                            "certification": "15",
                            "note": "London",
                            "iso_639_1": "en",
                            "release_date": "2026-01-03",
                            "type": 1,
                        }
                    ],
                },
            ]
        },
        "credits": {
            "cast": [
                {
                    "id": 501,
                    "credit_id": "cast-1",
                    "name": "Actor One",
                    "character": "Role A",
                    "order": 0,
                    "profile_path": "/actor.jpg",
                },
                {
                    "id": 501,
                    "credit_id": "cast-2",
                    "name": "Actor One",
                    "character": "Role B",
                    "order": 1,
                },
            ],
            "crew": [
                {
                    "id": 501,
                    "credit_id": "crew-1",
                    "name": "Actor One",
                    "job": "Director",
                    "department": "Directing",
                },
                {
                    "id": 502,
                    "credit_id": "crew-2",
                    "name": "Editor Two",
                    "job": "Editor",
                    "department": "Editing",
                },
            ],
        },
        "vote_average": 8.7,
        "vote_count": 100,
        "popularity": 12.5,
    }


def call(payload=None, *, movie_id=1, tmdb_id=100, fetched_at=FETCHED, job_id=JOB):
    return normalize_movie(
        full_payload() if payload is None else payload,
        movie_id=movie_id,
        tmdb_id=tmdb_id,
        fetched_at=fetched_at,
        job_id=job_id,
    )


class NormalizeContractTests(unittest.TestCase):
    def test_full_payload_all_topics_and_canonical_hash(self):
        payload = full_payload()
        doc = call(payload)
        self.assertEqual(doc["schema_version"], "tmdb-supplement-v1")
        self.assertEqual(doc["movie_id"], 1)
        self.assertEqual(doc["tmdb_id"], 100)
        self.assertEqual(doc["fetched_at"], "2026-11-16T04:00:00+00:00")
        self.assertEqual(doc["job_id"], JOB)
        expected_hash = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(doc["payload_sha256"], expected_hash)
        self.assertEqual(
            doc["field_status"],
            {
                "base": "PRESENT",
                "countries": "PRESENT",
                "companies": "PRESENT",
                "spoken_languages": "PRESENT",
                "keywords": "PRESENT",
                "releases": "PRESENT",
                "credits": "PRESENT",
                "metrics": "PRESENT",
            },
        )
        self.assertEqual(doc["facts"]["base"]["id"], 100)
        self.assertEqual(doc["facts"]["base"]["runtime"], 120)
        self.assertNotIn("runtime_minutes", doc["facts"]["base"])
        self.assertEqual(doc["facts"]["base"]["budget_known"], True)
        self.assertEqual(doc["facts"]["base"]["revenue_known"], True)
        self.assertNotIn("profit", doc["facts"]["base"])
        self.assertEqual([c["iso_3166_1"] for c in doc["facts"]["countries"]], ["US", "GB"])
        self.assertEqual(doc["facts"]["companies"][0]["origin_country"], "US")
        self.assertEqual([x["iso_639_1"] for x in doc["facts"]["spoken_languages"]], ["en", "fr"])
        self.assertEqual([x["id"] for x in doc["facts"]["keywords"]], [11, 12])
        # Exact duplicate release row is deduped, not duplicated.
        us_releases = doc["facts"]["releases"][0]["release_dates"]
        self.assertEqual(len(us_releases), 1)
        self.assertEqual(us_releases[0]["release_date_utc"], "2026-01-01T19:04:05+00:00")
        self.assertEqual(us_releases[0]["release_date_raw"], "2026-01-02T03:04:05+08:00")
        self.assertEqual(us_releases[0]["precision"], "second")
        self.assertEqual(doc["facts"]["releases"][0]["iso_3166_1"], "US")
        # Same person keeps multiple cast/crew credits.
        self.assertEqual(len(doc["facts"]["credits"]), 4)
        self.assertEqual(
            [c["credit_id"] for c in doc["facts"]["credits"]],
            ["cast-1", "cast-2", "crew-1", "crew-2"],
        )
        self.assertEqual(doc["facts"]["credits"][0]["person_id"], 501)
        self.assertEqual(doc["facts"]["credits"][0]["order"], 0)
        self.assertEqual(doc["facts"]["credits"][2]["job"], "Director")
        self.assertEqual(doc["facts"]["metrics"], {"vote_average": 8.7, "vote_count": 100, "popularity": 12.5})
        self.assertEqual(doc["issues"], [])

    def test_absent_append_keys_are_not_fetched_root_fields_are_missing(self):
        payload = full_payload()
        del payload["keywords"]
        del payload["release_dates"]
        del payload["credits"]
        del payload["production_countries"]
        del payload["production_companies"]
        del payload["spoken_languages"]
        del payload["vote_average"]
        del payload["vote_count"]
        del payload["popularity"]
        doc = call(payload)
        self.assertEqual(doc["field_status"]["base"], "PRESENT")
        self.assertEqual(doc["field_status"]["countries"], "MISSING")
        self.assertEqual(doc["field_status"]["companies"], "MISSING")
        self.assertEqual(doc["field_status"]["spoken_languages"], "MISSING")
        self.assertEqual(doc["field_status"]["keywords"], "NOT_FETCHED")
        self.assertEqual(doc["field_status"]["releases"], "NOT_FETCHED")
        self.assertEqual(doc["field_status"]["credits"], "NOT_FETCHED")
        self.assertEqual(doc["field_status"]["metrics"], "MISSING")
        self.assertEqual(doc["facts"]["keywords"], [])
        self.assertEqual(doc["facts"]["releases"], [])
        self.assertEqual(doc["facts"]["credits"], [])

    def test_null_append_keys_are_missing_not_empty(self):
        payload = full_payload()
        payload["keywords"] = None
        payload["release_dates"] = None
        payload["credits"] = None
        payload["production_countries"] = None
        doc = call(payload)
        self.assertEqual(doc["field_status"]["keywords"], "MISSING")
        self.assertEqual(doc["field_status"]["releases"], "MISSING")
        self.assertEqual(doc["field_status"]["credits"], "MISSING")
        self.assertEqual(doc["field_status"]["countries"], "MISSING")

    def test_explicit_empty_collections_are_empty(self):
        payload = full_payload()
        payload["production_countries"] = []
        payload["production_companies"] = []
        payload["spoken_languages"] = []
        payload["keywords"] = {"keywords": []}
        payload["release_dates"] = {"results": []}
        payload["credits"] = {"cast": [], "crew": []}
        doc = call(payload)
        for name in ("countries", "companies", "spoken_languages", "keywords", "releases", "credits"):
            self.assertEqual(doc["field_status"][name], "EMPTY", name)
            self.assertEqual(doc["facts"][name], [], name)

    def test_nested_nulls_are_missing(self):
        payload = full_payload()
        payload["keywords"] = {"keywords": None}
        payload["release_dates"] = {"results": None}
        payload["credits"] = {"cast": None, "crew": None}
        doc = call(payload)
        self.assertEqual(doc["field_status"]["keywords"], "MISSING")
        self.assertEqual(doc["field_status"]["releases"], "MISSING")
        self.assertEqual(doc["field_status"]["credits"], "MISSING")

    def test_append_errors_are_failed_not_empty(self):
        payload = full_payload()
        payload["keywords"] = {"success": False, "status_code": 500}
        payload["release_dates"] = {"status_code": 429, "status_message": "rate limited"}
        payload["credits"] = {"success": False}
        doc = call(payload)
        for name in ("keywords", "releases", "credits"):
            self.assertEqual(doc["field_status"][name], "FAILED", name)
            self.assertEqual(doc["facts"][name], [], name)
        codes = {x["code"] for x in doc["issues"]}
        self.assertIn("UPSTREAM_SUBRESOURCE_FAILED", codes)

    def test_partial_invalid_country_keeps_valid_fact_and_marks_failed(self):
        payload = full_payload()
        payload["production_countries"] = [
            {"iso_3166_1": "US", "name": "United States"},
            {"iso_3166_1": "USA", "name": "Invalid"},
        ]
        doc = call(payload)
        self.assertEqual(doc["field_status"]["countries"], "FAILED")
        self.assertEqual(doc["facts"]["countries"], [{"iso_3166_1": "US", "name": "United States"}])
        self.assertTrue(any(x["code"] == "PARTIAL_INVALID" for x in doc["issues"]))

    def test_ruby_hash_release_language_uses_explicit_code(self):
        payload = full_payload()
        raw = ('{"folded_name"=>"English", "english_name"=>"English", '
               '"iso_639_1"=>"en", "native_name"=>"English"}')
        payload["release_dates"] = {"results": [
            {"iso_3166_1": "AU", "release_dates": [
                {"type": 3, "release_date": "2026-01-02", "iso_639_1": raw},
            ]},
        ]}
        doc = call(payload)
        self.assertEqual(doc["field_status"]["releases"], "PRESENT")
        row = doc["facts"]["releases"][0]["release_dates"][0]
        self.assertEqual(row["iso_639_1"], "en")
        converted = [x for x in doc["issues"] if x["code"] == "RELEASE_LANGUAGE_CONVERTED"]
        self.assertEqual(len(converted), 1)
        self.assertEqual(converted[0]["raw"], raw)
        self.assertEqual(converted[0]["severity"], "warning")
        self.assertEqual(converted[0]["region"], "AU")

    def test_unrecognised_release_language_still_fails(self):
        for value in ("Mandarin", "PT-BR", "-", '{"native_name"=>"Mandarin"}'):
            payload = full_payload()
            payload["release_dates"] = {"results": [
                {"iso_3166_1": "AU", "release_dates": [
                    {"type": 3, "release_date": "2026-01-02", "iso_639_1": value},
                ]},
            ]}
            doc = call(payload)
            self.assertEqual(doc["field_status"]["releases"], "FAILED", value)
            self.assertTrue(
                any(x["code"] == "INVALID_RELEASE_LANGUAGE" for x in doc["issues"]), value)

    def test_only_production_countries_are_used(self):
        payload = full_payload()
        payload["origin_country"] = ["FR"]
        payload["production_companies"][0]["origin_country"] = "FR"
        payload["production_countries"] = [{"iso_3166_1": "US", "name": "United States"}]
        doc = call(payload)
        self.assertEqual(doc["facts"]["countries"], [{"iso_3166_1": "US", "name": "United States"}])
        self.assertEqual(doc["facts"]["companies"][0]["origin_country"], "FR")

    def test_language_code_is_not_guessed_from_names(self):
        payload = full_payload()
        payload["spoken_languages"] = [
            {"iso_639_1": "", "english_name": "English", "name": "English"}
        ]
        doc = call(payload)
        self.assertEqual(doc["facts"]["spoken_languages"], [])
        self.assertEqual(doc["field_status"]["spoken_languages"], "FAILED")
        self.assertEqual(doc["facts"]["base"]["original_language"], "en")
        self.assertIn("INVALID_LANGUAGE_CODE", {x["code"] for x in doc["issues"]})

    def test_keyword_dedupe_and_conflict(self):
        payload = full_payload()
        payload["keywords"] = {
            "keywords": [
                {"id": 11, "name": "prison"},
                {"id": 11, "name": "prison"},
            ]
        }
        doc = call(payload)
        self.assertEqual(doc["field_status"]["keywords"], "PRESENT")
        self.assertEqual(doc["facts"]["keywords"], [{"id": 11, "name": "prison"}])
        payload["keywords"]["keywords"][1]["name"] = "different"
        doc = call(payload)
        self.assertEqual(doc["field_status"]["keywords"], "FAILED")
        self.assertEqual(doc["facts"]["keywords"], [{"id": 11, "name": "prison"}])

    def test_same_person_multiple_credits_are_preserved(self):
        doc = call()
        self.assertEqual(
            [(x["role_type"], x["credit_id"]) for x in doc["facts"]["credits"]],
            [
                ("cast", "cast-1"),
                ("cast", "cast-2"),
                ("crew", "crew-1"),
                ("crew", "crew-2"),
            ],
        )
        self.assertEqual(doc["facts"]["credits"][1]["character"], "Role B")
        self.assertEqual(doc["facts"]["credits"][2]["department"], "Directing")

    def test_credit_identical_duplicate_is_deduped(self):
        payload = full_payload()
        payload["credits"]["cast"].append(copy.deepcopy(payload["credits"]["cast"][0]))
        doc = call(payload)
        self.assertEqual(doc["field_status"]["credits"], "PRESENT")
        self.assertEqual(len(doc["facts"]["credits"]), 4)
        self.assertEqual(
            [x["credit_id"] for x in doc["facts"]["credits"]],
            ["cast-1", "cast-2", "crew-1", "crew-2"],
        )

    def test_credit_conflict_keeps_first_and_fails_topic(self):
        payload = full_payload()
        payload["credits"]["cast"].append(
            {"id": 501, "credit_id": "cast-1", "name": "Actor One", "character": "Different", "order": 0}
        )
        doc = call(payload)
        self.assertEqual(doc["field_status"]["credits"], "FAILED")
        self.assertEqual(doc["facts"]["credits"][0]["character"], "Role A")
        self.assertIn("DUPLICATE_CREDIT_CONFLICT", {x["code"] for x in doc["issues"]})

    def test_same_credit_id_across_cast_and_crew_boundary_is_kept(self):
        payload = full_payload()
        payload["credits"]["crew"].append(
            {
                "id": 501,
                "credit_id": "cast-1",
                "name": "Actor One",
                "job": "Producer",
                "department": "Production",
            }
        )
        doc = call(payload)
        self.assertEqual(doc["field_status"]["credits"], "PRESENT")
        self.assertEqual(
            [(x["role_type"], x["credit_id"]) for x in doc["facts"]["credits"]],
            [
                ("cast", "cast-1"),
                ("cast", "cast-2"),
                ("crew", "crew-1"),
                ("crew", "crew-2"),
                ("crew", "cast-1"),
            ],
        )

    def test_missing_crew_preserves_valid_cast(self):
        payload = full_payload()
        del payload["credits"]["crew"]
        doc = call(payload)
        self.assertEqual(doc["field_status"]["credits"], "FAILED")
        self.assertEqual(len(doc["facts"]["credits"]), 2)
        self.assertTrue(all(x["role_type"] == "cast" for x in doc["facts"]["credits"]))
        self.assertIn("INVALID_CREDITS", {x["code"] for x in doc["issues"]})

    def test_valid_zero_metrics_and_unknown_money(self):
        payload = full_payload()
        payload.update(
            vote_average=0,
            vote_count=0,
            popularity=0,
            budget=0,
            revenue=0,
        )
        doc = call(payload)
        self.assertEqual(doc["field_status"]["metrics"], "PRESENT")
        self.assertEqual(doc["facts"]["metrics"], {"vote_average": 0, "vote_count": 0, "popularity": 0})
        self.assertEqual(doc["facts"]["base"]["budget"], 0)
        self.assertEqual(doc["facts"]["base"]["revenue"], 0)
        self.assertFalse(doc["facts"]["base"]["budget_known"])
        self.assertFalse(doc["facts"]["base"]["revenue_known"])
        self.assertNotIn("profit", doc["facts"]["base"])

    def test_invalid_metrics_are_null_and_failed(self):
        payload = full_payload()
        payload.update(vote_average="NaN", vote_count=-1, popularity=-2)
        doc = call(payload)
        self.assertEqual(doc["field_status"]["metrics"], "FAILED")
        self.assertEqual(
            doc["facts"]["metrics"],
            {"vote_average": None, "vote_count": None, "popularity": None},
        )
        self.assertGreaterEqual(len(doc["issues"]), 3)
        self.assertIn("INVALID_VOTE_COUNT", {x["code"] for x in doc["issues"]})

    def test_id_conflict_is_not_repaired_by_title(self):
        payload = full_payload()
        payload["id"] = 101
        with self.assertRaises(IdentityConflict) as ctx:
            call(payload, tmdb_id=100)
        self.assertEqual(ctx.exception.code, "TMDB_ID_MISMATCH")

    def test_boolean_ids_are_rejected(self):
        payload = full_payload()
        with self.assertRaises(InvalidInput):
            call(payload, movie_id=True)
        with self.assertRaises(InvalidInput):
            call(payload, tmdb_id=True)
        payload["id"] = True
        with self.assertRaises(InvalidInput):
            call(payload)

    def test_bad_money_is_null_and_base_failed(self):
        payload = full_payload()
        payload["budget"] = -1
        payload["revenue"] = True
        doc = call(payload)
        self.assertEqual(doc["field_status"]["base"], "FAILED")
        self.assertIsNone(doc["facts"]["base"]["budget"])
        self.assertIsNone(doc["facts"]["base"]["revenue"])
        self.assertFalse(doc["facts"]["base"]["budget_known"])
        self.assertNotIn("profit", doc["facts"]["base"])

    def test_release_precision_timezone_and_dedupe(self):
        payload = full_payload()
        payload["release_dates"] = {
            "results": [
                {
                    "iso_3166_1": "US",
                    "release_dates": [
                        {"type": 3, "release_date": "2026-01-02T03:04:05+08:00"},
                        {"type": 3, "release_date": "2026-01-02T03:04:05+08:00"},
                    ],
                },
                {
                    "iso_3166_1": "GB",
                    "release_dates": [{"type": 1, "release_date": "2026-01-03"}],
                },
            ]
        }
        doc = call(payload)
        self.assertEqual(doc["field_status"]["releases"], "PRESENT")
        us = doc["facts"]["releases"][0]["release_dates"]
        gb = doc["facts"]["releases"][1]["release_dates"]
        self.assertEqual(len(us), 1)
        self.assertEqual(us[0]["release_date"], "2026-01-01")
        self.assertEqual(us[0]["release_date_utc"], "2026-01-01T19:04:05+00:00")
        self.assertEqual(us[0]["precision"], "second")
        self.assertEqual(us[0]["dedupe_key"], hashlib.sha256(
            json.dumps(
                {
                    "iso_3166_1": "US",
                    "type": 3,
                    "release_date_raw": "2026-01-02T03:04:05+08:00",
                    "certification": None,
                    "note": None,
                    "iso_639_1": None,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest())
        self.assertEqual(gb[0]["release_date"], "2026-01-03")
        self.assertIsNone(gb[0]["release_date_utc"])
        self.assertEqual(gb[0]["precision"], "day")

    def test_partial_bad_release_keeps_valid_release(self):
        payload = full_payload()
        payload["release_dates"] = {
            "results": [
                {
                    "iso_3166_1": "US",
                    "release_dates": [
                        {"type": 3, "release_date": "2026-01-02"},
                        {"type": 99, "release_date": "2026-01-02"},
                    ],
                }
            ]
        }
        doc = call(payload)
        self.assertEqual(doc["field_status"]["releases"], "FAILED")
        self.assertEqual(len(doc["facts"]["releases"]), 1)
        self.assertEqual(len(doc["facts"]["releases"][0]["release_dates"]), 1)
        self.assertIn("INVALID_RELEASE_TYPE", {x["code"] for x in doc["issues"]})

    def test_canonical_hash_rejects_nan_and_infinity(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            payload = full_payload()
            payload["unexpected_metric"] = bad
            with self.assertRaises(NonJsonNumber):
                call(payload)

    def test_hash_is_independent_of_dict_key_order(self):
        p1 = full_payload()
        p2 = full_payload()
        reordered = dict(reversed(list(p2.items())))
        self.assertEqual(call(p1)["payload_sha256"], call(reordered)["payload_sha256"])

    def test_fetched_at_requires_timezone_and_converts_to_utc(self):
        doc = call(fetched_at="2026-11-16T12:00:00+08:00")
        self.assertEqual(doc["fetched_at"], "2026-11-16T04:00:00+00:00")
        with self.assertRaises(InvalidInput) as ctx:
            call(fetched_at="2026-11-16T12:00:00")
        self.assertEqual(ctx.exception.code, "FETCHED_AT_WITHOUT_TIMEZONE")

    def test_id_job_and_range_validation(self):
        for kwargs in (
            {"movie_id": 0},
            {"movie_id": True},
            {"movie_id": 9223372036854775808},
            {"tmdb_id": 10_000_000_000},
            {"tmdb_id": True},
            {"job_id": ""},
            {"job_id": "x" * 129},
        ):
            with self.assertRaises(InvalidInput):
                call(**kwargs)

    def test_unknown_metrics_null_are_present(self):
        payload = full_payload()
        payload.update(vote_average=None, vote_count=None, popularity=None)
        doc = call(payload)
        self.assertEqual(doc["field_status"]["metrics"], "PRESENT")
        self.assertEqual(
            doc["facts"]["metrics"],
            {"vote_average": None, "vote_count": None, "popularity": None},
        )
        self.assertEqual(doc["issues"], [])

    def test_missing_title_marks_base_failed_without_crashing(self):
        payload = full_payload()
        del payload["title"]
        doc = call(payload)
        self.assertEqual(doc["field_status"]["base"], "FAILED")
        self.assertIsNone(doc["facts"]["base"]["title"])
        self.assertEqual(doc["facts"]["countries"][0]["iso_3166_1"], "US")

    def test_non_json_python_value_is_rejected(self):
        payload = full_payload()
        payload["unexpected"] = object()
        with self.assertRaises(InvalidInput) as ctx:
            call(payload)
        self.assertEqual(ctx.exception.code, "PAYLOAD_NOT_JSON")

    def test_metric_vote_count_boolean_is_invalid(self):
        payload = full_payload()
        payload["vote_count"] = True
        doc = call(payload)
        self.assertEqual(doc["field_status"]["metrics"], "FAILED")
        self.assertIsNone(doc["facts"]["metrics"]["vote_count"])
        self.assertIn("INVALID_VOTE_COUNT", {x["code"] for x in doc["issues"]})

    def test_root_upstream_error(self):
        payload = full_payload()
        payload["success"] = False
        with self.assertRaises(UpstreamError):
            call(payload)
        payload = full_payload()
        payload["status_code"] = 404
        with self.assertRaises(UpstreamError):
            call(payload)

    def test_duplicate_countries_and_languages_are_deduped(self):
        payload = full_payload()
        payload["production_countries"] = [
            {"iso_3166_1": "US", "name": "United States"},
            {"iso_3166_1": "US", "name": "United States"},
        ]
        payload["spoken_languages"] = [
            {"iso_639_1": "en", "english_name": "English", "name": "English"},
            {"iso_639_1": "en", "english_name": "English", "name": "English"},
        ]
        doc = call(payload)
        self.assertEqual(doc["field_status"]["countries"], "PRESENT")
        self.assertEqual(len(doc["facts"]["countries"]), 1)
        self.assertEqual(doc["field_status"]["spoken_languages"], "PRESENT")
        self.assertEqual(len(doc["facts"]["spoken_languages"]), 1)

    def test_release_millisecond_precision_is_retained(self):
        payload = full_payload()
        payload["release_dates"] = {
            "results": [
                {
                    "iso_3166_1": "US",
                    "release_dates": [
                        {"type": 3, "release_date": "2026-01-02T03:04:05.123Z"}
                    ],
                }
            ]
        }
        doc = call(payload)
        item = doc["facts"]["releases"][0]["release_dates"][0]
        self.assertEqual(item["release_date_raw"], "2026-01-02T03:04:05.123Z")
        self.assertEqual(item["precision"], "millisecond")
        self.assertEqual(item["release_date_utc"], "2026-01-02T03:04:05.123000+00:00")

    def test_release_only_date_does_not_invent_timezone(self):
        payload = full_payload()
        payload["release_dates"] = {
            "results": [
                {"iso_3166_1": "US", "release_dates": [{"type": 1, "release_date": "2026-01-02"}]}
            ]
        }
        doc = call(payload)
        item = doc["facts"]["releases"][0]["release_dates"][0]
        self.assertIsNone(item["release_date_utc"])
        self.assertEqual(item["precision"], "day")

    def test_credits_empty_and_not_fetched_are_distinct(self):
        payload = full_payload()
        payload["credits"] = {"cast": [], "crew": []}
        self.assertEqual(call(payload)["field_status"]["credits"], "EMPTY")
        payload = full_payload()
        del payload["credits"]
        self.assertEqual(call(payload)["field_status"]["credits"], "NOT_FETCHED")


if __name__ == "__main__":
    unittest.main()