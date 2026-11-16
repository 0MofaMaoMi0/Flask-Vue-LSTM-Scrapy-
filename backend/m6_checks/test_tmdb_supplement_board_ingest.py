"""Offline tests for the trending board ingest glue."""
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from catalog.tmdb_supplement import board_ingest, trending
from catalog.tmdb_supplement.ingest import IngestInputError, IngestRejected

STAMP = "2026-11-16T16:00:00+00:00"


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


class FakeConnection:
    def __init__(self, mapping=None, lock=1):
        self.mapping = dict(mapping or {})
        self.snapshots = {}
        self.entries = []
        self.batches = {}
        self.statements = []
        self.lock_result = lock
        self.committed = 0
        self.rolled_back = 0
        self._seq = 7000

    def execute(self, sql, params=()):
        text = " ".join(str(sql).split())
        params = tuple(params or ())
        self.statements.append((text, params))
        if text.startswith("SELECT GET_LOCK"):
            return _Cursor(["lock"], [{"lock": self.lock_result}])
        if text.startswith("SELECT RELEASE_LOCK"):
            return _Cursor(["lock"], [{"lock": 1}])
        if text.startswith("SELECT movie_id,source_record_id FROM movie_source_records"):
            return _Cursor(["movie_id", "source_record_id"],
                           [{"movie_id": movie, "source_record_id": raw}
                            for raw, movie in self.mapping.items()])
        if text.startswith("INSERT INTO ingest_batches"):
            self._seq += 1
            self.batches[self._seq] = {"batch_id": self._seq}
            return _Cursor([], [], lastrowid=self._seq)
        if text.startswith("UPDATE ingest_batches"):
            self.batches.setdefault(params[-1], {"batch_id": params[-1]})["status"] = params[0]
            return _Cursor([], [])
        if text.startswith("SELECT snapshot_id,ingest_batch_id FROM tmdb_board_snapshots"):
            row = self.snapshots.get((params[0], params[1]))
            return _Cursor(["snapshot_id", "ingest_batch_id"], [row] if row else [])
        if text.startswith("INSERT INTO tmdb_board_snapshots"):
            self._seq += 1
            self.snapshots[(params[0], params[1])] = {
                "snapshot_id": self._seq, "ingest_batch_id": params[6],
                "status": params[2], "page_count": params[3], "entry_count": params[4],
                "payload_sha256": params[5]}
            return _Cursor([], [], lastrowid=self._seq)
        if text.startswith("SELECT tmdb_id FROM tmdb_board_entries"):
            return _Cursor(["tmdb_id"], [{"tmdb_id": entry["tmdb_id"]}
                                         for entry in self.entries
                                         if entry["snapshot_id"] == params[0]])
        if text.startswith("INSERT INTO tmdb_board_entries"):
            self.entries.append({"snapshot_id": params[0], "tmdb_id": params[1],
                                 "rank": params[2], "movie_id": params[3], "title": params[4]})
            return _Cursor([], [])
        raise AssertionError("unexpected SQL: " + text)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


def write_archive(root, job_id="board-job", day_ids=(100, 101, 999), week_ids=(200,),
                  captured_at=STAMP, status="succeeded"):
    job_dir = Path(root) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    channels = {}
    entries = {}
    for channel, ids in (("trending_day", day_ids), ("trending_week", week_ids)):
        rows = [{"tmdb_id": tmdb_id, "title": "t%d" % tmdb_id, "release_date": "2026-01-01"}
                for tmdb_id in ids]
        entries[channel] = rows
        channels[channel] = {"captured_at": captured_at, "status": status,
                             "pages": [{"page": 1, "status": "HTTP_200", "entries": len(rows)}],
                             "entry_count": len(rows),
                             "payload_sha256": "a" * 64}
    manifest = {"schema": trending.TRENDING_SCHEMA, "job_id": job_id,
                "language": "zh-CN", "pages": 1, "requests_used": 2,
                "max_requests": 12, "channels": channels}
    (job_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (job_dir / "entries.json").write_text(json.dumps(entries), encoding="utf-8")
    return job_dir


class DryRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_is_zero_sql(self):
        write_archive(self.root)
        connection = FakeConnection()
        result = board_ingest.dry_run(str(self.root / "board-job"))
        self.assertEqual(connection.statements, [])
        self.assertEqual(result["channels"]["trending_day"]["entries"], 3)
        self.assertEqual(result["operations"], 6)  # 3+1 day, 1+1 week

    def test_future_captured_at_is_rejected(self):
        write_archive(self.root, captured_at="2099-01-01T00:00:00+00:00")
        with self.assertRaises(IngestRejected):
            board_ingest.dry_run(str(self.root / "board-job"))

    def test_incomplete_manifest_is_rejected(self):
        (self.root / "empty").mkdir()
        with self.assertRaises(trending.TrendingArchiveError):
            board_ingest.dry_run(str(self.root / "empty"))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_inserts_snapshots_and_maps_known_movies(self):
        write_archive(self.root)
        connection = FakeConnection(mapping={"100": 11, "101": 12, "200": 13})
        result = board_ingest.run(connection, str(self.root / "board-job"))
        self.assertEqual(result["inserted_entries"], 4)
        self.assertEqual(result["unmapped_entries"], 1)
        self.assertTrue(result["idempotent"] is False)
        self.assertEqual(len(connection.snapshots), 2)
        mapped = {entry["tmdb_id"]: entry["movie_id"] for entry in connection.entries}
        self.assertEqual(mapped, {100: 11, 101: 12, 999: None, 200: 13})
        ranks = [entry["rank"] for entry in connection.entries if entry["snapshot_id"] ==
                 result["snapshots"]["trending_day"]["snapshot_id"]]
        self.assertEqual(ranks, [1, 2, 3])
        self.assertEqual(connection.committed, 2)  # data commit + lock release

    def test_replay_is_idempotent_and_keeps_batch_attribution(self):
        write_archive(self.root)
        connection = FakeConnection(mapping={"100": 11})
        first = board_ingest.run(connection, str(self.root / "board-job"),
                                 job_ref="trending:board-job")
        batch_id = first["batch_id"]
        snapshot = connection.snapshots[("trending_day", datetime(2026, 11, 16, 16, 0))]
        second = board_ingest.run(connection, str(self.root / "board-job"),
                                  job_ref="trending:board-job")
        self.assertTrue(second["idempotent"])
        self.assertEqual(second["inserted_entries"], 0)
        self.assertEqual(second["skipped_entries"], 4)
        self.assertEqual(snapshot["ingest_batch_id"], batch_id)
        self.assertNotEqual(second["batch_id"], batch_id)

    def test_status_and_counts_come_from_the_manifest(self):
        write_archive(self.root, status="weird")
        connection = FakeConnection()
        with self.assertRaises(trending.TrendingArchiveError):
            board_ingest.run(connection, str(self.root / "board-job"))

    def test_lock_failure_rejects_before_writing(self):
        write_archive(self.root)
        connection = FakeConnection(lock=0)
        with self.assertRaises(IngestRejected):
            board_ingest.run(connection, str(self.root / "board-job"))
        self.assertEqual(connection.batches, {})
        self.assertEqual(connection.rolled_back, 1)

    def test_unknown_mode_is_rejected(self):
        write_archive(self.root)
        with self.assertRaises(IngestInputError):
            board_ingest.run(FakeConnection(), str(self.root / "board-job"), mode="dry")


if __name__ == "__main__":
    unittest.main()


