"""Transactional ingestion of trending board archives into ``tmdb_board_*``.

Unlike the movie pipeline this writes no movie facts at all: a snapshot row per
(channel, captured_at) plus one entry row per ranked item.  Items that are not
in the catalogue keep ``movie_id = NULL`` -- the job never creates a movie and
never guesses an identity from a title.

Replays are idempotent: an existing snapshot is reused (its batch attribution is
never rewritten) and only missing entries are inserted.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from .ingest import (
    LOCK_TIMEOUT_SECONDS,
    IngestInputError,
    IngestRejected,
    _as_mapping,
    _clock,
    _finish_batch,
    _insert_batch,
    _iso,
    _lastrowid,
    _one,
    _scalar,
)
from .trending import CHANNELS, load_archive

BOARD_SCHEMA = "tmdb-supplement-board-ingest-v1"
MAX_ENTRIES_PER_CHANNEL = 40
STATUS_VALUES = ("succeeded", "partial", "failed")


def _rows(connection, sql, params=()):
    cursor = connection.execute(sql, params)
    rows = []
    for raw in cursor.fetchall():
        mapping = _as_mapping(cursor, raw)
        if mapping is not None:
            rows.append(mapping)
    return rows


def _tmdb_mapping(connection):
    """``tmdb_id -> movie_id`` for already-mapped movies; ambiguous -> None."""
    mapping = {}
    for row in _rows(connection,
                     "SELECT movie_id,source_record_id FROM movie_source_records WHERE source=%s",
                     ("tmdb",)):
        raw = row.get("source_record_id")
        if not isinstance(raw, str):
            continue
        candidate = raw.split(":", 1)[0]
        if not candidate.isdigit():
            continue
        tmdb_id = int(candidate)
        movie_id = int(row["movie_id"])
        if tmdb_id in mapping and mapping[tmdb_id] != movie_id:
            mapping[tmdb_id] = None
        else:
            mapping.setdefault(tmdb_id, movie_id)
    return mapping


def _channel_rows(manifest, entries):
    """Validate one channel block and return its snapshot header + entries."""
    planned = {}
    for channel in CHANNELS:
        info = manifest["channels"][channel]
        status = info.get("status")
        if status not in STATUS_VALUES:
            raise IngestInputError("STATUS_INVALID", "未知榜单状态: %s" % status)
        captured_at = _clock(info.get("captured_at"), "captured_at")
        page_count = manifest.get("pages")
        if type(page_count) is not int or not 1 <= page_count <= len(entries[channel]) + 2:
            raise IngestInputError("PAGES_INVALID", "榜单页数非法")
        items = entries[channel]
        if not isinstance(items, list) or len(items) > MAX_ENTRIES_PER_CHANNEL:
            raise IngestInputError("ENTRIES_INVALID", "榜单条目超限")
        seen = set()
        rows = []
        for rank, entry in enumerate(items, 1):
            if not isinstance(entry, dict):
                raise IngestInputError("ENTRY_INVALID", "榜单条目必须是对象")
            tmdb_id = entry.get("tmdb_id")
            if type(tmdb_id) is not int or tmdb_id <= 0:
                raise IngestInputError("ENTRY_TMDB_INVALID", "榜单条目缺少 tmdb_id")
            if tmdb_id in seen:
                continue
            seen.add(tmdb_id)
            title = entry.get("title")
            if title is not None and (not isinstance(title, str) or len(title) > 500):
                raise IngestInputError("ENTRY_TITLE_INVALID", "榜单条目标题非法")
            rows.append({"rank": rank, "tmdb_id": tmdb_id, "title": title})
        planned[channel] = {
            "captured_at": captured_at,
            "status": status,
            "page_count": page_count,
            "entry_count": len(rows),
            "payload_sha256": info.get("payload_sha256"),
            "rows": rows,
        }
    return planned


def dry_run(archive_dir, *, now=None):
    """Plan a board ingest without touching any database."""
    manifest, entries = load_archive(archive_dir)
    planned = _channel_rows(manifest, entries)
    moment = _clock(now or datetime.now(timezone.utc), "now")
    for channel, block in planned.items():
        if block["captured_at"] > moment:
            raise IngestRejected("FUTURE_CAPTURED_AT", "榜单时间晚于当前时钟: " + channel)
    return {
        "command": "board-ingest-plan",
        "schema": BOARD_SCHEMA,
        "job_id": manifest.get("job_id"),
        "mode": "dry-run",
        "assumed": {"empty_state": True, "batch_id": 1},
        "channels": {
            channel: {
                "captured_at": _iso(block["captured_at"]),
                "status": block["status"],
                "page_count": block["page_count"],
                "entries": block["entry_count"],
                "ranks": [row["rank"] for row in block["rows"]],
            } for channel, block in planned.items()
        },
        "operations": sum(block["entry_count"] + 1 for block in planned.values()),
        "finished_at": _iso(moment),
    }


def _ensure_snapshot(connection, channel, block, batch_id, stamp):
    existing = _one(
        connection,
        "SELECT snapshot_id,ingest_batch_id FROM tmdb_board_snapshots "
        "WHERE board_channel=%s AND captured_at=%s",
        (channel, block["captured_at"]))
    if existing is not None:
        return int(existing["snapshot_id"]), False
    cursor = connection.execute(
        "INSERT INTO tmdb_board_snapshots (board_channel,captured_at,status,page_count,"
        "entry_count,payload_sha256,ingest_batch_id,created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (channel, block["captured_at"], block["status"], block["page_count"],
         block["entry_count"], block["payload_sha256"], batch_id, stamp))
    return _lastrowid(connection, cursor), True


def run(connection, archive_dir, *, job_ref=None, mode="write", now=None):
    """Apply one trending archive in a single transaction."""
    if mode != "write":
        raise IngestInputError("UNSUPPORTED_MODE", "run 只接受 mode='write'")
    manifest, entries = load_archive(archive_dir)
    planned = _channel_rows(manifest, entries)
    moment = _clock(now or datetime.now(timezone.utc), "now")
    for channel, block in planned.items():
        if block["captured_at"] > moment:
            raise IngestRejected("FUTURE_CAPTURED_AT", "榜单时间晚于当前时钟: " + channel)
    job_id = manifest.get("job_id")
    job_ref = job_ref or ("trending:" + str(job_id))
    if not isinstance(job_ref, str) or not job_ref:
        raise IngestInputError("JOB_REF_INVALID", "job_ref 非法")
    lock_name = "mi2_tsup_board_" + hashlib.sha1(job_ref.encode("utf-8")).hexdigest()[:32]

    mapping = _tmdb_mapping(connection)
    batch_id = None
    committed = False
    inserted = skipped = unmapped = 0
    snapshots = {}
    try:
        locked = _scalar(connection, "SELECT GET_LOCK(%s,%s)",
                         (lock_name, LOCK_TIMEOUT_SECONDS))
        if locked != 1:
            raise IngestRejected("LOCK_UNAVAILABLE", "无法取得榜单写入锁")
        batch_id = _insert_batch(connection, job_ref, moment)
        for channel, block in planned.items():
            snapshot_id, created = _ensure_snapshot(connection, channel, block,
                                                    batch_id, moment)
            snapshots[channel] = {"snapshot_id": snapshot_id, "created": created}
            existing = {int(row["tmdb_id"]) for row in _rows(
                connection,
                "SELECT tmdb_id FROM tmdb_board_entries WHERE snapshot_id=%s",
                (snapshot_id,))}
            for row in block["rows"]:
                if row["tmdb_id"] in existing:
                    skipped += 1
                    continue
                movie_id = mapping.get(row["tmdb_id"])
                if movie_id is None:
                    unmapped += 1
                connection.execute(
                    "INSERT INTO tmdb_board_entries (snapshot_id,tmdb_id,rank_position,"
                    "movie_id,title) VALUES (%s,%s,%s,%s,%s)",
                    (snapshot_id, row["tmdb_id"], row["rank"], movie_id, row["title"]))
                inserted += 1
        summary = {
            "command": "board-ingest",
            "schema": BOARD_SCHEMA,
            "job_id": job_id,
            "job_ref": job_ref,
            "mode": "write",
            "batch_id": batch_id,
            "snapshots": snapshots,
            "inserted_entries": inserted,
            "skipped_entries": skipped,
            "unmapped_entries": unmapped,
            "idempotent": inserted == 0,
            "finished_at": _iso(moment),
        }
        _finish_batch(connection, batch_id, "succeeded", moment, {
            "channels": len(planned),
            "inserted_entries": inserted,
            "skipped_entries": skipped,
            "unmapped_entries": unmapped,
        })
        connection.commit()
        committed = True
        return summary
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


__all__ = ["BOARD_SCHEMA", "dry_run", "run"]