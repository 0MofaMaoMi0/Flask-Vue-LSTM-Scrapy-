"""Offline regressions for SWR, owned results and commit invalidation."""

import json
import threading
import time
from datetime import timedelta
from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import event, text

from catalog import public_catalog_cache as cache, recommendation_data as recommendations
from catalog.ingest_hub import pipeline
from catalog.tmdb_connection import TMDBConnection, using
from m6_checks.test_current_state_cache import NOW, _catalog_engine, _engine_pair, _meta, _row, _source


@pytest.fixture(autouse=True)
def cache_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("MI2_CATALOG_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MI2_CATALOG_CACHE_TTL", "5")


@pytest.fixture
def refresh_threads(monkeypatch):
    threads = []

    def thread_factory(**kwargs):
        thread = threading.Thread(**kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(cache, "threading", SimpleNamespace(Lock=threading.Lock, Thread=thread_factory))
    yield threads
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()


def _expire(engine):
    entry = cache._memory_entry(engine)
    entry["checked"] = time.monotonic() - 10
    return entry


def _change_movie(engine):
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE movies SET title='revised', updated_at=:stamp WHERE movie_id=1"),
            {"stamp": NOW + timedelta(days=1)},
        )


def test_stale_requests_share_one_worker_and_refresh_incrementally(monkeypatch, refresh_threads):
    engine = _engine_pair()
    full_build = Mock(side_effect=lambda stats: recommendations._load_candidates(engine, stats=stats))
    subset_build = Mock(side_effect=lambda ids, stats: recommendations._load_candidates(
        engine, stats=stats, movie_ids=ids,
    ))
    callbacks = (full_build, subset_build, recommendations.finalize)
    first = cache.load(engine, *callbacks)
    _change_movie(engine)
    _expire(engine)
    entered, release = threading.Event(), threading.Event()
    read_watermarks = cache._watermarks

    def blocked_watermarks(engine):
        entered.set()
        assert release.wait(5)
        return read_watermarks(engine)

    monkeypatch.setattr(cache, "_watermarks", blocked_watermarks)
    stats = {}
    try:
        stale = cache.load(engine, *callbacks, stats=stats)
        assert entered.wait(5)
        for _ in range(4):
            assert cache.load(engine, *callbacks)["source"] == "memory_stale"
        assert stale["source"] == stats["source"] == "memory_stale"
        assert stale["stats"] is stats and "watermark_ms" not in stats
        assert stale["version"] == first["version"]
        assert stale["movies"][0]["title"] == first["movies"][0]["title"]
        assert len(refresh_threads) == 1 and refresh_threads[0].daemon
    finally:
        release.set()
    refresh_threads[0].join(timeout=5)
    refreshed = cache.load(engine, *callbacks)
    assert refreshed["source"] == "memory_hit"
    assert refreshed["version"] != first["version"]
    assert refreshed["movies"][0]["title"] == "revised"
    assert full_build.call_count == subset_build.call_count == 1
    assert subset_build.call_args.args[0] == [1]
    assert "watermark_ms" not in stats


@pytest.mark.parametrize("options", [
    {"ttl": 0}, {"ttl": -1}, {"force": True}, {"max_age": 5, "ttl": 120}, {"swr": False},
])
def test_stale_bypasses_swr_when_synchronous_refresh_is_required(options, refresh_threads):
    engine = _engine_pair()

    def build(stats):
        return recommendations._load_candidates(engine, stats=stats)

    cache.load(engine, build)
    _expire(engine)
    _change_movie(engine)
    result = cache.load(engine, build, **options)
    assert result["source"] == "rebuilt"
    assert result["movies"][0]["title"] == "revised"
    assert not refresh_threads


def test_failed_background_refresh_keeps_snapshot_and_allows_retry(monkeypatch, refresh_threads, caplog):
    engine = _engine_pair()
    build = Mock(return_value=[{"movie_id": 1}])
    first = cache.load(engine, build, copy_mode="readonly")
    previous = _expire(engine)
    with monkeypatch.context() as patch:
        patch.setattr(cache, "_watermarks", Mock(side_effect=RuntimeError("unavailable")))
        assert cache.load(engine, build)["source"] == "memory_stale"
        refresh_threads[0].join(timeout=5)
    assert cache._memory_entry(engine) is previous
    assert "background refresh failed: RuntimeError" in caplog.text
    assert cache.warm_async(engine, build)
    refresh_threads[1].join(timeout=5)
    second = cache.load(engine, build, copy_mode="readonly")
    assert second["source"] == "memory_hit"
    assert second["movies"] is first["movies"]
    assert build.call_count == 1


def test_cold_warm_async_is_nonblocking_and_single_flight(refresh_threads):
    engine = _engine_pair()
    entered, release = threading.Event(), threading.Event()

    def build(_stats):
        entered.set()
        assert release.wait(5)
        return [{"movie_id": 1}]

    try:
        assert cache.warm_async(engine, build)
        assert entered.wait(5)
        assert cache.peek(engine) is None
        assert not cache.warm_async(engine, build)
    finally:
        release.set()
    refresh_threads[0].join(timeout=5)
    assert cache.peek(engine)["movies"] == 1


def test_invalidation_during_background_publish_preserves_force(monkeypatch, refresh_threads):
    engine = _engine_pair()
    build = Mock(return_value=[{"movie_id": 1}])
    cache.load(engine, build)
    _expire(engine)
    _change_movie(engine)
    ready, release = threading.Event(), threading.Event()
    read_watermarks = cache._watermarks
    calls = 0

    def pause_after_last_read(engine):
        nonlocal calls
        marks = read_watermarks(engine)
        calls += 1
        if calls == 2:
            ready.set()
            assert release.wait(5)
        return marks

    monkeypatch.setattr(cache, "_watermarks", pause_after_last_read)
    try:
        assert cache.load(engine, build)["source"] == "memory_stale"
        assert ready.wait(5)
        cache.invalidate(engine)
    finally:
        release.set()
    refresh_threads[0].join(timeout=5)
    assert cache.peek(engine) is None
    assert cache._FORCE_ENGINES.get(engine) is True
    assert cache.load(engine, build)["source"] == "rebuilt"
    assert build.call_count == 3


def test_shallow_and_readonly_share_immutable_fields_and_enrich_is_local(tmp_path):
    engine = _catalog_engine(
        [_row(1)], [_source(11, 1, {
            "id": 1001, "popularity": 5, "production_countries": [{"iso_3166_1": "US"}],
        }, NOW)], mappings={1: 1001}, metadata=[_meta(1, 11)],
        credits=[
            dict(movie_id=1, credit_id=1, person_id=11, role_type="crew", job="Director", sort_order=1),
            dict(movie_id=1, credit_id=2, person_id=22, role_type="cast", job="Actor", sort_order=1),
        ],
    )
    public_rows = recommendations.load_candidates_readonly(engine)
    assert isinstance(public_rows[0], MappingProxyType)
    builder = Mock(side_effect=AssertionError("unexpected rebuild"))
    shallow = cache.load(engine, builder, copy_mode="shallow")["movies"]
    assert shallow[0]["production_countries"] is public_rows[0]["production_countries"]
    with pytest.raises(TypeError):
        shallow[0]["production_countries"][0]["iso2"] = "ZZ"
    with pytest.raises(TypeError):
        public_rows[0]["tmdb_popularity"] = 900
    shallow[0]["tmdb_popularity"] = 900

    editable = recommendations.load_candidates(engine)[0]
    other = recommendations.load_candidates(engine)[0]
    list_fields = {"genres": ["剧情"], "director_ids": [11], "actor_ids": [22]}
    for field, expected in list_fields.items():
        assert shallow[0][field] is public_rows[0][field]
        assert isinstance(editable[field], list)
        assert editable[field] == expected
        assert editable[field] is not other[field]
        editable[field].clear()
        assert other[field] == expected
        assert public_rows[0][field] == tuple(expected)
    fresh = recommendations.load_candidates(engine)[0]
    assert {field: fresh[field] for field in list_fields} == list_fields

    service = TMDBConnection(tmp_path / "online")
    service._details[1001] = {
        "movie": {"id": 1001, "popularity": 100, "revenue": 250},
        "captured_at": "2027-01-01T00:00:00+00:00",
    }
    with using(service, 1, "test"):
        enriched = recommendations.load_candidates(engine)[0]
        assert (enriched["tmdb_popularity"], enriched["revenue"], enriched["catalog_popularity"]) == (100, 250, 1)
        assert recommendations.load_candidates_readonly(engine) is public_rows
    assert recommendations.load_candidates(engine)[0]["tmdb_popularity"] == 5
    assert public_rows[0]["revenue"] is None
    builder.assert_not_called()


def test_watermarks_shard_source_refs_and_preserve_exact_credit_rows():
    last = cache.CHUNK_SIZE + 1
    ids = range(1, last + 1)
    sources = [_source(mid, mid, {"id": 1000 + mid}, NOW) for mid in ids]
    sources[-1]["source"] = "legacy_movies"  # only the direct reference fingerprints this row
    engine = _catalog_engine(
        [_row(mid) for mid in ids],
        sources,
        mappings={mid: 1000 + mid for mid in ids},
        metadata=[_meta(mid, mid) for mid in ids],
        credits=[
            dict(movie_id=1, credit_id=1, person_id=11, role_type="cast", sort_order=1),
            dict(movie_id=1, credit_id=2, person_id=22, role_type="cast", sort_order=2),
        ],
    )
    shards = {"refs": [], "credits": [], "assets": [], "history": []}

    @event.listens_for(engine, "before_cursor_execute")
    def track_shards(_conn, _cursor, sql, params, _context, _executemany):
        if "SELECT id, payload_hash FROM movie_source_records" in sql:
            shards["refs"].append(len(params))
        if "FROM movie_credits WHERE movie_id IN" in sql:
            shards["credits"].append(len(params))
        if "FROM movie_assets WHERE movie_id IN" in sql:
            shards["assets"].append(len(params))
        if "FROM movie_source_records WHERE movie_id IN" in sql:
            shards["history"].append(len(params) - 1)  # the final bind is the history depth

    before = cache._watermarks(engine)
    assert shards == dict.fromkeys(shards, [cache.CHUNK_SIZE, 1])
    with engine.begin() as connection:
        # COUNT, SUM, MAX and the old weighted checksum are identical after this swap.
        connection.execute(text("UPDATE movie_credits SET sort_order=3-sort_order"))
        connection.execute(
            text("UPDATE movie_source_records SET payload_hash='revised' WHERE id=:id"),
            {"id": last},
        )
    after = cache._watermarks(engine)
    assert {mid for mid in before if before[mid] != after[mid]} == {1, last}


@pytest.mark.parametrize("source", ["tmdb_catalog", "tmdb_supplement"])
@pytest.mark.parametrize("valid_engine", [True, False])
def test_ingest_invalidation_is_best_effort_after_success(tmp_path, monkeypatch, source, valid_engine):
    engine = _engine_pair()
    connection = SimpleNamespace(engine=engine if valid_engine else object())
    receipt = {"source": source, "commit_seal": "test", "job_id": "test",
               "status": "VERIFIED", "counts": {}, "preview": {}}
    (tmp_path / "verification-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(pipeline, "verify_archive", Mock(return_value=receipt))
    writer = Mock(return_value={"batch_id": 123})
    monkeypatch.setattr(pipeline._ingest, "run", writer)
    (tmp_path / "manifest.json").write_text(json.dumps({
        "items": [{"status": "HTTP_200", "tmdb_id": 1001}],
    }), encoding="utf-8")
    (tmp_path / "normalized").mkdir()
    (tmp_path / "normalized" / "1001.json").write_text("{}", encoding="utf-8")

    hook = Mock(side_effect=RuntimeError("unavailable"))
    call_order = Mock()
    call_order.attach_mock(writer, "write")
    call_order.attach_mock(hook, "invalidate")
    monkeypatch.setattr(cache, "invalidate", hook)
    result = pipeline.commit_archive(tmp_path, execute=True, connection=connection,
                                     importer=SimpleNamespace(apply=writer))
    assert result["status"] == "COMMITTED" and result["batch_id"] == 123
    assert (tmp_path / "commit-receipt.json").is_file()
    writer.assert_called_once()
    if valid_engine:
        hook.assert_called_once_with(engine)
        assert [call[0] for call in call_order.mock_calls] == ["write", "invalidate"]
    else:
        hook.assert_not_called()
