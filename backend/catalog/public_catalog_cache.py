"""Shared public-catalog cache with per-engine isolation and atomic snapshots.

Only anonymous movie facts live here (rating, heat, revenue, genres, poster,
cast/director ids, production countries).  Per-user state is merged by callers
from the behaviour snapshot and must never enter this module.

Design notes
------------
* **Isolation.** Memory entries are keyed by the engine object; the disk snapshot
  name and the version hash both include a SHA-256 namespace derived from the
  engine's dialect/host/port/database (never the password).  Two engines cannot
  read each other's public data even when their watermarks happen to match.
* **Invalidation.** A narrow per-movie watermark covers every cached dependency:
  movie row update, external mapping, genre names/links, exact credit
  and asset rows, the current
  metadata row (topics + ``updated_at``) and the payload hash of the referenced
  source record.  Historical source rows that are not referenced are not pulled
  into Python, so appending history does not grow the check.
* **Stale reads.** A usable memory snapshot can serve while one daemon per
  engine checks its watermark. Cold and forced loads still validate synchronously.
* **Incremental refresh.** A small change rebuilds only the affected movies and
  merges them into a deep copy of the snapshot; a large change rebuilds all.
* **Atomicity.** Snapshots are written to a temp file, fsynced and moved with
  ``os.replace`` under an exclusive file lock.  A build that races a newer
  watermark is retried and never published under the old version.
* **Degradation.** A corrupt, truncated or structurally invalid file rebuilds.
  An unwritable directory degrades to process memory only; on Windows the
  system temp directory is never used as a fallback.

``MI2_CATALOG_STATS=1`` logs one JSON line per load (timings, cache source,
payload reads).  No identity or preference data is logged.
"""
import copy
import gzip
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

from sqlalchemy import bindparam, inspect, text
from weakref import WeakKeyDictionary

SCHEMA = "public-catalog-v2"
ALGORITHM = "watermark-v4-exact-credits"
TTL_SECONDS = 120
MAX_AGE_SECONDS = 3600
INCREMENTAL_LIMIT = 25
INCREMENTAL_RATIO = 0.15
BUILD_ATTEMPTS = 3
CHUNK_SIZE = 300
HISTORY_DEPTH = 8  # matches the bounded revenue candidate window
_PRIVATE_KEYS = {
    "favorite", "favourite", "want", "watched", "not_interested", "rating",
    "user", "user_id", "username", "preference", "behavior", "behaviour",
}
_LOG = logging.getLogger(__name__)
_LOCK = threading.Lock()
_MEMORY = WeakKeyDictionary()
_ENGINE_STATES = WeakKeyDictionary()
_GLOBAL_REVISION = 0
_FORCE_ALL = False
_FORCE_ENGINES = WeakKeyDictionary()
_FILE_LOCK = threading.Lock()


# ------------------------------------------------------------------ location


def cache_dirs():
    candidates = []
    configured = os.environ.get("MI2_CATALOG_CACHE_DIR")
    if configured:
        candidates.append(Path(configured))
    candidates.append(Path(__file__).with_name("data"))
    if os.name != "nt":
        candidates.append(Path(tempfile.gettempdir()) / "movieinsight-catalog-cache")
    return candidates


def _usable_dir():
    for directory in cache_dirs():
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".write-probe"
            probe.write_bytes(b"1")
            probe.unlink()
            return directory
        except OSError:
            continue
    return None


def persistent(engine):
    """A cache file only makes sense for a database other processes can see."""
    try:
        url = engine.url
        if url.get_backend_name() == "sqlite":
            return bool(url.database) and url.database not in (":memory:", "")
        return bool(url.database or url.host)
    except Exception:
        return False


def namespace(engine):
    """Stable, non-secret identity of the database behind an engine."""
    try:
        url = engine.url
        parts = [url.get_backend_name(), url.host or "", str(url.port or ""),
                 url.database or ""]
    except Exception:
        parts = [type(engine).__name__]
    if not persistent(engine):
        parts.append("#%d" % id(engine))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


def cache_path(engine=None):
    if engine is not None and not persistent(engine):
        return None
    directory = _usable_dir()
    if directory is None:
        return None
    suffix = namespace(engine) if engine is not None else "shared"
    return directory / ("public-catalog-%s.json.gz" % suffix)


def _ttl():
    try:
        return max(0.0, float(os.environ.get("MI2_CATALOG_CACHE_TTL", TTL_SECONDS)))
    except ValueError:
        return float(TTL_SECONDS)


# ------------------------------------------------------------------ watermark


def _hash(values):
    # Sort tokens so SQL row order cannot churn the version fingerprint.
    tokens = sorted(str(value) for value in values)
    return hashlib.sha1("\x1f".join(tokens).encode("utf-8")).hexdigest()[:16]


def _has_metadata(engine):
    try:
        return inspect(engine).has_table("tmdb_movie_metadata")
    except Exception:
        return False


def _chunks(ids):
    ids = sorted(ids)
    for offset in range(0, len(ids), CHUNK_SIZE):
        yield ids[offset:offset + CHUNK_SIZE]


def _watermarks(engine, *, has_metadata=None):
    """Per-movie narrow watermark of every cached dependency."""
    has_metadata = _has_metadata(engine) if has_metadata is None else has_metadata
    marks = {}

    class RecordTokens(list):
        def extend(self, values):
            # Keep each row's field associations intact before sorting rows.
            # A bag of scalar tokens misses path/primary or identity swaps.
            self.append(json.dumps(list(values),default=_json_default,
                                   ensure_ascii=False,separators=(",",":")))

    def bucket(movie_id):
        return marks.setdefault(int(movie_id), RecordTokens())

    with engine.connect() as connection:
        for movie_id, updated_at, status in connection.execute(text(
                "SELECT movie_id, updated_at, catalog_status FROM movies")).all():
            bucket(movie_id).extend(("movie", updated_at, status))
        for movie_id, source, external_id in connection.execute(text(
                "SELECT movie_id, source, external_id FROM movie_external_ids")).all():
            bucket(movie_id).extend(("map", source, external_id))
        for movie_id, genre_id, name in connection.execute(text(
                "SELECT l.movie_id, g.genre_id, g.name_zh FROM movie_genres l"
                " JOIN genres g ON g.genre_id=l.genre_id")).all():
            bucket(movie_id).extend(("genre", genre_id, name))
        if has_metadata:
            referenced = {}
            for (movie_id, tmdb_id, pk, updated_at,
                 base_pk, countries_pk, metrics_pk) in connection.execute(text(
                    "SELECT movie_id, tmdb_id, source_record_pk, updated_at,"
                    " JSON_EXTRACT(provenance_json,'$.base.source_record_pk'),"
                    " JSON_EXTRACT(provenance_json,'$.countries.source_record_pk'),"
                    " JSON_EXTRACT(provenance_json,'$.metrics.source_record_pk')"
                    " FROM tmdb_movie_metadata")).all():
                bucket(movie_id).extend(("meta", tmdb_id, pk, base_pk, countries_pk,
                                         metrics_pk, updated_at))
                for value in (pk, base_pk, countries_pk, metrics_pk):
                    if value is not None:
                        referenced.setdefault(int(value), set()).add(int(movie_id))
            statement = text(
                "SELECT id, payload_hash FROM movie_source_records"
                " WHERE id IN :ids").bindparams(bindparam("ids", expanding=True))
            for ids in _chunks(referenced):
                for pk, payload_hash in connection.execute(statement, {"ids": ids}):
                    for movie_id in referenced[int(pk)]:
                        bucket(movie_id).extend(("src", pk, payload_hash))

        credits = text(
            "SELECT movie_id, credit_id, person_id, role_type, job, sort_order"
            " FROM movie_credits WHERE movie_id IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        assets = text(
            "SELECT movie_id, asset_id, asset_type, source, source_path,"
            " is_primary, license_status, updated_at FROM movie_assets WHERE movie_id IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        # Keep the ranking window local to indexed movie IDs. The window transfers
        # only recent hashes, even if some movies have long capture histories.
        history = text(
            "SELECT movie_id, id, source, fetched_at, payload_hash FROM ("
            " SELECT movie_id, id, source, fetched_at, payload_hash,"
            " ROW_NUMBER() OVER (PARTITION BY movie_id"
            " ORDER BY fetched_at DESC, id DESC) AS rn"
            " FROM movie_source_records WHERE movie_id IN :ids"
            " AND source IN ('tmdb','legacy_movies2')) ranked"
            " WHERE rn <= :depth"
        ).bindparams(bindparam("ids", expanding=True))

        digests = {}
        # Digest each shard before loading more credit and asset rows. Row tokens
        # retain field associations, including compensating changes across credits.
        for ids in _chunks(marks):
            for kind, query in (("credit", credits), ("asset", assets), ("hist", history)):
                params = {"ids": ids}
                if kind == "hist":
                    params["depth"] = HISTORY_DEPTH
                for row in connection.execute(query, params):
                    bucket(row[0]).extend((kind,) + tuple(row[1:]))
            for movie_id in ids:
                digests[movie_id] = _hash(marks.pop(movie_id))
    return digests


def _version(namespace_value, marks):
    digest = hashlib.sha256()
    digest.update(("%s|%s|%s|" % (SCHEMA, ALGORITHM, namespace_value)).encode("utf-8"))
    for movie_id in sorted(marks):
        digest.update(("%d:%s;" % (movie_id, marks[movie_id])).encode("utf-8"))
    return digest.hexdigest()[:32]


# ------------------------------------------------------------------ document


_SCALAR_TYPES = (str, int, float, bool, type(None))


def _clone_value(value):
    """Thaw a snapshot into mutable catalog containers owned by the caller."""
    t = type(value)
    if t in (dict, MappingProxyType):
        return {k: _clone_value(v) for k, v in value.items()}
    if t in (list, tuple):
        return [_clone_value(item) for item in value]
    if t in _SCALAR_TYPES:
        return value
    if t is frozenset:
        return copy.deepcopy(set(value))
    return copy.deepcopy(value)


def _readonly_value(value):
    t = type(value)
    if t is dict:
        return MappingProxyType({k: _readonly_value(v) for k, v in value.items()})
    if t is list:
        return tuple(_readonly_value(item) for item in value)
    if t in _SCALAR_TYPES:
        return value
    if t is set:
        return frozenset(_readonly_value(item) for item in value)
    if t is tuple:
        return tuple(_readonly_value(item) for item in value)
    return copy.deepcopy(value)


def _valid_row(row):
    return (isinstance(row, dict) and isinstance(row.get("movie_id"), int)
            and not (_PRIVATE_KEYS & set(row)))


def _valid_doc(doc, *, namespace_value=None, max_age=MAX_AGE_SECONDS):
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return False
    if not isinstance(doc.get("version"), str) or not isinstance(doc.get("watermarks"), dict):
        return False
    if namespace_value is not None and doc.get("namespace") != namespace_value:
        return False
    movies = doc.get("movies")
    if not isinstance(movies, list) or not movies or not all(_valid_row(row) for row in movies):
        return False
    try:
        built = datetime.fromisoformat(doc["built_at"])
    except (KeyError, ValueError, TypeError):
        return False
    return (datetime.now(timezone.utc) - built).total_seconds() <= max_age


def _read(path, expected, *, namespace_value, max_age):
    try:
        with gzip.open(path, "rb") as handle:
            doc = json.loads(handle.read())
    except (OSError, ValueError, EOFError, UnicodeDecodeError):
        return None
    if not _valid_doc(doc, namespace_value=namespace_value, max_age=max_age):
        return None
    if doc.get("version") != expected:
        return None
    return doc


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    from decimal import Decimal
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _write(path, doc):
    payload = json.dumps(doc, ensure_ascii=False, separators=(",", ":"),
                         default=_json_default).encode("utf-8")
    handle = tempfile.NamedTemporaryFile(
        dir=str(path.parent), prefix=".public-catalog-", suffix=".tmp", delete=False)
    temp = Path(handle.name)
    try:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as stream:
            stream.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(temp, path)
        return len(payload)
    finally:
        try:
            if not handle.closed:
                handle.close()
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)


def _file_guard(path):
    class Guard:
        def __enter__(self):
            self.handle = None
            try:
                import fcntl
                self.handle = open(str(path) + ".lock", "a+")
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError):
                self.handle = None
            return self

        def __exit__(self, *exc):
            if self.handle is not None:
                try:
                    import fcntl
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
                finally:
                    self.handle.close()
            return False
    return Guard()


class _null_guard:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ------------------------------------------------------------------ lifecycle


class _EngineState:
    def __init__(self):
        self.lock = threading.Lock()
        self.revision = 0
        self.refreshing = False


def invalidate(engine=None):
    """Drop the snapshot and force this engine's next load to rebuild."""
    global _FORCE_ALL, _GLOBAL_REVISION
    with _LOCK:
        if engine is None:
            _GLOBAL_REVISION += 1
            _FORCE_ALL = True
            for known_engine in _ENGINE_STATES:
                _FORCE_ENGINES[known_engine] = True
            _MEMORY.clear()
        else:
            state = _ENGINE_STATES.get(engine)
            if state is None:
                state = _ENGINE_STATES[engine] = _EngineState()
            state.revision += 1
            _MEMORY.pop(engine, None)
            _FORCE_ENGINES[engine] = True


def stats_enabled():
    return os.environ.get("MI2_CATALOG_STATS", "").strip().lower() in ("1", "true", "yes", "on")


def _engine_state(engine):
    with _LOCK:
        state = _ENGINE_STATES.get(engine)
        if state is None:
            state = _EngineState()
            _ENGINE_STATES[engine] = state
        return state


def _memory_entry(engine):
    if engine is None:
        return None
    with _LOCK:
        return _MEMORY.get(engine)


def peek(engine=None):
    entry = _memory_entry(engine)
    return None if entry is None else {"version": entry["version"],
                                       "movies": len(entry["readonly"]),
                                       "namespace": entry["namespace"]}


def cache_revision(engine=None):
    """Return a revision tuple that advances on invalidation or snapshot version change."""
    with _LOCK:
        state = _ENGINE_STATES.get(engine) if engine is not None else None
        entry = _MEMORY.get(engine) if engine is not None else None
        return (
            _GLOBAL_REVISION,
            state.revision if state is not None else 0,
            entry["version"] if entry is not None else None,
        )


def _start_refresh(engine, build_all, build_subset, finalize, *, ttl=None,
                   max_age=MAX_AGE_SECONDS, stale_entry=None):
    state = _engine_state(engine)
    with _LOCK:
        if state.refreshing:
            return False
        if stale_entry is not None and _MEMORY.get(engine) is not stale_entry:
            return False
        state.refreshing = True

    def refresh():
        try:
            result = load(engine, build_all, build_subset, finalize, ttl=ttl,
                          max_age=max_age, copy_mode="readonly", swr=False)
            if result["source"] == "rebuilt_racing":
                _LOG.warning("public catalog background refresh did not stabilize")
        except Exception as error:
            _LOG.warning("public catalog background refresh failed: %s", type(error).__name__)
        finally:
            with _LOCK:
                state.refreshing = False

    try:
        threading.Thread(target=refresh, name="public-catalog-refresh", daemon=True).start()
    except Exception as error:
        with _LOCK:
            state.refreshing = False
        _LOG.warning("public catalog background refresh could not start: %s", type(error).__name__)
        return False
    return True


def warm_async(engine, build_all, build_subset=None, finalize=None):
    """Schedule a cold load or due revalidation. Return whether a worker started."""
    return _start_refresh(engine, build_all, build_subset, finalize)


def load(engine, build_all, build_subset=None, finalize=None, *, ttl=None,
         max_age=MAX_AGE_SECONDS, stats=None, copy_mode="clone", swr=True, force=False):
    """Return movies, version, source and stats. TTL zero validates every load."""
    counters = stats if isinstance(stats, dict) else {}
    counters.setdefault("json_reads", 0)
    started = time.monotonic()
    ttl = _ttl() if ttl is None else ttl
    ns = namespace(engine)

    entry = _memory_entry(engine)
    if not force and ttl > 0 and entry and entry["namespace"] == ns:
        age = time.monotonic() - entry["checked"]
        if age <= max_age:
            if age < ttl:
                return _result(entry, "memory_hit", counters, started, copy_mode)
            if swr:
                _start_refresh(engine, build_all, build_subset, finalize,
                               ttl=ttl, max_age=max_age, stale_entry=entry)
                return _result(entry, "memory_stale", counters, started, copy_mode)

    state = _engine_state(engine)
    with state.lock:
        entry, source = _refresh(engine, state, ns, build_all, build_subset, finalize,
                                 ttl, max_age, force, counters, started)
    return _result(entry, source, counters, started, copy_mode)


def _refresh(engine, state, ns, build_all, build_subset, finalize, ttl, max_age,
             force, counters, started):
    global _FORCE_ALL
    with _LOCK:
        if _FORCE_ALL:
            _FORCE_ALL = False
            _FORCE_ENGINES[engine] = True
        force = force or _FORCE_ENGINES.get(engine, False)
        revision = (_GLOBAL_REVISION, state.revision)
        entry = _MEMORY.get(engine)
    if not force and entry and entry["namespace"] == ns:
        age = time.monotonic() - entry["checked"]
        if age < ttl and age <= max_age:
            return entry, "memory_hit"

    marks = _watermarks(engine)
    version = _version(ns, marks)
    counters["watermark_ms"] = round((time.monotonic() - started) * 1000, 1)
    if not force and entry and entry["version"] == version and entry["namespace"] == ns:
        entry = dict(entry, checked=time.monotonic())
        source = "memory_hit"
    else:
        path = cache_path(engine)
        doc = _read(path, version, namespace_value=ns, max_age=max_age) if path and not force else None
        source = "disk_hit"
        if doc is None:
            doc, source = _rebuild_until_stable(
                engine, ns, build_all, build_subset, finalize, marks, version,
                path, force, max_age, counters, entry,
            )
        entry = {
            "version": doc["version"], "namespace": ns,
            "readonly": tuple(_readonly_value(row) for row in doc["movies"]),
            "marks": doc["watermarks"], "checked": time.monotonic(),
        }

    if source != "rebuilt_racing":
        with _LOCK:
            # A write may invalidate while a refresh is between its final query and
            # publication. Leave its force request pending for the next load.
            if revision == (_GLOBAL_REVISION, state.revision):
                _MEMORY[engine] = entry
                _FORCE_ENGINES.pop(engine, None)
    return entry, source


def _result(entry, source, counters, started, copy_mode):
    if copy_mode == "readonly":
        movies = entry["readonly"]
    elif copy_mode == "shallow":
        movies = [dict(row) for row in entry["readonly"]]
    else:
        movies = [_clone_value(row) for row in entry["readonly"]]
    counters.update(source=source, movies=len(movies), version=entry["version"],
                    namespace=entry["namespace"],
                    load_ms=round((time.monotonic() - started) * 1000, 1))
    _maybe_log(counters)
    return {"movies": movies, "version": entry["version"], "source": source, "stats": counters}


def _rebuild_until_stable(engine, ns, build_all, build_subset, finalize, marks, version,
                          path, force, max_age, counters, previous):
    build_started = time.time()  # built_at is wall-clock; compare in that domain
    with _FILE_LOCK:
        guard = _file_guard(path) if path is not None else _null_guard()
        with guard:
            if path is not None and not force:
                existing_doc = _read(path, version, namespace_value=ns, max_age=max_age)
                if existing_doc is not None:
                    return existing_doc, "disk_hit"
            for attempt in range(BUILD_ATTEMPTS):
                doc = _build(build_all, build_subset, finalize, marks, version,
                             ns, counters, None if force else previous)
                refreshed = _watermarks(engine)
                refreshed_version = _version(ns, refreshed)
                if refreshed_version == version:
                    if path is not None:
                        try:
                            if not _newer_on_disk(path, ns, version, max_age, build_started):
                                counters["cache_bytes"] = _write(path, doc)
                        except OSError as error:
                            _LOG.warning("public catalog cache not written: %s", type(error).__name__)
                    return doc, "rebuilt"
                marks, version = refreshed, refreshed_version
                counters["raced"] = attempt + 1
            counters["unstable"] = True
            return doc, "rebuilt_racing"


def _read_any(path, ns, max_age):
    """Disk snapshot of the same engine regardless of version (merge base)."""
    try:
        with gzip.open(path, "rb") as handle:
            doc = json.loads(handle.read())
    except (OSError, ValueError, EOFError, UnicodeDecodeError):
        return None
    return doc if _valid_doc(doc, namespace_value=ns, max_age=max_age) else None


def _newer_on_disk(path, ns, version, max_age, started_at):
    """Another process published while this build ran; do not clobber it."""
    doc = _read_any(path, ns, max_age)
    if doc is None or doc.get("version") == version:
        return False
    try:
        built = datetime.fromisoformat(doc["built_at"])
    except (KeyError, ValueError, TypeError):
        return False
    if built.tzinfo is None:
        built = built.replace(tzinfo=timezone.utc)
    # ``started_at`` is wall-clock epoch seconds (same domain as built_at).
    return built.timestamp() > started_at


def _build(build_all, build_subset, finalize, marks, version, ns, counters, previous):
    changed = []
    if previous and previous["namespace"] == ns:
        old = previous.get("marks") or {}
        changed = sorted(set(old) ^ set(marks) | {
            movie_id for movie_id in set(old) & set(marks) if old[movie_id] != marks[movie_id]})
    limit = max(INCREMENTAL_LIMIT, int(len(marks) * INCREMENTAL_RATIO))
    if (previous and previous["namespace"] == ns and build_subset and finalize and changed
            and len(changed) <= limit and previous["version"] != version):
        counters.update(incremental=True, changed_movies=len(changed))
        movies = {row["movie_id"]: row for row in map(_clone_value, previous["readonly"])}
        for movie_id in changed:
            movies.pop(movie_id, None)
        present = [movie_id for movie_id in changed if movie_id in marks]
        if present:
            for row in build_subset(present,counters):
                movies[row["movie_id"]] = _clone_value(row)
        merged = [movies[movie_id] for movie_id in sorted(movies)]
        finalize(merged)
        return {"schema": SCHEMA, "namespace": ns, "version": version,
                "algorithm": ALGORITHM,
                "built_at": datetime.now(timezone.utc).isoformat(),
                "watermarks": marks, "movies": merged}
    counters.update(incremental=False, changed_movies=len(changed))
    movies = build_all(counters)
    if finalize:
        finalize(movies)
    return {"schema": SCHEMA, "namespace": ns, "version": version,
            "algorithm": ALGORITHM,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "watermarks": marks, "movies": movies}


def _maybe_log(counters):
    if stats_enabled():
        _LOG.info("public_catalog_cache %s", json.dumps(counters, ensure_ascii=False, sort_keys=True))


__all__ = ["SCHEMA", "TTL_SECONDS", "MAX_AGE_SECONDS", "cache_dirs", "cache_path",
           "invalidate", "load", "namespace", "peek", "persistent", "stats_enabled",
           "warm_async"]
