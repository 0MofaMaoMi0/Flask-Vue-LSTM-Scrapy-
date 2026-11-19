"""Offline tests for the TMDB supplement ingest glue.

No database, network, configuration or real capture archive is used: archives
are synthetic fixtures under a temporary directory and the "database" is a fake
DB-API connection that records every statement and simulates the handful of
tables the glue touches.
"""
import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from catalog.tmdb_supplement import ingest, writer

TOPICS = writer.TOPICS
STAMP = datetime(2026, 11, 16, 12, 0, 0)
LATER = datetime(2026, 11, 16, 13, 0, 0)
SHA_A = "a" * 64
SHA_B = "b" * 64
FORBIDDEN = re.compile(
    r"\b(?:INSERT INTO|UPDATE|DELETE FROM)\s+"
    r"(?:movies|movie_credits|movie_ratings_summary|movie_assets|genres|movie_genres)\b")


# ------------------------------------------------------------------ fixtures


def iso(moment):
    return moment.replace(tzinfo=timezone.utc).isoformat()


def facts():
    return {
        "base": {"title": "Demo", "original_title": "Demo", "release_date": "2020-01-02",
                 "runtime": 101, "original_language": "en", "overview": "demo"},
        "countries": [{"iso_3166_1": "US", "name": "United States"}],
        "companies": [{"id": 41077, "name": "Demo Studio",
                       "origin_country": "US", "logo_path": None}],
        "spoken_languages": [{"iso_639_1": "en", "english_name": "English", "name": "English"}],
        "keywords": [{"id": 9715, "name": "superhero"}],
        "releases": [{"iso_3166_1": "US", "release_dates": [{
            "type": 3, "release_date": "2020-01-02", "release_date_raw": "2020-01-02",
            "certification": "PG", "note": "", "iso_639_1": "en",
            "dedupe_key": "c" * 64}]}],
        "credits": [],
        "metrics": {"vote_average": 7.5, "vote_count": 1200, "popularity": 33.5},
    }


def document(movie_id=1, tmdb_id=101, *, fetched_at=None, payload_sha256=SHA_A,
             status=None, fact_bundle=None):
    return {
        "schema_version": "tmdb-supplement-v1",
        "movie_id": movie_id,
        "tmdb_id": tmdb_id,
        "fetched_at": iso(fetched_at or STAMP),
        "job_id": "job-offline",
        "payload_sha256": payload_sha256,
        "field_status": status or {topic: "PRESENT" for topic in TOPICS},
        "facts": fact_bundle if fact_bundle is not None else facts(),
        "issues": [],
    }


def attempt(tmdb_id, movie_id, number=1, *, status="HTTP_200", http_status=200,
            doc=None, raw=None, with_raw=True):
    entry = {"tmdb_id": tmdb_id, "movie_id": movie_id, "attempt": number,
             "status": status, "http_status": http_status, "document": doc}
    if with_raw:
        entry["raw"] = raw if raw is not None else {"id": tmdb_id, "title": "raw"}
    return entry


def write_archive(root, attempts, *, schema=ingest.MANIFEST_SCHEMA, job_id="job-offline"):
    root = Path(root)
    (root / "normalized").mkdir(parents=True, exist_ok=True)
    (root / "raw").mkdir(parents=True, exist_ok=True)
    manifest_attempts = []
    for entry in attempts:
        tmdb_id = entry["tmdb_id"]
        number = entry["attempt"]
        if entry.get("document") is not None:
            path = root / "normalized" / str(tmdb_id) / ("attempt-%d.json" % number)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(entry["document"], ensure_ascii=False), encoding="utf-8")
        if "raw" in entry:
            path = root / "raw" / str(tmdb_id) / ("attempt-%d.json" % number)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(entry["raw"]), encoding="utf-8")
        manifest_attempts.append({key: entry[key] for key in
                                  ("tmdb_id", "movie_id", "attempt", "status", "http_status")})
    manifest = {"schema": schema, "job_id": job_id,
                "api_root": "https://api.themoviedb.org/3", "language": "zh-CN",
                "append_to_response": ["credits", "keywords", "release_dates"],
                "attempts": manifest_attempts}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


# ------------------------------------------------------------- fake database


class _Cursor:
    def __init__(self, columns, rows, lastrowid=None):
        self.description = [(name, None, None, None, None, None, None) for name in columns]
        self._rows = list(rows)
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


def _insert_columns(text, table):
    match = re.match(r"INSERT INTO " + table + r" \(([^)]*)\) VALUES", text)
    return [column.strip() for column in match.group(1).split(",")]


class FakeConnection:
    """Records statements and simulates the tables the ingest glue writes."""

    def __init__(self, *, metadata=None, source_records=None, jobs=None,
                 lock=1, drop_metadata=False):
        self.metadata = dict(metadata or {})
        self.source_records = dict(source_records or {})
        self.jobs = dict(jobs or {})
        self.batches = {}
        self.items = []
        self.statements = []
        self.commits = 0
        self.rollbacks = 0
        self.lock_result = lock
        self.drop_metadata = drop_metadata
        self._batch_seq = 900
        self._job_seq = 7
        self._record_seq = 201

    def executed(self, prefix, start=0):
        return [sql for sql, _ in self.statements[start:] if sql.startswith(prefix)]

    def execute(self, sql, params=()):
        text = " ".join(str(sql).split())
        params = tuple(params or ())
        self.statements.append((text, params))

        if text.startswith("SELECT GET_LOCK"):
            return _Cursor(["lock"], [{"lock": self.lock_result}])
        if text.startswith("SELECT RELEASE_LOCK"):
            return _Cursor(["lock"], [{"lock": 1}])
        if text.startswith("INSERT INTO ingest_batches"):
            self._batch_seq += 1
            row = dict(zip(_insert_columns(text, "ingest_batches"), params))
            row["batch_id"] = self._batch_seq
            self.batches[self._batch_seq] = row
            return _Cursor([], [], lastrowid=self._batch_seq)
        if text.startswith("UPDATE ingest_batches"):
            row = self.batches.setdefault(params[-1], {"batch_id": params[-1]})
            row.update(status=params[0], finished_at=params[1], counts_json=params[2],
                       error_summary=params[3])
            return _Cursor([], [])
        if text.startswith("INSERT INTO tmdb_supplement_jobs"):
            self._job_seq += 1
            row = dict(zip(_insert_columns(text, "tmdb_supplement_jobs"), params))
            row["job_id"] = self._job_seq
            self.jobs[row["job_ref"]] = row
            return _Cursor([], [], lastrowid=self._job_seq)
        if text.startswith("UPDATE tmdb_supplement_jobs"):
            reference = None
            if len(params) == 15:
                reference = params[-1]
            for key, row in self.jobs.items():
                if len(params) == 15 and row["job_id"] == reference:
                    row.update(status=params[1])
                elif len(params) == 10 and row["job_id"] == params[-1]:
                    row.update(status=params[0], finished_at=params[1],
                               success_count=params[3], failure_count=params[4],
                               skipped_count=params[5], error_code=params[6],
                               error_summary=params[7])
                elif len(params) == 7 and row["job_id"] == params[-1]:
                    row.update(status=params[0], finished_at=params[1],
                               error_code=params[3], error_summary=params[4])
            return _Cursor([], [])
        if text.startswith("SELECT job_id,started_at FROM tmdb_supplement_jobs"):
            row = self.jobs.get(params[0])
            rows = [{"job_id": row["job_id"], "started_at": row.get("started_at")}] if row else []
            return _Cursor(["job_id", "started_at"], rows)
        if text.startswith("SELECT job_id FROM tmdb_supplement_jobs"):
            row = self.jobs.get(params[0])
            return _Cursor(["job_id"], [{"job_id": row["job_id"]}] if row else [])
        if text.startswith("SELECT id,movie_id FROM movie_source_records"):
            row = self.source_records.get((params[0], params[1]))
            return _Cursor(["id", "movie_id"], [dict(row)] if row else [])
        if text.startswith("INSERT INTO movie_source_records"):
            self._record_seq += 1
            self.source_records[(params[1], params[2])] = {
                "id": self._record_seq, "movie_id": params[0], "payload_hash": params[5]}
            return _Cursor([], [], lastrowid=self._record_seq)
        if text.startswith("INSERT INTO tmdb_movie_metadata"):
            row = dict(zip(_insert_columns(text, "tmdb_movie_metadata"), params))
            if not self.drop_metadata:
                self.metadata[row["movie_id"]] = row
            return _Cursor([], [], lastrowid=row["movie_id"])
        if " FROM tmdb_movie_metadata" in text and text.startswith("SELECT "):
            columns = [column.strip() for column in
                       text[7:text.index(" FROM ")].split(",")]
            row = self.metadata.get(params[0])
            return _Cursor(columns, [dict(row)] if row is not None else [])
        if text.startswith("INSERT INTO ingest_items"):
            self.items.append({"batch_id": params[0], "source_record_id": params[1],
                               "status": params[2], "movie_id": params[3],
                               "payload_hash": params[5]})
            return _Cursor([], [])
        return _Cursor([], [])

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


# ------------------------------------------------------------------- tests


class LoadJobTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_uses_latest_successful_attempt_only(self):
        first = document(payload_sha256=SHA_A)
        second = document(fetched_at=LATER, payload_sha256=SHA_B)
        write_archive(self.root, [
            attempt(101, 1, 1, status="HTTP_500", http_status=500, doc=first, with_raw=False),
            attempt(101, 1, 2, doc=second),
        ])
        job = ingest.load_job(self.root)
        self.assertEqual(1, len(job["documents"]))
        self.assertEqual(SHA_B, job["documents"][0]["payload_sha256"])
        self.assertEqual({"attempts": 2, "success": 1, "failed": 1}, job["attempt_counts"])

    def test_ignores_archive_without_success(self):
        write_archive(self.root, [
            attempt(101, 1, 1, status="HTTP_429", http_status=429, doc=document()),
        ])
        with self.assertRaises(ingest.IngestInputError) as caught:
            ingest.load_job(self.root)
        self.assertEqual("NO_SUCCESSFUL_ATTEMPT", caught.exception.code)

    def test_rejects_unknown_manifest_schema(self):
        write_archive(self.root, [attempt(101, 1, 1, doc=document())], schema="other-v9")
        with self.assertRaises(ingest.IngestInputError) as caught:
            ingest.load_job(self.root)
        self.assertEqual("INVALID_MANIFEST_SCHEMA", caught.exception.code)

    def test_rejects_document_without_facts(self):
        broken = document()
        broken.pop("facts")
        write_archive(self.root, [attempt(101, 1, 1, doc=broken)])
        with self.assertRaises(ingest.IngestInputError) as caught:
            ingest.load_job(self.root)
        self.assertEqual("MISSING_DOCUMENT_FIELD", caught.exception.code)

    def test_rejects_duplicate_movie_id(self):
        write_archive(self.root, [
            attempt(101, 1, 1, doc=document(movie_id=1, tmdb_id=101)),
            attempt(102, 1, 1, doc=document(movie_id=1, tmdb_id=102)),
        ])
        with self.assertRaises(ingest.IngestInputError) as caught:
            ingest.load_job(self.root)
        self.assertEqual("DUPLICATE_MOVIE_ID", caught.exception.code)

    def test_document_limit_is_enforced(self):
        write_archive(self.root, [
            attempt(101, 1, 1, doc=document(movie_id=1, tmdb_id=101)),
            attempt(102, 2, 1, doc=document(movie_id=2, tmdb_id=102)),
        ])
        with mock.patch.object(ingest, "MAX_DOCUMENTS", 1):
            with self.assertRaises(ingest.IngestInputError) as caught:
                ingest.load_job(self.root)
        self.assertEqual("TOO_MANY_DOCUMENTS", caught.exception.code)


class DryRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = write_archive(Path(self.tmp.name), [attempt(101, 1, 1, doc=document())])

    def test_dry_run_plans_without_database(self):
        summary = ingest.dry_run(self.root, now=STAMP)
        self.assertEqual(1, summary["documents"])
        self.assertGreater(summary["operations"], 0)
        self.assertEqual({"empty_state": True, "source_record_pk": 1}, summary["assumed"])
        self.assertEqual(1, summary["per_topic"]["countries"]["replace"])

    def test_run_rejects_non_write_mode(self):
        with self.assertRaises(ingest.IngestInputError) as caught:
            ingest.run(FakeConnection(), self.root, mode="dry-run")
        self.assertEqual("UNSUPPORTED_MODE", caught.exception.code)


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = write_archive(Path(self.tmp.name), [attempt(101, 1, 1, doc=document())])
        self.job_ref = "job-offline"

    def run_ingest(self, connection, **options):
        options.setdefault("now", STAMP)
        return ingest.run(connection, self.root, job_ref=self.job_ref, **options)

    def test_writes_batch_before_documents(self):
        connection = FakeConnection()
        summary = self.run_ingest(connection)
        texts = [sql for sql, _ in connection.statements]
        lock = next(i for i, sql in enumerate(texts) if sql.startswith("SELECT GET_LOCK"))
        batch = next(i for i, sql in enumerate(texts) if sql.startswith("INSERT INTO ingest_batches"))
        source = next(i for i, sql in enumerate(texts)
                      if sql.startswith("INSERT INTO movie_source_records"))
        metadata = next(i for i, sql in enumerate(texts)
                        if sql.startswith("INSERT INTO tmdb_movie_metadata"))
        self.assertLess(lock, batch)
        self.assertLess(batch, source)
        self.assertLess(source, metadata)
        self.assertEqual(1, summary["documents"])
        self.assertGreater(summary["executed_operations"], 0)
        self.assertIn("tmdb_movie_countries", summary["by_table"])
        self.assertEqual("inserted", connection.items[0]["status"])
        self.assertEqual(1, len(connection.batches))
        self.assertEqual("succeeded", connection.batches[summary["batch_id"]]["status"])

    def test_source_record_is_reused_on_rerun(self):
        connection = FakeConnection()
        self.run_ingest(connection)
        first = len(connection.statements)
        self.run_ingest(connection, now=LATER)
        again = connection.statements[first:]
        self.assertEqual([], [sql for sql, _ in again
                              if sql.startswith("INSERT INTO movie_source_records")])

    def test_owner_conflict_rolls_back(self):
        record_id = "101:supplement:" + SHA_A
        connection = FakeConnection(source_records={
            ("tmdb", record_id): {"id": 55, "movie_id": 99}})
        with self.assertRaises(ingest.IngestRejected) as caught:
            self.run_ingest(connection)
        self.assertEqual("SOURCE_OWNER_CONFLICT", caught.exception.code)
        self.assertEqual(1, connection.rollbacks)
        self.assertEqual("failed", connection.jobs[self.job_ref]["status"])
        self.assertIsNotNone(connection.jobs[self.job_ref]["finished_at"])
        self.assertEqual([], [sql for sql, _ in connection.statements
                              if sql.startswith("UPDATE ingest_batches")])

    def test_reconciliation_mismatch_rolls_back(self):
        connection = FakeConnection(drop_metadata=True)
        with self.assertRaises(ingest.IngestRejected) as caught:
            self.run_ingest(connection)
        self.assertEqual("COMMIT_REQUIRES_RECONCILIATION", caught.exception.code)
        self.assertEqual(1, connection.rollbacks)

    def test_rerun_is_idempotent(self):
        connection = FakeConnection()
        first = self.run_ingest(connection)
        self.assertGreater(first["executed_operations"], 0)
        self.assertFalse(first["idempotent"])
        mark = len(connection.statements)
        second = self.run_ingest(connection, now=LATER)
        self.assertEqual(0, second["executed_operations"])
        self.assertTrue(second["idempotent"])
        self.assertEqual("skipped", connection.items[-1]["status"])
        replay = [sql for sql, _ in connection.statements[mark:]]
        for table in ("tmdb_movie_countries", "tmdb_movie_companies", "tmdb_movie_languages",
                      "tmdb_movie_keywords", "tmdb_release_records",
                      "tmdb_metric_observations", "tmdb_movie_metadata"):
            self.assertEqual([], [sql for sql in replay if sql.startswith("INSERT INTO " + table)],
                             table)

    def test_lock_is_released_and_job_finalized(self):
        connection = FakeConnection()
        summary = self.run_ingest(connection)
        self.assertEqual(1, len(connection.executed("SELECT RELEASE_LOCK")))
        job = connection.jobs[self.job_ref]
        self.assertEqual("succeeded", job["status"])
        self.assertIsNotNone(job["finished_at"])
        self.assertEqual("succeeded", connection.batches[summary["batch_id"]]["status"])
        self.assertIsNotNone(connection.batches[summary["batch_id"]]["finished_at"])

    def test_lock_unavailable_rejected(self):
        connection = FakeConnection(lock=0)
        with self.assertRaises(ingest.IngestRejected) as caught:
            self.run_ingest(connection)
        self.assertEqual("LOCK_UNAVAILABLE", caught.exception.code)
        self.assertEqual([], connection.executed("INSERT INTO ingest_batches"))
        self.assertEqual(1, len(connection.executed("SELECT RELEASE_LOCK")))

    def test_summary_contains_no_payload_or_token(self):
        summary = self.run_ingest(FakeConnection())
        rendered = json.dumps(summary, ensure_ascii=False, default=str).lower()
        self.assertNotIn(SHA_A, json.dumps(summary, default=str))
        for needle in ("token", "http://", "https://", "demostudio", "superhero"):
            self.assertNotIn(needle, rendered)

    def test_sealed_catalog_tables_are_never_written(self):
        connection = FakeConnection()
        self.run_ingest(connection)
        for sql, _ in connection.statements:
            self.assertIsNone(FORBIDDEN.search(sql), sql)


if __name__ == "__main__":
    unittest.main()