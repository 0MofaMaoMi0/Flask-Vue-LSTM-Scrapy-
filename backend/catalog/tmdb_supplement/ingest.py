"""TMDB supplement v1 ingest: capture archive -> one database transaction.

The capture step (``capture.py``) only writes files; the writer kernel
(``writer.py``) only decides and applies per-topic operations; this module is
the single integration point that turns an archive directory into rows.

Contract
--------
``load_job(archive_dir)``
    Reads ``manifest.json`` and, for every ``tmdb_id``, the *latest successful*
    normalized document.  Everything is validated here, before the first SQL
    statement, so a bad archive never leaves a half batch behind.

``dry_run(archive_dir, now=None)``
    Runs :func:`writer.build_plan` against an assumed empty state.  No
    connection is accepted and no SQL is executed.

``run(connection, archive_dir, *, job_ref=None, mode='write', now=None)``
    One transaction, one advisory lock, one ``ingest_batches`` row, one
    ``tmdb_supplement_jobs`` row, one ``movie_source_records`` row per movie and
    one ``ingest_items`` row per movie.  Relations are written exclusively
    through :func:`writer.apply_plan`.

The connection is a DB-API style object::

    connection.execute(sql, params) -> cursor (fetchone/fetchall/lastrowid)
    connection.commit() / connection.rollback()

Rows may come back as mappings (``DictCursor``) or as tuples with
``cursor.description``; both are accepted.  Transaction control belongs to
this module: on success it commits once, on any failure it rolls back and
writes a best-effort failed job record.

Boundaries kept by construction
-------------------------------
* The sealed 16-table catalog stays read-only: ``ingest_batches``,
  ``movie_source_records`` and ``ingest_items`` are reused, while
  ``movies`` / ``movie_credits`` / ``movie_ratings_summary`` / ``movie_assets``
  / ``genres`` are never written (``writer.build_plan`` is called without
  ``allow_master_fill``).
* Tokens, DSNs and request URLs never enter ``scope_json``, ``counts_json``,
  ``error_summary`` or the returned summary; only counters and stable codes are
  stored.
* Replaying the same ``job_ref`` keeps the original attempt instant, so the
  attempt layer cannot turn an idempotent rerun into a fresh write.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import writer as _writer

MANIFEST_SCHEMA = "tmdb-supplement-capture-manifest-v1"
MAX_DOCUMENTS = 5000
MAX_RAW_BYTES = 8 * 1024 * 1024
LOCK_TIMEOUT_SECONDS = 30

_HEX64 = re.compile(r"[0-9a-fA-F]{64}\Z")
_JOB_REF = re.compile(r"[A-Za-z0-9:._-]{1,128}\Z")


class IngestError(Exception):
    """Base class for ingest failures; ``code`` is a stable, secret-free code."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


class IngestInputError(IngestError):
    """The archive (or the arguments) are unusable; nothing was written."""


class IngestRejected(IngestError):
    """A guard fired while writing; the transaction was rolled back."""


# ------------------------------------------------------------------ primitives


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _json_object(value, label):
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            raise IngestError("INVALID_JSON", label + " 不是合法 JSON")
        if not isinstance(parsed, dict):
            raise IngestError("INVALID_JSON", label + " 必须是 JSON 对象")
        return parsed
    raise IngestError("INVALID_JSON", label + " 类型不受支持")


def _positive_int(value, label):
    if type(value) is not int or value <= 0:
        raise IngestInputError("INVALID_INTEGER", label + " 必须是正整数")
    return value


def _utc(value, label):
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            raise IngestInputError("INVALID_TIME", label + " 不是 ISO8601 时间")
    else:
        raise IngestInputError("INVALID_TIME", label + " 必须是时间或 ISO8601 文本")
    if moment.tzinfo is None:
        raise IngestInputError("NAIVE_TIME", label + " 必须带时区")
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _optional_utc(value, label):
    if value is None:
        return None
    if isinstance(value, datetime) and value.tzinfo is None:
        return value
    return _utc(value, label)


def _clock(value, label):
    """Internal clock value: a naive datetime is read as UTC."""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value
    return _utc(value, label)


def _iso(value):
    """JSON-safe rendering of a clock value (summaries never leak datetime objects)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat()
    return str(value)


def _text(value, label, *, maximum, required=True):
    if value is None:
        if required:
            raise IngestInputError("MISSING_TEXT", label + " 不能为空")
        return None
    if not isinstance(value, str) or not value.strip():
        raise IngestInputError("INVALID_TEXT", label + " 必须是非空文本")
    text = value.strip()
    if len(text) > maximum:
        raise IngestInputError("TEXT_TOO_LONG", "%s 超过 %d 字符" % (label, maximum))
    return text


def _read_json(path, *, maximum=None):
    if not path.is_file():
        raise IngestInputError("FILE_MISSING", "缺少文件: " + path.name)
    if maximum is not None and path.stat().st_size > maximum:
        raise IngestInputError("FILE_TOO_LARGE", "文件超过上限: " + path.name)
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError:
        raise IngestInputError("INVALID_JSON", "无法解析 JSON: " + path.name)


def _attempt_succeeded(attempt):
    if attempt.get("status") == "HTTP_200":
        return True
    return type(attempt.get("http_status")) is int and attempt.get("http_status") == 200


# ------------------------------------------------------------------- archive


def _validate_document(document, *, tmdb_id, movie_id):
    if not isinstance(document, dict):
        raise IngestInputError("INVALID_DOCUMENT", "规范化文档必须是 JSON 对象")
    for key in ("schema_version", "movie_id", "tmdb_id", "fetched_at",
                "payload_sha256", "field_status", "facts"):
        if key not in document:
            raise IngestInputError("MISSING_DOCUMENT_FIELD", "文档缺少字段 " + key)
    if document["schema_version"] != "tmdb-supplement-v1":
        raise IngestInputError("INVALID_SCHEMA_VERSION", "文档 schema_version 不受支持")
    if _positive_int(document["movie_id"], "movie_id") != movie_id:
        raise IngestInputError("MOVIE_ID_MISMATCH", "文档 movie_id 与清单不一致")
    if _positive_int(document["tmdb_id"], "tmdb_id") != tmdb_id:
        raise IngestInputError("TMDB_ID_MISMATCH", "文档 tmdb_id 与文件名不一致")
    _utc(document["fetched_at"], "fetched_at")
    digest = document["payload_sha256"]
    if not isinstance(digest, str) or not _HEX64.match(digest):
        raise IngestInputError("INVALID_PAYLOAD_SHA256", "payload_sha256 必须是 64 位十六进制")
    status = document["field_status"]
    if not isinstance(status, dict):
        raise IngestInputError("INVALID_FIELD_STATUS", "field_status 必须是对象")
    for topic in _writer.TOPICS:
        if topic not in status:
            raise IngestInputError("MISSING_TOPIC_STATUS", "field_status 缺少 " + topic)
        if status[topic] not in _writer.STATUSES:
            raise IngestInputError("INVALID_TOPIC_STATUS", "field_status 取值不在词表内: " + topic)
    facts = document["facts"]
    if not isinstance(facts, dict):
        raise IngestInputError("INVALID_FACTS", "facts 必须是对象")
    for topic in _writer.TOPICS:
        if topic not in facts:
            raise IngestInputError("MISSING_TOPIC_FACTS", "facts 缺少 " + topic)
    return document


def load_job(archive_dir):
    """Read one capture archive and validate every document before any write."""
    root = Path(archive_dir)
    if not root.is_dir():
        raise IngestInputError("ARCHIVE_MISSING", "归档目录不存在")
    manifest = _read_json(root / "manifest.json")
    if not isinstance(manifest, dict):
        raise IngestInputError("INVALID_MANIFEST", "manifest.json 必须是对象")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise IngestInputError("INVALID_MANIFEST_SCHEMA", "manifest schema 不受支持")
    attempts = manifest.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        raise IngestInputError("INVALID_MANIFEST", "manifest.attempts 必须是非空数组")

    counters = {"attempts": len(attempts), "success": 0, "failed": 0}
    latest = {}
    for attempt in attempts:
        if not isinstance(attempt, dict):
            raise IngestInputError("INVALID_ATTEMPT", "attempt 必须是对象")
        tmdb_id = _positive_int(attempt.get("tmdb_id"), "attempt.tmdb_id")
        number = _positive_int(attempt.get("attempt"), "attempt.attempt")
        if _attempt_succeeded(attempt):
            counters["success"] += 1
            current = latest.get(tmdb_id)
            if current is None or number > current["attempt"]:
                latest[tmdb_id] = attempt
        else:
            counters["failed"] += 1
    if not latest:
        raise IngestInputError("NO_SUCCESSFUL_ATTEMPT", "归档里没有成功的采集尝试")
    if len(latest) > MAX_DOCUMENTS:
        raise IngestInputError("TOO_MANY_DOCUMENTS",
                               "归档文档数超过上限 %d" % MAX_DOCUMENTS)

    documents, raws, seen_movies = [], {}, {}
    for tmdb_id in sorted(latest):
        attempt = latest[tmdb_id]
        number = attempt["attempt"]
        document_path = root / "normalized" / str(tmdb_id) / ("attempt-%d.json" % number)
        raw_path = root / "raw" / str(tmdb_id) / ("attempt-%d.json" % number)
        document = _read_json(document_path)
        movie_id = _positive_int(
            document.get("movie_id") if isinstance(document, dict) else None,
            "document.movie_id")
        declared_movie = attempt.get("movie_id")
        if declared_movie is not None and _positive_int(declared_movie,
                                                        "attempt.movie_id") != movie_id:
            raise IngestInputError("MOVIE_ID_MISMATCH", "文档与清单 movie_id 不一致: " + str(tmdb_id))
        _validate_document(document, tmdb_id=tmdb_id, movie_id=movie_id)
        if movie_id in seen_movies:
            raise IngestInputError("DUPLICATE_MOVIE_ID", "同一批里 movie_id 重复: %d" % movie_id)
        seen_movies[movie_id] = tmdb_id
        documents.append(document)
        raws[tmdb_id] = _read_json(raw_path, maximum=MAX_RAW_BYTES)

    job_id = _text(manifest.get("job_id") or root.name, "job_id", maximum=128)
    return {
        "job_id": job_id,
        "manifest": manifest,
        "attempt_counts": counters,
        "documents": documents,
        "raws": raws,
    }


def dry_run(archive_dir, *, now=None):
    """Plan the archive against an assumed empty state; executes zero SQL."""
    job = load_job(archive_dir)
    stamp = _clock(now or datetime.now(timezone.utc), "now")
    per_topic = {topic: {} for topic in _writer.TOPICS}
    operations = 0
    for document in job["documents"]:
        planned = dict(document)
        planned["source_record_pk"] = 1  # placeholder: dry-run has no database row
        plan = _writer.build_plan(None, planned, now=stamp)
        for topic, decision in plan["decisions"].items():
            action = decision["action"]
            bucket = per_topic[topic]
            bucket[action] = bucket.get(action, 0) + 1
        operations += len(plan["operations"])
    return {
        "job_id": job["job_id"],
        "mode": "dry-run",
        "documents": len(job["documents"]),
        "operations": operations,
        "per_topic": per_topic,
        "attempts": job["attempt_counts"],
        "assumed": {"empty_state": True, "source_record_pk": 1},
        "finished_at": _iso(stamp),
    }


# ------------------------------------------------------------------ database


def _as_mapping(cursor, row):
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    description = getattr(cursor, "description", None) or []
    if description:
        return {entry[0]: value for entry, value in zip(description, row)}
    raise IngestError("ROW_SHAPE", "结果行既不是映射也没有 description")


def _one(connection, sql, params=()):
    cursor = connection.execute(sql, params)
    return _as_mapping(cursor, cursor.fetchone())


def _scalar(connection, sql, params=()):
    row = _one(connection, sql, params)
    if row is None:
        return None
    return next(iter(row.values()))


def _lastrowid(connection, cursor):
    value = getattr(cursor, "lastrowid", None)
    if value is None:
        value = getattr(connection, "lastrowid", None)
    if type(value) is not int or value <= 0:
        raise IngestError("LASTROWID_UNAVAILABLE", "无法取得自增主键")
    return value


def _same_value(left, right):
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, datetime) or isinstance(right, datetime):
        return _optional_utc(left, "left") == _optional_utc(right, "right")
    if isinstance(left, str) and isinstance(right, str):
        if left.lstrip().startswith("{") and right.lstrip().startswith("{"):
            try:
                return _canonical(json.loads(left)) == _canonical(json.loads(right))
            except ValueError:
                return left == right
        return left == right
    if isinstance(left, dict) or isinstance(right, dict):
        try:
            return _canonical(left) == _canonical(right)
        except (TypeError, ValueError):
            return left == right
    return left == right


_METADATA_SELECT = (
    "SELECT movie_id,tmdb_id,source_record_pk,payload_sha256,field_status_json,"
    "last_attempt_json,provenance_json,base_at,countries_at,companies_at,"
    "spoken_languages_at,keywords_at,releases_at,credits_at,metrics_at,fetched_at "
    "FROM tmdb_movie_metadata WHERE movie_id=%s"
)
_METADATA_RECONCILE = (
    "SELECT field_status_json,last_attempt_json,provenance_json,fetched_at,"
    "base_at,countries_at,companies_at,spoken_languages_at,keywords_at,"
    "releases_at,credits_at,metrics_at FROM tmdb_movie_metadata WHERE movie_id=%s"
)
RECONCILE_COLUMNS = (
    "field_status_json", "last_attempt_json", "provenance_json", "fetched_at",
    "base_at", "countries_at", "companies_at", "spoken_languages_at",
    "keywords_at", "releases_at", "credits_at", "metrics_at",
)


def _select_metadata(connection, movie_id, *, lock):
    sql = _METADATA_SELECT + (" FOR UPDATE" if lock else "")
    return _one(connection, sql, (movie_id,))


def _source_record_id(document):
    return "%d:supplement:%s" % (document["tmdb_id"], document["payload_sha256"])


def _ensure_source_record(connection, document, raw, batch_id, stamp):
    record_id = _source_record_id(document)
    existing = _one(
        connection,
        "SELECT id,movie_id FROM movie_source_records WHERE source=%s AND source_record_id=%s",
        ("tmdb", record_id))
    if existing is not None:
        owner = existing.get("movie_id")
        if owner != document["movie_id"]:
            raise IngestRejected("SOURCE_OWNER_CONFLICT",
                                 "来源记录已归属其它电影: " + record_id)
        return int(existing["id"])
    cursor = connection.execute(
        "INSERT INTO movie_source_records (movie_id,source,source_record_id,fetched_at,"
        "source_updated_at,payload_hash,metadata_json,ingest_batch_id,license_status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (document["movie_id"], "tmdb", record_id, stamp, None,
         document["payload_sha256"], _canonical(raw), batch_id, "allowed"))
    return _lastrowid(connection, cursor)


def _insert_batch(connection, job_ref, stamp):
    cursor = connection.execute(
        "INSERT INTO ingest_batches (source,mode,status,started_at,finished_at,"
        "source_snapshot_ref,counts_json,error_summary) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        ("tmdb", "write", "running", stamp, None, "tmdb-supplement:" + job_ref,
         _canonical({}), None))
    return _lastrowid(connection, cursor)


def _finish_batch(connection, batch_id, status, stamp, counts, error_summary=None):
    connection.execute(
        "UPDATE ingest_batches SET status=%s,finished_at=%s,counts_json=%s,"
        "error_summary=%s WHERE batch_id=%s",
        (status, stamp, _canonical(counts), error_summary, batch_id))


def _upsert_job(connection, job_ref, job, batch_id, stamp, batch_counts):
    scope = _canonical({
        "schema": MANIFEST_SCHEMA,
        "job_id": job["job_id"],
        "api_root": job["manifest"].get("api_root"),
        "language": job["manifest"].get("language"),
        "append_to_response": job["manifest"].get("append_to_response") or [],
        "documents": len(job["documents"]),
        "attempts": job["attempt_counts"],
    })
    existing = _one(connection,
                    "SELECT job_id FROM tmdb_supplement_jobs WHERE job_ref=%s", (job_ref,))
    if existing is not None:
        job_id = int(existing["job_id"])
        connection.execute(
            "UPDATE tmdb_supplement_jobs SET mode=%s,status=%s,request_budget=%s,"
            "request_count=%s,success_count=%s,failure_count=%s,skipped_count=%s,"
            "scope_json=%s,checkpoint_json=%s,ingest_batch_id=%s,heartbeat_at=%s,"
            "error_code=%s,error_summary=%s,updated_at=%s WHERE job_id=%s",
            ("write", "running", batch_counts["request_budget"],
             batch_counts["request_count"], batch_counts["success_count"],
             batch_counts["failure_count"], batch_counts["skipped_count"],
             scope, _canonical({}), batch_id, stamp, None, None, stamp, job_id))
        return job_id
    cursor = connection.execute(
        "INSERT INTO tmdb_supplement_jobs (job_ref,mode,status,request_budget,"
        "request_count,success_count,failure_count,skipped_count,scope_json,"
        "checkpoint_json,ingest_batch_id,started_at,heartbeat_at,finished_at,"
        "error_code,error_summary,created_at,updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (job_ref, "write", "running", batch_counts["request_budget"],
         batch_counts["request_count"], batch_counts["success_count"],
         batch_counts["failure_count"], batch_counts["skipped_count"],
         scope, _canonical({}), batch_id, stamp, stamp, None, None, None, stamp, stamp))
    return _lastrowid(connection, cursor)


def _finish_job(connection, job_id, status, stamp, counts, error_code=None,
                error_summary=None):
    connection.execute(
        "UPDATE tmdb_supplement_jobs SET status=%s,finished_at=%s,heartbeat_at=%s,"
        "success_count=%s,failure_count=%s,skipped_count=%s,error_code=%s,"
        "error_summary=%s,updated_at=%s WHERE job_id=%s",
        (status, stamp, stamp, counts["success_count"], counts["failure_count"],
         counts["skipped_count"], error_code, error_summary, stamp, job_id))


def _insert_item(connection, batch_id, document, status, stamp):
    connection.execute(
        "INSERT INTO ingest_items (batch_id,source_record_id,status,movie_id,error_code,"
        "payload_hash,attempt_count,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (batch_id, _source_record_id(document)[:128], status, document["movie_id"],
         None, document["payload_sha256"], 1, stamp))


def _reconcile(connection, plan):
    row = _one(connection, _METADATA_RECONCILE, (plan["movie_id"],))
    if row is None:
        raise IngestRejected("COMMIT_REQUIRES_RECONCILIATION", "写入后未找到元数据行")
    metadata = plan["metadata"]
    for column in RECONCILE_COLUMNS:
        if not _same_value(metadata.get(column), row.get(column)):
            raise IngestRejected("COMMIT_REQUIRES_RECONCILIATION",
                                 "写入结果与计划不一致: " + column)


def _record_failure(connection, job_ref, stamp, error):
    """Best-effort failure trace in a fresh transaction; never masks ``error``."""
    code = getattr(error, "code", None) or type(error).__name__
    try:
        existing = _one(connection,
                        "SELECT job_id FROM tmdb_supplement_jobs WHERE job_ref=%s",
                        (job_ref,))
        summary = str(code)[:512]
        if existing is not None:
            connection.execute(
                "UPDATE tmdb_supplement_jobs SET status=%s,finished_at=%s,heartbeat_at=%s,"
                "error_code=%s,error_summary=%s,updated_at=%s WHERE job_id=%s",
                ("failed", stamp, stamp, str(code)[:64], summary, stamp,
                 int(existing["job_id"])))
        else:
            connection.execute(
                "INSERT INTO tmdb_supplement_jobs (job_ref,mode,status,request_budget,"
                "request_count,success_count,failure_count,skipped_count,scope_json,"
                "checkpoint_json,ingest_batch_id,started_at,heartbeat_at,finished_at,"
                "error_code,error_summary,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (job_ref, "write", "failed", 0, 0, 0, 0, 0, _canonical({}),
                 _canonical({}), None, stamp, stamp, stamp, str(code)[:64],
                 summary, stamp, stamp))
        connection.commit()
    except Exception:  # noqa: BLE001 - audit trace must not mask the real error
        try:
            connection.rollback()
        except Exception:  # noqa: BLE001
            pass


def _validate_job_ref(value, job_id):
    reference = value if value is not None else ("tsup-" + job_id)
    if not isinstance(reference, str) or not _JOB_REF.match(reference):
        raise IngestInputError("INVALID_JOB_REF", "job_ref 必须是 1..128 位的安全标识")
    return reference


def _empty_counts(documents):
    return {
        "request_budget": documents,
        "request_count": documents,
        "success_count": 0,
        "failure_count": 0,
        "skipped_count": 0,
    }


def run(connection, archive_dir, *, job_ref=None, mode="write", now=None):
    """Apply one archive to the supplement tables in a single transaction."""
    if mode != "write":
        raise IngestInputError("UNSUPPORTED_MODE", "run 只接受 mode='write'，预览请用 dry_run")
    job = load_job(archive_dir)  # full validation happens before the first statement
    job_ref = _validate_job_ref(job_ref, job["job_id"])
    stamp = _clock(now or datetime.now(timezone.utc), "now")
    lock_name = "mi2_tsup_" + hashlib.sha1(job_ref.encode("utf-8")).hexdigest()[:40]

    batch_counts = _empty_counts(len(job["documents"]))
    executed = 0
    by_table = {}
    per_topic = {topic: {} for topic in _writer.TOPICS}
    job_id = None
    batch_id = None
    committed = False
    try:
        locked = _scalar(connection, "SELECT GET_LOCK(%s,%s)",
                         (lock_name, LOCK_TIMEOUT_SECONDS))
        if locked != 1:
            raise IngestRejected("LOCK_UNAVAILABLE", "无法取得补采写入锁")
        prior = _one(connection,
                     "SELECT job_id,started_at FROM tmdb_supplement_jobs WHERE job_ref=%s",
                     (job_ref,))
        # A replay of the same job_ref keeps the original attempt instant, so the
        # attempt layer cannot turn an idempotent rerun into a fresh write.
        attempt_stamp = stamp
        if prior is not None and prior.get("started_at") is not None:
            attempt_stamp = _optional_utc(prior["started_at"], "started_at") or stamp
        batch_id = _insert_batch(connection, job_ref, stamp)
        job_id = _upsert_job(connection, job_ref, job, batch_id, stamp, batch_counts)

        for document in job["documents"]:
            source_pk = _ensure_source_record(
                connection, document, job["raws"][document["tmdb_id"]], batch_id, stamp)
            existing = _select_metadata(connection, document["movie_id"], lock=True)
            planned = dict(document)
            planned["source_record_pk"] = source_pk
            plan = _writer.build_plan(existing, planned, now=attempt_stamp)
            applied = _writer.apply_plan(connection, plan)
            _reconcile(connection, plan)
            executed += applied["executed"]
            for table, count in applied["by_table"].items():
                by_table[table] = by_table.get(table, 0) + count
            for topic, decision in plan["decisions"].items():
                bucket = per_topic[topic]
                action = decision["action"]
                bucket[action] = bucket.get(action, 0) + 1
            if existing is None:
                status = "inserted"
            elif plan["operations"]:
                status = "updated"
            else:
                status = "skipped"
            _insert_item(connection, batch_id, document, status, stamp)
            batch_counts["success_count"] += 1
            if status == "skipped":
                batch_counts["skipped_count"] += 1

        summary = {
            "job_id": job_id,
            "job_ref": job_ref,
            "mode": "write",
            "documents": len(job["documents"]),
            "batch_id": batch_id,
            "executed_operations": executed,
            "by_table": by_table,
            "per_topic": per_topic,
            "idempotent": executed == 0,
            "finished_at": _iso(stamp),
            "attempts": job["attempt_counts"],
        }
        _finish_batch(connection, batch_id, "succeeded", stamp, {
            "documents": len(job["documents"]),
            "success": batch_counts["success_count"],
            "skipped": batch_counts["skipped_count"],
            "failed": batch_counts["failure_count"],
            "executed_operations": executed,
            "by_table": by_table,
            "attempts": job["attempt_counts"],
        })
        _finish_job(connection, job_id, "succeeded", stamp, batch_counts)
        connection.commit()
        committed = True
        return summary
    except Exception as error:
        if not committed:
            try:
                connection.rollback()
            except Exception:  # noqa: BLE001
                pass
        _record_failure(connection, job_ref, stamp, error)
        raise
    finally:
        try:
            connection.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
            connection.commit()
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "IngestError",
    "IngestInputError",
    "IngestRejected",
    "MANIFEST_SCHEMA",
    "MAX_DOCUMENTS",
    "load_job",
    "dry_run",
    "run",
]