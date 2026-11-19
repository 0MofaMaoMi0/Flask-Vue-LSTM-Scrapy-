"""Offline checks for the bounded ``releases`` repair path (no database)."""

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone

from catalog.tmdb_supplement import release_repair


STAMP = datetime(2026, 11, 17, 2, 10, 0, tzinfo=timezone.utc)
FETCHED = "2026-11-16T17:14:39+00:00"


def document(movie_id=2130, tmdb_id=150213, rows=2):
    return {
        "schema_version": "tmdb-supplement-normalized-v1",
        "job_id": "job-all-20261117",
        "movie_id": movie_id,
        "tmdb_id": tmdb_id,
        "fetched_at": FETCHED,
        "payload_sha256": "a" * 64,
        "field_status": {
            "base": "PRESENT", "countries": "PRESENT", "companies": "PRESENT",
            "spoken_languages": "PRESENT", "keywords": "PRESENT",
            "credits": "PRESENT", "metrics": "PRESENT", "releases": "PRESENT",
        },
        "facts": {
            "releases": [{
                "iso_3166_1": "AU",
                "release_dates": [
                    {
                        "type": 3,
                        "type_name": "theatrical",
                        "release_date": "2026-01-0%d" % (index + 1),
                        "release_date_raw": "2026-01-0%d" % (index + 1),
                        "release_date_utc": "2026-01-0%dT00:00:00+00:00" % (index + 1),
                        "precision": "day",
                        "certification": None,
                        "note": None,
                        "iso_639_1": "en",
                        "dedupe_key": hashlib.sha256(("k%d" % index).encode()).hexdigest(),
                    }
                    for index in range(rows)
                ],
            }],
        },
    }


def row(movie_id=2130, source_record_pk=12176, releases="FAILED"):
    return {
        "movie_id": movie_id,
        "tmdb_id": 150213,
        "source_record_pk": source_record_pk,
        "payload_sha256": "a" * 64,
        "field_status_json": json.dumps({
            "base": "PRESENT", "countries": "PRESENT", "companies": "PRESENT",
            "spoken_languages": "PRESENT", "keywords": "PRESENT",
            "credits": "PRESENT", "metrics": "PRESENT", "releases": releases,
        }),
        "last_attempt_json": json.dumps({
            "base": {"status": "PRESENT", "attempt_at": FETCHED, "error_code": None,
                     "payload_sha256": "a" * 64, "source_record_pk": source_record_pk},
            "releases": {"status": "FAILED", "attempt_at": FETCHED, "error_code": None,
                         "payload_sha256": "a" * 64, "source_record_pk": source_record_pk},
        }),
        "provenance_json": json.dumps({
            "base": {"state": "PRESENT", "fetched_at": FETCHED,
                     "payload_sha256": "a" * 64, "source_record_pk": source_record_pk},
        }),
        "releases_at": None,
        "fetched_at": datetime(2026, 11, 16, 17, 14, 39, tzinfo=timezone.utc),
    }


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="repair-")
        os.makedirs(os.path.join(self.root, "normalized"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, *, doc=None, schema=release_repair.SCHEMA, sha=None, entries=True):
        payload = json.dumps(doc or document(), ensure_ascii=False).encode("utf-8")
        path = os.path.join(self.root, "normalized", "150213.json")
        with open(path, "wb") as handle:
            handle.write(payload)
        record = {
            "schema": schema,
            "job_ref": "job-fix-releases-20261117",
            "source_job": "job-all-20261117",
            "entries": ([{
                "movie_id": 2130,
                "tmdb_id": 150213,
                "new_normalized_sha256": sha or hashlib.sha256(payload).hexdigest(),
            }] if entries else []),
        }
        with open(os.path.join(self.root, "repair.json"), "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False)
        return self.root

    def test_loads_valid_archive(self):
        job = release_repair.load_archive(self.write())
        self.assertEqual(job["job_ref"], "job-fix-releases-20261117")
        self.assertEqual(len(job["documents"]), 1)
        self.assertEqual(job["documents"][0]["movie_id"], 2130)

    def test_rejects_unknown_schema(self):
        with self.assertRaises(release_repair.RepairInputError):
            release_repair.load_archive(self.write(schema="other"))

    def test_rejects_hash_mismatch(self):
        with self.assertRaises(release_repair.RepairInputError):
            release_repair.load_archive(self.write(sha="b" * 64))

    def test_rejects_missing_entries(self):
        with self.assertRaises(release_repair.RepairInputError):
            release_repair.load_archive(self.write(entries=False))

    def test_rejects_document_without_releases(self):
        doc = document()
        doc["field_status"]["releases"] = "FAILED"
        payload = json.dumps(doc, ensure_ascii=False).encode("utf-8")
        root = self.write()
        with open(os.path.join(root, "normalized", "150213.json"), "wb") as handle:
            handle.write(payload)
        record = json.load(open(os.path.join(root, "repair.json"), encoding="utf-8"))
        record["entries"][0]["new_normalized_sha256"] = hashlib.sha256(payload).hexdigest()
        json.dump(record, open(os.path.join(root, "repair.json"), "w", encoding="utf-8"))
        with self.assertRaises(release_repair.RepairInputError):
            release_repair.load_archive(root)

    def test_dry_run_counts_rows_without_database(self):
        summary = release_repair.dry_run(self.write(), now=STAMP)
        self.assertEqual(summary["mode"], "dry-run")
        self.assertEqual(summary["documents"], 1)
        self.assertEqual(summary["operations"], 3)  # two rows + cleanup

    def test_plan_touches_only_releases(self):
        planned = release_repair._plan_movie(row(), document(), stamp=STAMP)
        tables = {operation[0] for operation in planned["operations"]}
        self.assertEqual(tables, {"tmdb_release_records"})
        self.assertEqual(planned["status"]["releases"], "PRESENT")
        self.assertEqual(planned["status"]["base"], "PRESENT")
        self.assertEqual(planned["provenance"]["base"]["state"], "PRESENT")
        self.assertNotIn("metrics", planned["provenance"])
        self.assertEqual(planned["attempts"]["releases"]["status"], "PRESENT")
        self.assertEqual(planned["attempts"]["releases"]["source_record_pk"], 12176)
        self.assertEqual(planned["provenance"]["releases"]["fetched_at"], FETCHED)
        self.assertEqual(planned["releases_at"], datetime(2026, 11, 16, 17, 14, 39))

    def test_plan_requires_existing_provenance(self):
        with self.assertRaises(release_repair.RepairRejected):
            release_repair._plan_movie(row(source_record_pk=None), document(), stamp=STAMP)

    def test_plan_keeps_executed_count_in_sync(self):
        planned = release_repair._plan_movie(row(), document(rows=3), stamp=STAMP)
        self.assertEqual(len(planned["operations"]), 4)


if __name__ == "__main__":
    unittest.main()