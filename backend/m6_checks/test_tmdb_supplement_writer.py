"""Offline unit tests for the TMDB supplement v1 writer kernel.

Every fixture below is synthetic and derived from the documented
``tmdb-supplement-v1`` shape.  Nothing here touches a database, the network,
configuration, a private key or a real catalog; ``apply_plan`` is executed
against a recording fake connection so the assertions stay on SQL shape and
parameters.

Run from ``backend/`` with::

    python -B -m unittest m6_checks.test_tmdb_supplement_writer
"""
import hashlib
import json
import unittest
from datetime import datetime

from catalog.tmdb_supplement import writer

TOPICS = writer.TOPICS
PRESENT_PAYLOAD = "a" * 64
CONFLICT_PAYLOAD = "b" * 64
BASE_TIME = "2026-11-01T00:00:00+00:00"
LATER_TIME = "2026-11-01T20:00:00+00:00"


# --------------------------------------------------------------------- fixtures


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def release_item(**overrides):
    payload = {
        "iso_3166_1": "CN",
        "type": 3,
        "release_date_raw": "1994-06-01",
        "certification": None,
        "note": None,
        "iso_639_1": None,
    }
    payload.update(overrides)
    key = hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()
    return {
        "type": payload["type"],
        "type_name": "Theatrical",
        "release_date": payload["release_date_raw"],
        "release_date_raw": payload["release_date_raw"],
        "release_date_utc": None,
        "precision": "day",
        "certification": payload["certification"],
        "note": payload["note"],
        "iso_639_1": payload["iso_639_1"],
        "dedupe_key": key,
    }


def base_facts():
    return {
        "base": {
            "id": 9001,
            "title": "Synthetic Movie",
            "original_title": "Synthetic Movie",
            "release_date": "1994-06-01",
            "runtime": 100,
            "original_language": "zh",
            "overview": "synthetic overview",
            "genres": [{"id": 18, "name": "剧情"}],
            "imdb_id": None,
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "adult": False,
            "budget": 0,
            "revenue": 0,
            "budget_known": False,
            "revenue_known": False,
        },
        "countries": [{"iso_3166_1": "CN", "name": "中国"}],
        "companies": [
            {"id": 4201, "name": "Company A", "origin_country": "US", "logo_path": None},
            {"id": 4202, "name": "Company B", "origin_country": None, "logo_path": "/logo.png"},
        ],
        "spoken_languages": [
            {"iso_639_1": "zh", "english_name": "Chinese", "name": "中文"},
        ],
        "keywords": [
            {"id": 8801, "name": "关键词甲"},
            {"id": 8802, "name": "关键词乙"},
        ],
        "releases": [
            {"iso_3166_1": "CN", "release_dates": [release_item()]},
        ],
        "credits": [{"credit_id": "c1", "person_id": 99, "role": "cast", "name": "Actor"}],
        "metrics": {"vote_average": 8.0, "vote_count": 100, "popularity": 5.5},
    }


def document(*, movie_id=501, tmdb_id=9001, fetched_at=BASE_TIME, statuses=None,
             facts=None, payload=PRESENT_PAYLOAD, source_record_pk=1001):
    status = {topic: "PRESENT" for topic in TOPICS}
    status.update(statuses or {})
    data = facts if facts is not None else base_facts()
    return {
        "schema_version": "tmdb-supplement-v1",
        "movie_id": movie_id,
        "tmdb_id": tmdb_id,
        "fetched_at": fetched_at,
        "job_id": "job-fixture-1",
        "payload_sha256": payload,
        "field_status": status,
        "facts": data,
        "issues": [],
        "source_record_pk": source_record_pk,
    }


def retained_row(*, movie_id=501, tmdb_id=9001, success_at=BASE_TIME, fetched_at=None,
                 topics=None, payload=PRESENT_PAYLOAD, source_record_pk=1001,
                 attempt_at=None, master=None):
    """Build a ``tmdb_movie_metadata`` row where every topic retains ``payload``."""
    overrides = dict(topics or {})
    statuses, provenance, attempts, success = {}, {}, {}, {}
    for topic in TOPICS:
        entry = {
            "state": "PRESENT",
            "fetched_at": success_at,
            "payload_sha256": payload,
            "source_record_pk": source_record_pk,
        }
        entry.update(overrides.get(topic) or {})
        provenance[topic] = entry
        statuses[topic] = entry["state"]
        success[topic] = entry["fetched_at"]
        attempts[topic] = {
            "status": entry["state"],
            "attempt_at": attempt_at or entry["fetched_at"],
            "source_record_pk": source_record_pk,
            "payload_sha256": payload,
            "error_code": None,
        }
    row = {
        "movie_id": movie_id,
        "tmdb_id": tmdb_id,
        "fetched_at": fetched_at or success_at,
        "field_status_json": canonical(statuses),
        "last_attempt_json": canonical(attempts),
        "provenance_json": canonical(provenance),
        "source_record_pk": source_record_pk,
        "payload_sha256": payload,
    }
    for topic in TOPICS:
        row[writer.SUCCESS_TIME_COLUMNS[topic]] = success[topic]
    if master is not None:
        row["master"] = master
    return row


def naive(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)


def tables(plan):
    return [operation[0] for operation in plan["operations"]]


def operations_for(plan, table):
    return [operation for operation in plan["operations"] if operation[0] == table]


class FakeConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        return None


# ----------------------------------------------------------------- 1 per topic


class PerTopicComparisonTests(unittest.TestCase):
    def test_newer_countries_still_written_when_companies_already_newer(self):
        row = retained_row(
            success_at="2026-11-01T10:00:00+00:00",
            fetched_at=LATER_TIME,
            topics={"companies": {"fetched_at": LATER_TIME}},
        )
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")

        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")

        self.assertEqual("replace", plan["decisions"]["countries"]["action"])
        self.assertEqual("stale", plan["decisions"]["companies"]["action"])
        self.assertEqual(naive("2026-11-01T15:00:00+00:00"),
                         plan["metadata"]["countries_at"])
        self.assertEqual(naive(LATER_TIME), plan["metadata"]["companies_at"])
        self.assertIn("tmdb_movie_countries", tables(plan))
        self.assertNotIn("tmdb_movie_companies", tables(plan))

    def test_global_fetched_at_is_not_a_topic_gate(self):
        row = retained_row(success_at=LATER_TIME, fetched_at=LATER_TIME)
        doc = document(fetched_at="2026-11-01T10:00:00+00:00")
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        self.assertEqual("stale", plan["decisions"]["countries"]["action"])
        self.assertNotIn("tmdb_movie_countries", tables(plan))


# --------------------------------------------------------------------- 2 replace


class ReplaceRuleTests(unittest.TestCase):
    def test_replace_writes_rows_and_provenance(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")

        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        provenance = json.loads(plan["metadata"]["provenance_json"])

        self.assertEqual("replace", plan["decisions"]["countries"]["action"])
        self.assertEqual("PRESENT", provenance["countries"]["state"])
        self.assertEqual("2026-11-01T15:00:00+00:00", provenance["countries"]["fetched_at"])
        self.assertEqual(PRESENT_PAYLOAD, provenance["countries"]["payload_sha256"])
        self.assertEqual(1001, provenance["countries"]["source_record_pk"])
        self.assertEqual(naive("2026-11-01T15:00:00+00:00"),
                         plan["metadata"]["countries_at"])

        upsert = [operation for operation in operations_for(plan, "tmdb_movie_countries")
                  if operation[1] == "upsert"]
        self.assertEqual(1, len(upsert))
        self.assertEqual("upsert", upsert[0][1])
        self.assertEqual({"movie_id": 501, "country_iso2": "CN"},
                         dict(upsert[0][2]))
        self.assertEqual("中国", upsert[0][3]["country_name"])

    def test_base_replace_only_advances_bookkeeping(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00", source_record_pk=1001)
        doc = document(fetched_at="2026-11-01T15:00:00+00:00", source_record_pk=1002)
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        self.assertEqual("replace", plan["decisions"]["base"]["action"])
        self.assertEqual(1002, plan["metadata"]["source_record_pk"])
        self.assertEqual(PRESENT_PAYLOAD, plan["metadata"]["payload_sha256"])
        self.assertNotIn("movies", tables(plan))


# ----------------------------------------------------------------------- 3 EMPTY


class EmptyRuleTests(unittest.TestCase):
    def test_empty_clears_rows_and_advances_provenance(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        facts = base_facts()
        facts["countries"] = []
        doc = document(fetched_at="2026-11-01T15:00:00+00:00",
                       statuses={"countries": "EMPTY"}, facts=facts)

        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        provenance = json.loads(plan["metadata"]["provenance_json"])

        self.assertEqual("clear", plan["decisions"]["countries"]["action"])
        self.assertEqual("EMPTY", provenance["countries"]["state"])
        self.assertEqual(naive("2026-11-01T15:00:00+00:00"),
                         plan["metadata"]["countries_at"])
        deletes = operations_for(plan, "tmdb_movie_countries")
        self.assertEqual(1, len(deletes))
        self.assertEqual("delete_all", deletes[0][1])

    def test_clear_ignores_append_only_metric_history(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        facts = base_facts()
        facts["metrics"] = {"vote_average": None, "vote_count": None, "popularity": None}
        doc = document(fetched_at="2026-11-01T15:00:00+00:00",
                       statuses={"metrics": "EMPTY"}, facts=facts)
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        self.assertEqual("clear", plan["decisions"]["metrics"]["action"])
        self.assertEqual([], operations_for(plan, "tmdb_metric_observations"))


# ------------------------------------------------------- 4 same time same payload


class IdempotencyTests(unittest.TestCase):
    def test_rerun_same_time_same_payload_is_a_true_noop(self):
        row = retained_row(attempt_at=BASE_TIME)
        doc = document(fetched_at=BASE_TIME)

        plan = writer.build_plan(row, doc, now=BASE_TIME)

        self.assertEqual([], plan["operations"])
        for topic in TOPICS:
            self.assertIn(plan["decisions"][topic]["action"], ("noop", "skipped"))

    def test_plan_is_identical_when_built_twice(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")
        first = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        second = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        self.assertEqual(first["operations"], second["operations"])


# --------------------------------------------------- 5 same time different payload


class VersionConflictTests(unittest.TestCase):
    def test_conflict_records_attempt_without_touching_facts(self):
        row = retained_row(success_at=BASE_TIME, attempt_at="2026-11-01T09:00:00+00:00")
        doc = document(fetched_at=BASE_TIME, payload=CONFLICT_PAYLOAD)

        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        status = json.loads(plan["metadata"]["field_status_json"])
        attempts = json.loads(plan["metadata"]["last_attempt_json"])
        provenance = json.loads(plan["metadata"]["provenance_json"])

        self.assertEqual("conflict", plan["decisions"]["countries"]["action"])
        self.assertNotIn("tmdb_movie_countries", tables(plan))
        self.assertEqual(PRESENT_PAYLOAD, provenance["countries"]["payload_sha256"])
        self.assertEqual(naive(BASE_TIME), plan["metadata"]["countries_at"])
        self.assertEqual("FAILED", status["countries"])
        self.assertEqual("TOPIC_VERSION_CONFLICT", attempts["countries"]["error_code"])
        self.assertEqual("FAILED", attempts["countries"]["status"])
        details = attempts["countries"]["conflict_details"]
        self.assertEqual(
            {"retained_payload_sha256", "incoming_payload_sha256",
             "retained_source_record_pk", "incoming_source_record_pk", "attempt_at"},
            set(details))
        self.assertEqual(PRESENT_PAYLOAD, details["retained_payload_sha256"])
        self.assertEqual(CONFLICT_PAYLOAD, details["incoming_payload_sha256"])
        self.assertEqual(1001, details["retained_source_record_pk"])
        self.assertEqual(1001, details["incoming_source_record_pk"])
        self.assertEqual("2026-11-01T21:00:00+00:00", details["attempt_at"])

    def test_conflict_is_not_a_sixth_state_and_stays_out_of_provenance(self):
        row = retained_row(success_at=BASE_TIME)
        doc = document(fetched_at=BASE_TIME, payload=CONFLICT_PAYLOAD)
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        provenance = json.loads(plan["metadata"]["provenance_json"])
        self.assertNotIn("conflict", json.dumps(provenance))
        self.assertEqual({"state": "PRESENT",
                          "fetched_at": BASE_TIME,
                          "payload_sha256": PRESENT_PAYLOAD,
                          "source_record_pk": 1001},
                         provenance["countries"])


# ---------------------------------------------------------------- 6 older response


class OlderResponseTests(unittest.TestCase):
    def test_older_success_never_replaces_or_advances(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00",
                           attempt_at="2026-11-01T09:00:00+00:00")
        doc = document(fetched_at="2026-11-01T08:00:00+00:00")

        plan = writer.build_plan(row, doc, now="2026-11-01T07:00:00+00:00")
        provenance = json.loads(plan["metadata"]["provenance_json"])

        self.assertEqual("stale", plan["decisions"]["countries"]["action"])
        self.assertEqual(naive("2026-11-01T10:00:00+00:00"),
                         plan["metadata"]["countries_at"])
        self.assertEqual("2026-11-01T10:00:00+00:00",
                         provenance["countries"]["fetched_at"])
        self.assertEqual([], plan["operations"])

    def test_newer_attempt_of_old_snapshot_updates_attempt_layer_only(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00",
                           attempt_at="2026-11-01T09:00:00+00:00")
        doc = document(fetched_at="2026-11-01T08:00:00+00:00")

        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        attempts = json.loads(plan["metadata"]["last_attempt_json"])
        provenance = json.loads(plan["metadata"]["provenance_json"])

        self.assertEqual("stale", plan["decisions"]["countries"]["action"])
        self.assertEqual("2026-11-01T21:00:00+00:00", attempts["countries"]["attempt_at"])
        self.assertEqual("PRESENT", attempts["countries"]["status"])
        self.assertEqual("2026-11-01T10:00:00+00:00", provenance["countries"]["fetched_at"])


# ------------------------------------------------------------------- 7 failure


class FailureRuleTests(unittest.TestCase):
    def test_failure_only_touches_attempt_layer(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00",
                           attempt_at="2026-11-01T09:00:00+00:00")
        doc = document(fetched_at="2026-11-01T15:00:00+00:00",
                       statuses={"countries": "FAILED"})

        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        status = json.loads(plan["metadata"]["field_status_json"])
        provenance = json.loads(plan["metadata"]["provenance_json"])

        self.assertEqual("skipped", plan["decisions"]["countries"]["action"])
        self.assertNotIn("tmdb_movie_countries", tables(plan))
        self.assertEqual("FAILED", status["countries"])
        self.assertEqual(naive("2026-11-01T10:00:00+00:00"),
                         plan["metadata"]["countries_at"])
        self.assertEqual("2026-11-01T10:00:00+00:00",
                         provenance["countries"]["fetched_at"])

    def test_not_fetched_keeps_retained_rows(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        doc = document(fetched_at="2026-11-01T15:00:00+00:00",
                       statuses={"keywords": "NOT_FETCHED"})
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        self.assertNotIn("tmdb_movie_keywords", tables(plan))
        self.assertNotIn("tmdb_keywords", tables(plan))


# ------------------------------------------------------- 8 global fetched_at floor


class GlobalFetchedAtTests(unittest.TestCase):
    def test_global_fetched_at_never_moves_backwards(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00", fetched_at=LATER_TIME)
        doc = document(fetched_at="2026-11-01T08:00:00+00:00")
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        self.assertEqual(naive(LATER_TIME), plan["metadata"]["fetched_at"])

    def test_global_fetched_at_advances_for_newer_document(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00", fetched_at="2026-11-01T10:00:00+00:00")
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        self.assertEqual(naive("2026-11-01T15:00:00+00:00"),
                         plan["metadata"]["fetched_at"])


# ---------------------------------------------------------- 9 attempt monotonic


class AttemptMonotonicTests(unittest.TestCase):
    def test_older_attempt_is_ignored(self):
        row = retained_row(success_at=BASE_TIME, attempt_at="2026-11-01T09:00:00+00:00")
        doc = document(fetched_at="2026-11-01T08:00:00+00:00",
                       statuses={"countries": "FAILED"})
        plan = writer.build_plan(row, doc, now="2026-11-01T07:00:00+00:00")
        attempts = json.loads(plan["metadata"]["last_attempt_json"])
        status = json.loads(plan["metadata"]["field_status_json"])
        self.assertEqual("2026-11-01T09:00:00+00:00", attempts["countries"]["attempt_at"])
        self.assertEqual("PRESENT", status["countries"])

    def test_equal_attempt_with_same_payload_is_ignored(self):
        row = retained_row(success_at=BASE_TIME, attempt_at=BASE_TIME)
        doc = document(fetched_at=BASE_TIME)
        plan = writer.build_plan(row, doc, now=BASE_TIME)
        self.assertEqual([], plan["operations"])

    def test_equal_attempt_with_different_payload_is_a_conflict(self):
        row = retained_row(success_at=BASE_TIME, attempt_at=BASE_TIME)
        doc = document(fetched_at=BASE_TIME, payload=CONFLICT_PAYLOAD)
        plan = writer.build_plan(row, doc, now=BASE_TIME)
        attempts = json.loads(plan["metadata"]["last_attempt_json"])
        self.assertEqual("TOPIC_VERSION_CONFLICT", attempts["countries"]["error_code"])

    def test_newer_attempt_wins(self):
        row = retained_row(success_at=BASE_TIME, attempt_at="2026-11-01T05:00:00+00:00")
        doc = document(fetched_at=BASE_TIME)
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        attempts = json.loads(plan["metadata"]["last_attempt_json"])
        self.assertEqual("2026-11-01T21:00:00+00:00", attempts["countries"]["attempt_at"])


# ----------------------------------------------------- 10 partial facts fail-closed


class PartialFactsTests(unittest.TestCase):
    def test_failed_topic_with_partial_facts_writes_no_rows(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        facts = base_facts()
        facts["companies"] = [{"id": 4201, "name": "Partial Co", "origin_country": "US",
                               "logo_path": None}]
        doc = document(fetched_at="2026-11-01T15:00:00+00:00",
                       statuses={"companies": "FAILED"}, facts=facts)
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        self.assertEqual("skipped", plan["decisions"]["companies"]["action"])
        self.assertNotIn("tmdb_companies", tables(plan))
        self.assertNotIn("tmdb_movie_companies", tables(plan))
        self.assertEqual(naive("2026-11-01T10:00:00+00:00"),
                         plan["metadata"]["companies_at"])


# ------------------------------------------------------------------ fingerprints


class ReleaseFingerprintTests(unittest.TestCase):
    def test_formula_matches_manifest(self):
        key = "0123456789abcdef" * 4
        expected = hashlib.sha256(("501" + "\u001f" + key).encode("utf-8")).hexdigest()
        self.assertEqual(expected, writer.release_fingerprint(501, key))
        self.assertEqual(64, len(expected))
        self.assertEqual(expected.lower(), expected)

    def test_same_movie_same_key_is_stable_and_not_the_raw_key(self):
        key = "0123456789abcdef" * 4
        first = writer.release_fingerprint(501, key)
        self.assertEqual(first, writer.release_fingerprint(501, key))
        self.assertNotEqual(key, first)

    def test_different_movies_never_share_a_fingerprint(self):
        key = "0123456789abcdef" * 4
        self.assertNotEqual(writer.release_fingerprint(501, key),
                            writer.release_fingerprint(502, key))

    def test_release_rows_carry_recomputed_fingerprints(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        releases = operations_for(plan, "tmdb_release_records")
        upserts = [operation for operation in releases if operation[1] == "upsert"]
        self.assertEqual(1, len(upserts))
        dedupe_key = doc["facts"]["releases"][0]["release_dates"][0]["dedupe_key"]
        self.assertEqual(writer.release_fingerprint(501, dedupe_key),
                         upserts[0][3]["record_fingerprint"])
        self.assertNotEqual(dedupe_key, upserts[0][3]["record_fingerprint"])

    def test_invalid_dedupe_key_is_rejected(self):
        with self.assertRaises(writer.WriterInputError):
            writer.release_fingerprint(501, "not-a-hash")


# ------------------------------------------------------- dictionary upsert shapes


class RelationshipUpsertTests(unittest.TestCase):
    def test_companies_upsert_dictionary_then_link_without_duplicates(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        dictionaries = [operation for operation in operations_for(plan, "tmdb_companies")
                        if operation[1] == "upsert"]
        links = [operation for operation in operations_for(plan, "tmdb_movie_companies")
                 if operation[1] == "link"]
        self.assertEqual([4201, 4202], [operation[3]["tmdb_company_id"] for operation in dictionaries])
        self.assertEqual(2, len(links))
        self.assertTrue(all(operation[1] == "link" for operation in links))

    def test_keywords_use_dictionary_and_link_tables(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        self.assertEqual(2, len([operation for operation in operations_for(plan, "tmdb_keywords")
                                 if operation[1] == "upsert"]))
        self.assertEqual(2, len([operation for operation in operations_for(plan, "tmdb_movie_keywords")
                                 if operation[1] == "link"]))

    def test_rebuild_produces_identical_operations(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")
        first = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        second = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        self.assertEqual(first["operations"], second["operations"])
        self.assertEqual(len(first["operations"]), len(second["operations"]))


# ------------------------------------------------------------ JSON serialization


class JsonEncodingTests(unittest.TestCase):
    def test_canonical_encoding_is_compact_sorted_and_utf8(self):
        text = writer._canonical({"第二个": "值", "第一个": 1})
        self.assertEqual('{"第一个":1,"第二个":"值"}', text)
        self.assertNotIn(", ", text)
        self.assertNotIn("\\u", text)

    def test_metadata_json_columns_round_trip(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        for column in ("field_status_json", "last_attempt_json", "provenance_json"):
            text = plan["metadata"][column]
            self.assertEqual(canonical(json.loads(text)), text)


# ------------------------------------------------------------------ input errors


class InputValidationTests(unittest.TestCase):
    def test_boolean_movie_id_is_rejected(self):
        with self.assertRaises(writer.WriterInputError):
            writer.decide(None, document(movie_id=True), attempt_at=BASE_TIME)

    def test_fetched_at_without_timezone_is_rejected(self):
        with self.assertRaises(writer.WriterInputError):
            writer.decide(None, document(fetched_at="2026-11-01T00:00:00"),
                          attempt_at=BASE_TIME)

    def test_bad_payload_sha256_is_rejected(self):
        with self.assertRaises(writer.WriterInputError):
            writer.decide(None, document(payload="not-a-sha"), attempt_at=BASE_TIME)

    def test_missing_attempt_at_is_rejected(self):
        with self.assertRaises(writer.WriterInputError):
            writer.decide(None, document(), attempt_at=None)

    def test_unknown_topic_status_is_rejected(self):
        with self.assertRaises(writer.WriterInputError):
            writer.decide(None, document(statuses={"countries": "CONFLICT"}),
                          attempt_at=BASE_TIME)

    def test_movie_id_mismatch_is_rejected(self):
        row = retained_row()
        with self.assertRaises(writer.WriterInputError):
            writer.build_plan(row, document(movie_id=777), now=BASE_TIME)


# ------------------------------------------------------------------ apply_plan


class ApplyPlanTests(unittest.TestCase):
    def test_apply_executes_exactly_the_planned_operations(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")

        connection = FakeConnection()
        result = writer.apply_plan(connection, plan)

        self.assertEqual(len(plan["operations"]), result["executed"])
        self.assertEqual(len(plan["operations"]), len(connection.calls))
        self.assertEqual({"executed": result["executed"],
                          "by_table": result["by_table"]}, result)

    def test_apply_renders_upsert_with_duplicate_key_update(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        connection = FakeConnection()
        writer.apply_plan(connection, plan)

        statements = [(sql, params) for sql, params in connection.calls]
        country = [item for item in statements
                   if "INSERT INTO tmdb_movie_countries" in item[0]]
        self.assertEqual(1, len(country))
        self.assertIn("ON DUPLICATE KEY UPDATE", country[0][0])
        self.assertEqual((501, "CN", "中国"), country[0][1])

    def test_apply_renders_dictionary_link_subquery(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        connection = FakeConnection()
        writer.apply_plan(connection, plan)

        links = [(sql, params) for sql, params in connection.calls
                 if "INSERT INTO tmdb_movie_companies" in sql]
        self.assertEqual(2, len(links))
        self.assertIn("SELECT %s, company_id FROM tmdb_companies", links[0][0])
        self.assertEqual((501, 4201), links[0][1])

    def test_apply_renders_cleanup_for_stale_rows(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        connection = FakeConnection()
        writer.apply_plan(connection, plan)

        cleanup = [(sql, params) for sql, params in connection.calls
                   if sql.startswith("DELETE FROM tmdb_movie_countries")]
        self.assertEqual(1, len(cleanup))
        self.assertIn("NOT IN (%s)", cleanup[0][0])
        self.assertEqual((501, "CN"), cleanup[0][1])

    def test_apply_renders_metadata_upsert_with_all_columns(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        connection = FakeConnection()
        writer.apply_plan(connection, plan)

        metadata = [(sql, params) for sql, params in connection.calls
                    if "INSERT INTO tmdb_movie_metadata" in sql]
        self.assertEqual(1, len(metadata))
        self.assertEqual(17, metadata[0][0].count("%s"))
        self.assertEqual(17, len(metadata[0][1]))
        self.assertEqual(501, metadata[0][1][0])

    def test_empty_plan_executes_nothing(self):
        connection = FakeConnection()
        result = writer.apply_plan(connection, {"operations": [], "now": naive(BASE_TIME)})
        self.assertEqual(0, result["executed"])
        self.assertEqual([], connection.calls)

    def test_malformed_plan_is_rejected(self):
        with self.assertRaises(writer.WriterInputError):
            writer.apply_plan(FakeConnection(), {"now": naive(BASE_TIME), "operations": [("t", "x")]})


# ---------------------------------------------------------------- master fill


class MasterFillTests(unittest.TestCase):
    def test_master_fill_is_off_by_default(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00", master={"title": None})
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        self.assertNotIn("movies", tables(plan))

    def test_master_fill_requires_a_snapshot(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")
        with self.assertRaises(writer.WriterInputError):
            writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00",
                              allow_master_fill=True)

    def test_master_fill_only_touches_null_columns(self):
        master = {"title": "Existing Title", "original_title": None, "release_date": None,
                  "runtime_minutes": None, "original_language": None, "overview": None}
        row = retained_row(success_at="2026-11-01T10:00:00+00:00", master=master)
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00",
                                 allow_master_fill=True)
        fills = operations_for(plan, "movies")
        self.assertEqual(1, len(fills))
        self.assertNotIn("title", fills[0][3])
        self.assertEqual("synthetic overview", fills[0][3]["overview"])


# ------------------------------------------------------------------- deferrals


class DeferredTopicTests(unittest.TestCase):
    def test_credits_are_attempt_only_never_frozen_table_writes(self):
        row = retained_row(success_at="2026-11-01T10:00:00+00:00")
        doc = document(fetched_at="2026-11-01T15:00:00+00:00")
        plan = writer.build_plan(row, doc, now="2026-11-01T21:00:00+00:00")
        self.assertEqual("skipped", plan["decisions"]["credits"]["action"])
        self.assertIn("credits", plan["deferred"])
        self.assertNotIn("movie_credits", tables(plan))
        self.assertEqual(naive("2026-11-01T10:00:00+00:00"),
                         plan["metadata"]["credits_at"])
        status = json.loads(plan["metadata"]["field_status_json"])
        self.assertEqual("PRESENT", status["credits"])


if __name__ == "__main__":
    unittest.main()