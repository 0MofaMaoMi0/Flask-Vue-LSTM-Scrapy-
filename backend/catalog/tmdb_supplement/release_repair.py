"""Bounded repair of the single failed ``releases`` topic.

The full capture pipeline writes a whole document per movie, so re-running it
would rewrite the seven already-good topics and add a fresh metric observation.
This module is the narrow alternative: it takes a *derived* archive produced
from the original raw payloads (see ``build_repair_archive``) and touches only

* ``tmdb_release_records`` rows for that movie (delete-free replace),
* the ``releases`` entries of ``tmdb_movie_metadata`` (status / provenance /
  attempt / ``releases_at``),
* one ``ingest_batches`` row, one ``ingest_items`` row and one
  ``tmdb_supplement_jobs`` row for traceability.

Everything else is left exactly as it was, in particular the seven other
topics, ``tmdb_metric_observations`` and the existing ``movie_source_records``
row, whose primary key is reused as ``source_record_pk``.  A movie is only
eligible when its stored ``releases`` status is ``FAILED``.

The derived archive is::

    repair.json                      # schema, source job, hashes, per-movie entries
    normalized/<tmdb_id>.json        # re-normalized document (releases PRESENT)

Nothing here fetches from the network and no raw archive is modified.
"""

import datetime
import hashlib
import json
import re
from pathlib import Path

from . import ingest as _ingest
from . import writer as _writer

SCHEMA = "tmdb-supplement-release-repair-v1"
RELEASES = "releases"
MAX_DOCUMENTS = 64
LOCK_TIMEOUT_SECONDS = 20
_JOB_REF = re.compile(r"[A-Za-z0-9:._-]{1,128}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ISO_INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")


class RepairError(Exception):
    """Base class; ``code`` is a stable machine-readable reason."""

    code = "REPAIR_ERROR"

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class RepairInputError(RepairError):
    code = "REPAIR_INPUT_INVALID"


class RepairRejected(RepairError):
    code = "REPAIR_REJECTED"


def _read_json(path, *, maximum):
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise RepairInputError("FILE_UNREADABLE", str(path.name)) from error
    if len(raw) > maximum:
        raise RepairInputError("FILE_TOO_LARGE", str(path.name))
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise RepairInputError("INVALID_JSON", str(path.name)) from error


def _int(value, label, *, maximum):
    if type(value) is not int or value <= 0 or value > maximum:
        raise RepairInputError("INVALID_" + label.upper(), label)
    return value


def _sha(value, label):
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise RepairInputError("INVALID_" + label.upper(), label)
    return value


def load_archive(archive_dir):
    """Validate a derived repair archive and return its documents."""
    root = Path(archive_dir)
    record = _read_json(root / "repair.json", maximum=1024 * 1024)
    if type(record) is not dict or record.get("schema") != SCHEMA:
        raise RepairInputError("INVALID_SCHEMA", "repair.json schema 不受支持")
    entries = record.get("entries")
    if type(entries) is not list or not entries:
        raise RepairInputError("INVALID_ENTRIES", "entries 必须是非空列表")
    if len(entries) > MAX_DOCUMENTS:
        raise RepairInputError("TOO_MANY_DOCUMENTS", "单次修复上限 %d" % MAX_DOCUMENTS)
    documents, seen_movies, seen_tmdb = [], set(), set()
    for entry in entries:
        if type(entry) is not dict:
            raise RepairInputError("INVALID_ENTRY", "entry 必须是对象")
        movie_id = _int(entry.get("movie_id"), "movie_id", maximum=2 ** 31)
        tmdb_id = _int(entry.get("tmdb_id"), "tmdb_id", maximum=2 ** 31)
        if movie_id in seen_movies or tmdb_id in seen_tmdb:
            raise RepairInputError("DUPLICATE_MOVIE", "movie/tmdb 重复")
        seen_movies.add(movie_id)
        seen_tmdb.add(tmdb_id)
        expected = _sha(entry.get("new_normalized_sha256"), "new_normalized_sha256")
        path = root / "normalized" / (str(tmdb_id) + ".json")
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise RepairInputError("FILE_UNREADABLE", path.name) from error
        if hashlib.sha256(payload).hexdigest() != expected:
            raise RepairInputError("HASH_MISMATCH", path.name)
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise RepairInputError("INVALID_JSON", path.name) from error
        if type(document) is not dict:
            raise RepairInputError("INVALID_DOCUMENT", path.name)
        if document.get("movie_id") != movie_id or document.get("tmdb_id") != tmdb_id:
            raise RepairInputError("IDENTITY_MISMATCH", path.name)
        status = document.get("field_status")
        if type(status) is not dict or status.get(RELEASES) != "PRESENT":
            raise RepairInputError("RELEASES_NOT_PRESENT", path.name)
        facts = document.get("facts")
        rows = facts.get(RELEASES) if isinstance(facts, dict) else None
        if type(rows) is not list or not rows:
            raise RepairInputError("RELEASES_EMPTY", path.name)
        fetched_at = document.get("fetched_at")
        if type(fetched_at) is not str or _ISO_INSTANT.fullmatch(fetched_at) is None:
            raise RepairInputError("INVALID_FETCHED_AT", path.name)
        payload_sha = _sha(document.get("payload_sha256"), "payload_sha256")
        documents.append({
            "movie_id": movie_id,
            "tmdb_id": tmdb_id,
            "fetched_at": fetched_at,
            "payload_sha256": payload_sha,
            "facts": facts,
            "statuses": status,
        })
    metadata = record.get("repair_metadata")
    return {
        "root": root,
        "job_ref": _job_ref(record, root),
        "documents": documents,
        "record": record,
        "repair_metadata": metadata if isinstance(metadata, dict) else {},
    }


def _job_ref(record, root):
    value = record.get("job_ref") or ("fix-releases-" + root.name)
    if type(value) is not str or _JOB_REF.fullmatch(value) is None:
        raise RepairInputError("INVALID_JOB_REF", "job_ref 必须是 1..128 位安全标识")
    return value


def _metadata_row(connection, movie_id, *, lock):
    sql = _ingest._METADATA_SELECT + (" FOR UPDATE" if lock else "")
    return _ingest._one(connection, sql, (movie_id,))


def _releases_status(row):
    raw = (row or {}).get("field_status_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    return raw.get(RELEASES)


def _existing_pk(row):
    value = (row or {}).get("source_record_pk")
    if type(value) is not int or value <= 0:
        raise RepairRejected("SOURCE_RECORD_MISSING", "既有来源记录缺失，未改写入库")
    return value


def _plan_movie(row, document, *, stamp):
    """Release operations for one movie plus the metadata patch they justify."""
    source_pk = _existing_pk(row)
    observed_at = _ingest._clock(document["fetched_at"], "fetched_at")
    operations = _writer._replace_operations(
        RELEASES, document["movie_id"], document["facts"][RELEASES], source_pk, observed_at)
    status = json.loads(row["field_status_json"]) if isinstance(row.get("field_status_json"), str) else dict(row["field_status_json"])
    provenance = json.loads(row["provenance_json"]) if isinstance(row.get("provenance_json"), str) else dict(row["provenance_json"])
    attempts = json.loads(row["last_attempt_json"]) if isinstance(row.get("last_attempt_json"), str) else dict(row["last_attempt_json"])
    status[RELEASES] = "PRESENT"
    provenance[RELEASES] = {
        "state": "PRESENT",
        "fetched_at": _ingest._iso(observed_at),
        "payload_sha256": document["payload_sha256"],
        "source_record_pk": source_pk,
    }
    attempts[RELEASES] = {
        "status": "PRESENT",
        "attempt_at": _ingest._iso(stamp),
        "error_code": None,
        "payload_sha256": document["payload_sha256"],
        "source_record_pk": source_pk,
    }
    return {
        "operations": operations,
        "now": stamp,
        "movie_id": document["movie_id"],
        "status": status,
        "provenance": provenance,
        "attempts": attempts,
        "releases_at": observed_at,
    }


def _patch_metadata(connection, planned):
    connection.execute(
        "UPDATE tmdb_movie_metadata SET field_status_json=%s,provenance_json=%s,"
        "last_attempt_json=%s,releases_at=%s,updated_at=%s WHERE movie_id=%s",
        (_ingest._canonical(planned["status"]), _ingest._canonical(planned["provenance"]),
         _ingest._canonical(planned["attempts"]), planned["releases_at"], planned["now"],
         planned["movie_id"]))
    if getattr(connection, "rowcount", None) not in (None, 1):
        raise RepairRejected("METADATA_PATCH_FAILED", "元数据行未按预期更新")


def dry_run(archive_dir, *, now=None):
    """Read-only preview: which movies would be repaired and how many rows."""
    job = load_archive(archive_dir)
    stamp = _ingest._clock(now or datetime.datetime.now(datetime.timezone.utc), "now")
    return _preview(job, stamp, connection=None)


def _preview(job, stamp, *, connection):
    movies, operations = 0, 0
    for document in job["documents"]:
        if connection is not None:
            row = _metadata_row(connection, document["movie_id"], lock=False)
            if row is None:
                raise RepairRejected("METADATA_MISSING", "元数据行不存在: %d" % document["movie_id"])
            if _releases_status(row) != "FAILED":
                raise RepairRejected("RELEASES_NOT_FAILED", "目标主题状态不是 FAILED: %d" % document["movie_id"])
            planned = _plan_movie(row, document, stamp=stamp)
            operations += len(planned["operations"])
        else:
            operations += sum(
                len(region.get("release_dates") or [])
                for region in document["facts"][RELEASES]) + 1
        movies += 1
    return {
        "command": "repair-releases-plan",
        "mode": "dry-run",
        "job_ref": job["job_ref"],
        "documents": movies,
        "operations": operations,
        "finished_at": _ingest._iso(stamp),
    }


def run(connection, archive_dir, *, mode="write", now=None):
    """Apply a derived repair archive in a single transaction."""
    if mode != "write":
        raise RepairInputError("UNSUPPORTED_MODE", "run 只接受 mode='write'，预览请用 dry_run")
    job = load_archive(archive_dir)
    job_ref = job["job_ref"]
    stamp = _ingest._clock(now or datetime.datetime.now(datetime.timezone.utc), "now")
    lock_name = "mi2_tsup_repair_" + hashlib.sha1(job_ref.encode("utf-8")).hexdigest()[:32]
    batch_id = job_id = None
    inserted = 0
    committed = False
    try:
        locked = _ingest._scalar(connection, "SELECT GET_LOCK(%s,%s)", (lock_name, LOCK_TIMEOUT_SECONDS))
        if locked != 1:
            raise RepairRejected("LOCK_UNAVAILABLE", "无法取得补采写入锁")
        batch_id = _insert_batch(connection, job_ref, stamp)
        job_id = _upsert_job(connection, job_ref, job, batch_id, stamp)
        for document in job["documents"]:
            row = _metadata_row(connection, document["movie_id"], lock=True)
            if row is None:
                raise RepairRejected("METADATA_MISSING", "元数据行不存在: %d" % document["movie_id"])
            if _releases_status(row) != "FAILED":
                raise RepairRejected("RELEASES_NOT_FAILED", "目标主题状态不是 FAILED: %d" % document["movie_id"])
            planned = _plan_movie(row, document, stamp=stamp)
            applied = _writer.apply_plan(connection, planned)
            if applied["executed"] != len(planned["operations"]):
                raise RepairRejected("OPERATION_COUNT_MISMATCH", "写入操作数与计划不一致")
            _patch_metadata(connection, planned)
            inserted += applied["executed"]
            item = {"movie_id": document["movie_id"], "tmdb_id": document["tmdb_id"], "payload_sha256": document["payload_sha256"]}
            _ingest._insert_item(connection, batch_id, item, "updated", stamp)
        _ingest._finish_batch(connection, batch_id, "succeeded", stamp, {
            "mode": "repair-releases",
            "documents": len(job["documents"]),
            "success": len(job["documents"]),
            "failed": 0,
            "skipped": 0,
            "executed_operations": inserted,
        })
        _ingest._finish_job(connection, job_id, "succeeded", stamp, {
            "success_count": len(job["documents"]), "failure_count": 0, "skipped_count": 0})
        connection.commit()
        committed = True
        return {
            "command": "repair-releases",
            "mode": "write",
            "job_ref": job_ref,
            "job_id": job_id,
            "batch_id": batch_id,
            "documents": len(job["documents"]),
            "executed_operations": inserted,
            "finished_at": _ingest._iso(stamp),
        }
    except Exception:
        if not committed:
            try:
                connection.rollback()
            except Exception:  # noqa: BLE001
                pass
        raise
    finally:
        try:
            connection.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
            connection.commit()
        except Exception:  # noqa: BLE001
            pass


def _insert_batch(connection, job_ref, stamp):
    cursor = connection.execute(
        "INSERT INTO ingest_batches (source,mode,status,started_at,finished_at,"
        "source_snapshot_ref,counts_json,error_summary) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        ("tmdb", "write", "running", stamp, None, "tmdb-supplement-repair:" + job_ref,
         _ingest._canonical({}), None))
    return _ingest._lastrowid(connection, cursor)


def _upsert_job(connection, job_ref, job, batch_id, stamp):
    scope = _ingest._canonical({
        "schema": SCHEMA,
        "documents": len(job["documents"]),
        "topics": [RELEASES],
        "mode": "repair-releases",
        "source_job": (job["record"].get("source_job") if isinstance(job["record"], dict) else None),
        "normalizer_module_sha256": (job["record"].get("normalizer_module_sha256") if isinstance(job["record"], dict) else None),
    })
    existing = _ingest._one(connection,
                            "SELECT job_id FROM tmdb_supplement_jobs WHERE job_ref=%s", (job_ref,))
    if existing is not None:
        return int(existing["job_id"])
    cursor = connection.execute(
        "INSERT INTO tmdb_supplement_jobs (job_ref,mode,status,request_budget,request_count,"
        "success_count,failure_count,skipped_count,scope_json,checkpoint_json,"
        "ingest_batch_id,started_at,heartbeat_at,finished_at,error_code,error_summary,"
        "created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (job_ref, "write", "running", len(job["documents"]), len(job["documents"]), 0, 0, 0, scope, _ingest._canonical({}),
         batch_id, stamp, stamp, None, None, None, stamp, stamp))
    return _ingest._lastrowid(connection, cursor)


__all__ = [
    "SCHEMA",
    "MAX_DOCUMENTS",
    "RepairError",
    "RepairInputError",
    "RepairRejected",
    "load_archive",
    "dry_run",
    "run",
]