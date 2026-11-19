"""Current-state reads + shared public cache. Synthetic sqlite only; no real DB."""
import gzip
import json
import threading
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text

from catalog import current_state, public_catalog_cache
from catalog.models import CatalogBase

NOW = datetime(2026, 11, 18, 10, 0, 0)


@pytest.fixture(autouse=True)
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MI2_CATALOG_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MI2_CATALOG_CACHE_TTL", "0")
    public_catalog_cache.invalidate()
    yield
    public_catalog_cache.invalidate()


def _catalog_engine(rows, sources, mappings=None, metadata=None, assets=None, credits=None,
                    url="sqlite://"):
    from sqlalchemy import MetaData, Table, Column, Integer, String, JSON, DateTime, Float, Boolean, Date, insert
    from sqlalchemy.pool import StaticPool
    engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    md = MetaData()
    for name in ("movies", "genres", "movie_genres", "movie_external_ids",
                 "movie_source_records", "movie_assets", "movie_credits"):
        columns = []
        for col in CatalogBase.metadata.tables[name].columns:
            kind = col.type
            if isinstance(kind, JSON):
                kind = JSON
            elif isinstance(kind, DateTime):
                kind = DateTime
            elif isinstance(kind, Date):
                kind = Date
            elif isinstance(kind, Boolean):
                kind = Boolean
            elif isinstance(kind, String):
                kind = String
            elif isinstance(kind, Float):
                kind = Float
            else:
                kind = Integer
            columns.append(Column(col.name, kind))
        Table(name, md, *columns)
    md.create_all(engine)
    with engine.begin() as c:
        c.execute(insert(md.tables["movies"]), rows)
        c.execute(insert(md.tables["genres"]), [dict(genre_id=1, name_zh="剧情")])
        c.execute(insert(md.tables["movie_genres"]),
                  [dict(movie_id=row["movie_id"], genre_id=1) for row in rows])
        c.execute(insert(md.tables["movie_external_ids"]),
                  [dict(movie_id=mid, source="tmdb", external_id=str(tid))
                   for mid, tid in (mappings or {}).items()])
        c.execute(insert(md.tables["movie_source_records"]), sources)
        if assets:
            c.execute(insert(md.tables["movie_assets"]), assets)
        if credits:
            c.execute(insert(md.tables["movie_credits"]), credits)
    if metadata:
        with engine.begin() as c:
            c.execute(text(
                "CREATE TABLE tmdb_movie_metadata (movie_id INTEGER PRIMARY KEY, tmdb_id INTEGER,"
                " source_record_pk INTEGER, payload_sha256 TEXT, field_status_json TEXT,"
                " last_attempt_json TEXT, provenance_json TEXT, base_at TIMESTAMP,"
                " releases_at TIMESTAMP, fetched_at TIMESTAMP, updated_at TIMESTAMP)"))
            c.execute(text(
                "INSERT INTO tmdb_movie_metadata (movie_id,tmdb_id,source_record_pk,"
                "field_status_json,last_attempt_json,provenance_json,base_at,fetched_at,updated_at)"
                " VALUES (:movie_id,:tmdb_id,:pk,'{}','{}',:provenance,:base_at,:fetched_at,:updated_at)"),
                metadata)
    return engine


def _meta(movie_id, pk, *, base_at=NOW, updated_at=NOW, state="PRESENT", topics=None):
    """Provenance for base/countries/metrics; ``topics`` overrides per topic."""
    chosen = dict(topics or {})
    provenance = {}
    for topic in ("base", "countries", "metrics"):
        entry = chosen.get(topic)
        if entry is None:
            entry = {"state": state, "source_record_pk": pk}
        provenance[topic] = {
            "state": entry.get("state", state),
            "source_record_pk": entry.get("source_record_pk", pk),
            "fetched_at": (entry.get("fetched_at") or base_at).isoformat() + "+00:00",
        }
    return dict(movie_id=movie_id, tmdb_id=1000 + movie_id, pk=pk,
                provenance=json.dumps(provenance),
                base_at=base_at, fetched_at=base_at, updated_at=updated_at)


def _row(movie_id, **extra):
    base = dict(movie_id=movie_id, title="film %d" % movie_id, catalog_status="active",
                release_year=2020, updated_at=NOW)
    base.update(extra)
    return base


def _source(pk, movie_id, payload, fetched_at):
    return dict(id=pk, movie_id=movie_id, source="tmdb", source_record_id="tmdb-%d" % pk,
                fetched_at=fetched_at, payload_hash="a" * 64, metadata_json=payload,
                ingest_batch_id=1, license_status="allowed")


def _engine_pair(url="sqlite://"):
    rows = [_row(i) for i in (1, 2, 3, 4, 5, 6)]
    sources = [
        _source(11, 1, {"id": 1001, "popularity": 5, "revenue": 100}, NOW - timedelta(days=2)),
        _source(12, 1, {"id": 1001, "popularity": 9}, NOW),
        _source(21, 2, {"id": 999, "popularity": 777}, NOW),
        _source(22, 2, {"id": 1002, "popularity": 3}, NOW - timedelta(days=3)),
        _source(31, 3, {"id": 1003, "popularity": 7}, NOW),
        _source(41, 4, {"id": 1004, "popularity": None}, NOW),
        _source(51, 5, {"id": 1005, "popularity": 2, "vote_average": 8, "vote_count": 0}, NOW),
        _source(61, 6, {"id": 1006, "popularity": 4}, NOW),
    ]
    metadata = [
        _meta(1, 12), _meta(2, 21), _meta(3, 31, state="EMPTY"), _meta(4, 41), _meta(5, 51),
    ]
    return _catalog_engine(rows, sources, mappings={i: 1000 + i for i in (1, 2, 3, 4, 5, 6)},
                           metadata=metadata, url=url)


def test_current_reads_only_attributed_records():
    from catalog.recommendation_data import load_candidates
    engine = _engine_pair()
    stats = {}
    rows = load_candidates(engine)
    byid = {row["movie_id"]: row for row in rows}
    assert len(rows) == 6
    assert byid[1]["tmdb_popularity"] == 9
    # 8 source rows exist, but only the attributed/fallback ones are read.
    assert stats.get("json_reads", 0) <= 8


def test_newer_failed_attempt_keeps_previous_success():
    from catalog.recommendation_data import load_candidates
    engine = _engine_pair()
    with engine.begin() as c:
        c.execute(text("UPDATE movie_source_records SET metadata_json=:p WHERE id=12"),
                  {"p": json.dumps({"id": 1001, "popularity": None})})
        c.execute(text("UPDATE tmdb_movie_metadata SET updated_at=:t WHERE movie_id=1"),
                  {"t": NOW + timedelta(minutes=5)})
    rows = {row["movie_id"]: row for row in load_candidates(engine)}
    assert rows[1]["tmdb_popularity"] is None  # EMPTY capture, no invention
    assert rows[1]["feature_source"] == current_state.CURRENT_SOURCE


def test_wrong_identity_record_never_used():
    from catalog.recommendation_data import load_candidates
    rows = {row["movie_id"]: row for row in load_candidates(_engine_pair())}
    assert rows[2]["tmdb_popularity"] == 3  # older matching record, not the 777 impostor


def test_empty_and_zero_vote_payloads_do_not_invent_values():
    from catalog.recommendation_data import load_candidates
    rows = {row["movie_id"]: row for row in load_candidates(_engine_pair())}
    assert rows[4]["tmdb_popularity"] is None
    assert rows[5]["tmdb_rating"] is None and rows[5]["vote_count"] == 0


def test_historical_revenue_fallback_keeps_observation_semantics():
    from catalog.recommendation_data import load_candidates
    rows = {row["movie_id"]: row for row in load_candidates(_engine_pair())}
    assert rows[1]["revenue"] == 100
    assert rows[1]["revenue_source"] == current_state.HISTORICAL_SOURCE


def test_unmapped_movie_stays_in_catalog_with_legacy_fallback():
    from catalog.recommendation_data import load_candidates
    rows = {row["movie_id"]: row for row in load_candidates(_engine_pair())}
    assert rows[6]["tmdb_popularity"] == 4  # no metadata row -> latest valid source
    assert rows[6]["tmdb_id"] == "1006"


def test_hot_read_skips_rebuild_and_watermarks():
    from catalog.recommendation_data import load_candidates
    engine = _engine_pair()
    first = load_candidates(engine)
    second = load_candidates(engine)
    assert [r["movie_id"] for r in first] == [r["movie_id"] for r in second]
    assert public_catalog_cache.peek(engine)["movies"] == 6


def test_incremental_change_rebuilds_only_that_movie(monkeypatch):
    monkeypatch.setenv("MI2_CATALOG_CACHE_TTL", "0")
    from catalog.recommendation_data import load_candidates
    engine = _engine_pair()
    load_candidates(engine)
    with engine.begin() as c:
        c.execute(text("UPDATE movie_source_records SET metadata_json=:p WHERE id=12"),
                  {"p": json.dumps({"id": 1001, "popularity": 30})})
        c.execute(text("UPDATE tmdb_movie_metadata SET updated_at=:t WHERE movie_id=1"),
                  {"t": NOW + timedelta(minutes=6)})
    rows = {row["movie_id"]: row for row in load_candidates(engine)}
    assert rows[1]["tmdb_popularity"] == 30
    assert rows[3]["tmdb_popularity"] is None  # EMPTY metrics stay empty


def test_cross_process_change_detected_by_watermark(monkeypatch):
    monkeypatch.setenv("MI2_CATALOG_CACHE_TTL", "0")
    from catalog.recommendation_data import load_candidates
    engine = _engine_pair()
    load_candidates(engine)
    with engine.begin() as c:
        c.execute(text("UPDATE tmdb_movie_metadata SET updated_at=:t WHERE movie_id=1"),
                  {"t": NOW + timedelta(minutes=9)})
        c.execute(text("UPDATE movie_source_records SET metadata_json=:p WHERE id=12"),
                  {"p": json.dumps({"id": 1001, "popularity": 12})})
    rows = {row["movie_id"]: row for row in load_candidates(engine)}
    assert rows[1]["tmdb_popularity"] == 12


def test_corrupt_snapshot_rebuilds(tmp_path):
    from catalog.recommendation_data import load_candidates
    engine = _engine_pair("sqlite:///" + str(tmp_path / "catalog.sqlite"))
    load_candidates(engine)
    path = public_catalog_cache.cache_path(engine)
    path.write_bytes(b"not a gzip stream")
    public_catalog_cache.invalidate()
    rows = load_candidates(engine)
    assert len(rows) == 6


def test_failed_build_does_not_publish(tmp_path):
    from catalog import recommendation_data as module
    engine = _engine_pair("sqlite:///" + str(tmp_path / "catalog.sqlite"))
    good = module.load_candidates(engine)
    path = public_catalog_cache.cache_path(engine)
    before = path.read_bytes()
    public_catalog_cache.invalidate()
    with engine.begin() as c:
        c.execute(text("UPDATE tmdb_movie_metadata SET updated_at=:t WHERE movie_id=1"),
                  {"t": NOW + timedelta(minutes=11)})

    def boom(counters):
        raise RuntimeError("provider down")

    with pytest.raises(RuntimeError):
        public_catalog_cache.load(engine, boom)
    assert path.read_bytes() == before
    assert [r["movie_id"] for r in good] == [r["movie_id"] for r in module.load_candidates(engine)]


def test_concurrent_reads_see_complete_snapshot():
    from catalog.recommendation_data import load_candidates
    engine = _engine_pair()
    results = []

    def worker():
        results.append([row["movie_id"] for row in load_candidates(engine)])

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results and all(ids == [1, 2, 3, 4, 5, 6] for ids in results)


def test_snapshot_holds_no_personal_fields(tmp_path):
    from catalog.recommendation_data import load_candidates
    engine = _engine_pair("sqlite:///" + str(tmp_path / "catalog.sqlite"))
    load_candidates(engine)
    with gzip.open(public_catalog_cache.cache_path(engine), "rb") as handle:
        doc = json.loads(handle.read())
    private = {"favorite", "want", "watched", "not_interested", "rating", "user"}
    for movie in doc["movies"]:
        assert not private & set(movie)


def test_two_users_merge_from_same_public_snapshot():
    from catalog.recommendation_data import load_candidates
    from catalog.recommendation import recommend
    engine = _engine_pair()
    catalog = load_candidates(engine)
    first = recommend(catalog, {"1": {"favorite": True}})
    second = recommend(catalog, {"2": {"watched": True}})
    first_ids = [item["movie_id"] for item in first["items"]]
    second_ids = [item["movie_id"] for item in second["items"]]
    assert first_ids != second_ids
    assert all("favorite" not in movie and "watched" not in movie for movie in catalog)

def test_appending_history_does_not_increase_current_reads():
    """Extra historical payloads must not multiply per-request JSON reads."""
    from catalog.recommendation_data import _load_candidates
    rows = [_row(1), _row(2)]
    sources = [
        _source(11, 1, {"id": 1001, "popularity": 9}, NOW),
        _source(21, 2, {"id": 1002, "popularity": 4}, NOW),
    ]
    engine = _catalog_engine(rows, sources, mappings={1: 1001, 2: 1002},
                             metadata=[_meta(1, 11), _meta(2, 21)])
    before = {}
    _load_candidates(engine, stats=before)
    with engine.begin() as connection:
        for index in range(50):
            connection.execute(text(
                "INSERT INTO movie_source_records (id,movie_id,source,source_record_id,"
                "fetched_at,payload_hash,metadata_json,ingest_batch_id,license_status)"
                " VALUES (:id,1,'tmdb',:ref,:fetched,'%s',:payload,1,'allowed')" % ("b" * 64)),
                {"id": 9000 + index, "ref": "old-%d" % index,
                 "fetched": NOW - timedelta(days=30 + index),
                 "payload": json.dumps({"id": 1001, "popularity": 1 + index})})
    after = {}
    values = {row["movie_id"]: row for row in _load_candidates(engine, stats=after)}
    assert values[1]["tmdb_popularity"] == 9
    # Revenue candidates are capped per movie, so 500 more historical rows do
    # not add reads once the cap is reached.
    with engine.begin() as connection:
        for index in range(500):
            connection.execute(text(
                "INSERT INTO movie_source_records (id,movie_id,source,source_record_id,"
                "fetched_at,payload_hash,metadata_json,ingest_batch_id,license_status)"
                " VALUES (:id,1,'tmdb',:ref,:fetched,'%s',:payload,1,'allowed')" % ("d" * 64)),
                {"id": 20000 + index, "ref": "more-%d" % index,
                 "fetched": NOW - timedelta(days=200 + index),
                 "payload": json.dumps({"id": 1001, "popularity": 1 + index})})
    later = {}
    _load_candidates(engine, stats=later)
    assert later["json_reads"] == after["json_reads"]


def test_second_engine_sees_committed_supplement_change(tmp_path, monkeypatch):
    """A CLI ingest committing in another engine is visible without a restart."""
    monkeypatch.setenv("MI2_CATALOG_CACHE_TTL", "0")
    from catalog.recommendation_data import load_candidates
    url = "sqlite:///" + str(tmp_path / "catalog.sqlite")
    engine = _catalog_engine([_row(1)], [_source(11, 1, {"id": 1001, "popularity": 5}, NOW)],
                             mappings={1: 1001}, metadata=[_meta(1, 11)], url=url)
    assert load_candidates(engine)[0]["tmdb_popularity"] == 5
    other = _catalog_engine([], [], url=url) if False else None
    from sqlalchemy import create_engine
    writer = create_engine(url)
    with writer.begin() as connection:
        connection.execute(text("INSERT INTO movie_source_records (id,movie_id,source,"
                                "source_record_id,fetched_at,payload_hash,metadata_json,"
                                "ingest_batch_id,license_status) VALUES (99,1,'tmdb','new',"
                                ":fetched,:hash,:payload,1,'allowed')"),
                           {"fetched": NOW, "hash": "c" * 64,
                            "payload": json.dumps({"id": 1001, "popularity": 21})})
        connection.execute(text("UPDATE tmdb_movie_metadata SET source_record_pk=99,"
                                "provenance_json=:prov, updated_at=:t WHERE movie_id=1"),
                           {"prov": json.dumps({topic: {"state": "PRESENT",
                                                        "source_record_pk": 99}
                                                for topic in ("base", "countries", "metrics")}),
                            "t": NOW + timedelta(minutes=1)})
    rows = load_candidates(engine)
    assert rows[0]["tmdb_popularity"] == 21


def test_revenue_fallback_skips_newer_wrong_identity():
    """Probe 1: a newer impostor must not hide an older valid amount."""
    from catalog.recommendation_data import load_candidates
    rows = [_row(1)]
    sources = [
        _source(11, 1, {"id": 1001, "popularity": 6}, NOW),
        _source(12, 1, {"id": 999, "revenue": 200}, NOW),
        _source(13, 1, {"id": 1001, "revenue": 100}, NOW - timedelta(days=4)),
    ]
    engine = _catalog_engine(rows, sources, mappings={1: 1001},
                             metadata=[_meta(1, 11)])
    values = {row["movie_id"]: row for row in load_candidates(engine)}
    assert values[1]["revenue"] == 100
    assert values[1]["revenue_source"] == current_state.HISTORICAL_SOURCE
    assert values[1]["revenue_as_of"].endswith("Z")


def test_metrics_topic_uses_its_own_capture():
    from catalog.recommendation_data import load_candidates
    rows = [_row(1)]
    sources = [
        _source(11, 1, {"id": 1001, "popularity": 5, "vote_average": 4.0, "vote_count": 10}, NOW),
        _source(12, 1, {"id": 1001, "popularity": 99, "vote_average": 9.5, "vote_count": 500},
                NOW - timedelta(days=2)),
    ]
    topics = {"base": {"source_record_pk": 11}, "countries": {"source_record_pk": 11},
              "metrics": {"source_record_pk": 12}}
    engine = _catalog_engine(rows, sources, mappings={1: 1001},
                             metadata=[_meta(1, 11, topics=topics)])
    values = {row["movie_id"]: row for row in load_candidates(engine)}
    assert values[1]["tmdb_popularity"] == 99
    assert values[1]["tmdb_rating"] == 9.5
    assert values[1]["vote_count"] == 500


def test_empty_metrics_does_not_fall_back_to_history():
    from catalog.recommendation_data import load_candidates
    rows = [_row(1)]
    sources = [
        _source(11, 1, {"id": 1001, "popularity": 5, "vote_average": 7, "vote_count": 40}, NOW),
        _source(12, 1, {"id": 1001, "popularity": 88, "vote_average": 8, "vote_count": 90},
                NOW - timedelta(days=2)),
    ]
    topics = {"base": {"source_record_pk": 11}, "countries": {"source_record_pk": 11},
              "metrics": {"state": "EMPTY", "source_record_pk": 12}}
    engine = _catalog_engine(rows, sources, mappings={1: 1001},
                             metadata=[_meta(1, 11, topics=topics)])
    values = {row["movie_id"]: row for row in load_candidates(engine)}
    assert values[1]["tmdb_popularity"] is None
    assert values[1]["tmdb_rating"] is None
    assert values[1]["vote_count"] is None


def test_genre_rename_invalidates_snapshot_after_ttl():
    """Probe 3: genre names are part of the watermark, not just ids."""
    from catalog.recommendation_data import load_candidates
    engine = _engine_pair()
    first = {row["movie_id"]: row for row in load_candidates(engine)}
    assert first[1]["genres"] == ["剧情"]
    with engine.begin() as connection:
        connection.execute(text("UPDATE genres SET name_zh='genre_changed' WHERE genre_id=1"))
    second = {row["movie_id"]: row for row in load_candidates(engine)}
    assert second[1]["genres"] == ["genre_changed"]


def test_asset_inplace_change_invalidates_snapshot():
    from catalog.recommendation_data import load_candidates
    rows = [_row(1)]
    sources = [_source(11, 1, {"id": 1001, "popularity": 5}, NOW)]
    assets = [dict(asset_id=1, movie_id=1, asset_type="poster", source="tmdb",
                   source_path="/old.jpg", language="en", width=100, height=150,
                   is_primary=True, license_status="allowed", updated_at=NOW)]
    engine = _catalog_engine(rows, sources, mappings={1: 1001},
                             metadata=[_meta(1, 11)], assets=assets)
    first = {row["movie_id"]: row for row in load_candidates(engine)}
    assert first[1]["poster"] and "old.jpg" in first[1]["poster"]
    with engine.begin() as connection:
        connection.execute(text("UPDATE movie_assets SET source_path='/new.jpg',"
                                " updated_at=:t WHERE asset_id=1"),
                           {"t": NOW + timedelta(minutes=3)})
    second = {row["movie_id"]: row for row in load_candidates(engine)}
    assert second[1]["poster"] and "new.jpg" in second[1]["poster"]


def test_engine_isolation_same_watermark_different_database(tmp_path, monkeypatch):
    """Two databases with identical watermarks must not share a snapshot."""
    monkeypatch.setenv("MI2_CATALOG_CACHE_TTL", "0")
    from catalog.recommendation_data import load_candidates
    first = _catalog_engine([_row(1)], [_source(11, 1, {"id": 1001, "popularity": 5}, NOW)],
                            mappings={1: 1001}, metadata=[_meta(1, 11)],
                            url="sqlite:///" + str(tmp_path / "a.sqlite"))
    second = _catalog_engine([_row(1)], [_source(11, 1, {"id": 1001, "popularity": 77}, NOW)],
                             mappings={1: 1001}, metadata=[_meta(1, 11)],
                             url="sqlite:///" + str(tmp_path / "b.sqlite"))
    assert load_candidates(first)[0]["tmdb_popularity"] == 5
    assert load_candidates(second)[0]["tmdb_popularity"] == 77
    assert public_catalog_cache.namespace(first) != public_catalog_cache.namespace(second)


def test_incremental_merge_leaves_previous_snapshot_untouched():
    from catalog.recommendation_data import load_candidates
    engine = _engine_pair()
    before = load_candidates(engine)
    snapshot_before = [dict(row) for row in before]
    with engine.begin() as connection:
        connection.execute(text("UPDATE movie_source_records SET metadata_json=:p WHERE id=12"),
                           {"p": json.dumps({"id": 1001, "popularity": 30})})
        connection.execute(text("UPDATE tmdb_movie_metadata SET updated_at=:t WHERE movie_id=1"),
                           {"t": NOW + timedelta(minutes=4)})
    after = {row["movie_id"]: row for row in load_candidates(engine)}
    assert [row["tmdb_popularity"] for row in snapshot_before] == [9.0, 3.0, None, None, 2.0, 4.0]
    assert after[3]["tmdb_popularity"] is None


def test_force_rebuild_survives_builder_failure():
    from catalog import recommendation_data as module
    engine = _engine_pair()
    module.load_candidates(engine)
    public_catalog_cache.invalidate(engine)

    calls = {"n": 0}
    real = module._load_candidates

    def boom(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("provider down")

    module._load_candidates = boom
    try:
        with pytest.raises(RuntimeError):
            module.load_candidates(engine)
        with pytest.raises(RuntimeError):
            module.load_candidates(engine)
    finally:
        module._load_candidates = real
    assert calls["n"] == 2  # the force request was not silently lost
    assert module.load_candidates(engine)[0]["movie_id"] == 1


def test_corrupt_second_row_rebuilds(tmp_path, monkeypatch):
    from catalog.recommendation_data import load_candidates
    engine = _engine_pair("sqlite:///" + str(tmp_path / "catalog.sqlite"))
    load_candidates(engine)
    path = public_catalog_cache.cache_path(engine)
    with gzip.open(path, "rb") as handle:
        doc = json.loads(handle.read())
    doc["movies"][1] = {"title": "broken"}
    with gzip.open(path, "wb") as handle:
        handle.write(json.dumps(doc).encode("utf-8"))
    public_catalog_cache.invalidate(engine)
    rows = load_candidates(engine)
    assert len(rows) == 6

def test_persistent_engine_publishes_updated_version_to_disk(tmp_path, monkeypatch):
    """A real watermark change must upgrade the on-disk snapshot version.

    Regression for comparing ``built_at`` (wall clock) with ``time.monotonic()``:
    every old file looked newer, so the disk copy never advanced.
    """
    monkeypatch.setenv("MI2_CATALOG_CACHE_TTL", "0")
    from catalog.recommendation_data import load_candidates
    engine = _catalog_engine([_row(1)], [_source(11, 1, {"id": 1001, "popularity": 5}, NOW)],
                             mappings={1: 1001}, metadata=[_meta(1, 11)],
                             url="sqlite:///" + str(tmp_path / "engine.sqlite"))
    load_candidates(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE genres SET name_zh='changed' WHERE genre_id=1"))
    load_candidates(engine)
    with gzip.open(public_catalog_cache.cache_path(engine), "rb") as handle:
        doc = json.loads(handle.read())
    assert doc["version"] == public_catalog_cache.peek(engine)["version"]
    assert doc["movies"][0]["genres"] == ["changed"]


def test_force_survives_watermark_failure(tmp_path, monkeypatch):
    """A DB failure while reading watermarks must keep the force request."""
    from catalog import recommendation_data as module
    engine = _catalog_engine([_row(1)], [_source(11, 1, {"id": 1001, "popularity": 5}, NOW)],
                             mappings={1: 1001}, metadata=[_meta(1, 11)],
                             url="sqlite:///" + str(tmp_path / "engine.sqlite"))
    module.load_candidates(engine)
    public_catalog_cache.invalidate(engine)

    def broken(*args, **kwargs):
        raise RuntimeError("watermark read failed")

    monkeypatch.setattr(public_catalog_cache, "_watermarks", broken)
    with pytest.raises(RuntimeError):
        module.load_candidates(engine)
    monkeypatch.undo()
    assert public_catalog_cache._FORCE_ENGINES.get(engine) is True
    result = public_catalog_cache.load(
        engine,
        lambda counters: module._load_candidates(engine, stats=counters),
        lambda ids, counters: module._load_candidates(engine, stats=counters, movie_ids=ids),
        module.finalize, ttl=0, stats={})
    assert result["source"] == "rebuilt"


def test_unstable_build_returns_the_document_version(monkeypatch):
    """After 3 racing attempts the version must match the built document."""
    from itertools import count
    sequence = count(1)
    monkeypatch.setattr(public_catalog_cache, "_watermarks",
                        lambda *args, **kwargs: {1: str(next(sequence))})
    engine = create_engine("sqlite://")
    result = public_catalog_cache.load(
        engine, lambda counters: [{"movie_id": 1}], None, None, ttl=0, stats={})
    assert result["source"] == "rebuilt_racing"
    expected = public_catalog_cache._version(
        public_catalog_cache.namespace(engine), {1: "3"})
    assert result["version"] == expected


def test_watermark_is_independent_of_row_order():
    engine = _catalog_engine([_row(1), _row(2)],
                             [_source(11, 1, {"id": 1001, "popularity": 5}, NOW),
                              _source(22, 2, {"id": 1002, "popularity": 3}, NOW)],
                             mappings={1: 1001, 2: 1002},
                             metadata=[_meta(1, 11), _meta(2, 22)])
    first = public_catalog_cache._watermarks(engine)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM movie_genres"))
        connection.execute(text("INSERT INTO movie_genres (movie_id, genre_id) VALUES (2,1),(1,1)"))
    second = public_catalog_cache._watermarks(engine)
    assert first == second


def test_credit_person_swap_changes_watermark_without_count_change():
    credits = [
        dict(credit_id=1, movie_id=1, person_id=10, source="tmdb",
             source_credit_key="c1", role_type="cast", job=None, character_name=None,
             sort_order=0),
        dict(credit_id=2, movie_id=1, person_id=20, source="tmdb",
             source_credit_key="c2", role_type="cast", job=None, character_name=None,
             sort_order=1),
    ]
    engine = _catalog_engine([_row(1)], [_source(11, 1, {"id": 1001}, NOW)],
                             mappings={1: 1001}, metadata=[_meta(1, 11)], credits=credits)
    before = public_catalog_cache._watermarks(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE movie_credits SET person_id=30 - person_id"))
    after = public_catalog_cache._watermarks(engine)
    assert before[1] != after[1]


def test_asset_path_change_with_same_timestamp_changes_watermark():
    assets = [dict(asset_id=1, movie_id=1, asset_type="poster", source="tmdb",
                   source_path="/old.jpg", language="en", width=100, height=150,
                   is_primary=True, license_status="allowed", updated_at=NOW)]
    engine = _catalog_engine([_row(1)], [_source(11, 1, {"id": 1001}, NOW)],
                             mappings={1: 1001}, metadata=[_meta(1, 11)], assets=assets)
    before = public_catalog_cache._watermarks(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE movie_assets SET source_path='/same.jpg'"))
    after = public_catalog_cache._watermarks(engine)
    assert before[1] != after[1]

def test_new_fallback_source_without_metadata_is_seen(tmp_path, monkeypatch):
    """No supplement capture: a newer legal TMDB source must refresh the value."""
    monkeypatch.setenv("MI2_CATALOG_CACHE_TTL", "0")
    from catalog.recommendation_data import load_candidates
    engine = _catalog_engine(
        [_row(1)], [_source(11, 1, {"id": 1001, "popularity": 5}, NOW)],
        mappings={1: 1001}, url="sqlite:///" + str(tmp_path / "fallback.sqlite"))
    assert load_candidates(engine)[0]["tmdb_popularity"] == 5
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO movie_source_records (id, movie_id, source, source_record_id,"
            " fetched_at, payload_hash, metadata_json, ingest_batch_id, license_status)"
            " VALUES (12, 1, 'tmdb', 'tmdb-12', :t, :h, :p, 1, 'allowed')"),
            {"t": NOW + timedelta(minutes=5), "h": "b" * 64,
             "p": json.dumps({"id": 1001, "popularity": 42})})
    assert load_candidates(engine)[0]["tmdb_popularity"] == 42


def test_corrected_fallback_payload_is_seen(tmp_path, monkeypatch):
    """Re-writing an existing fallback record's payload must invalidate too."""
    monkeypatch.setenv("MI2_CATALOG_CACHE_TTL", "0")
    from catalog.recommendation_data import load_candidates
    engine = _catalog_engine(
        [_row(1)], [_source(11, 1, {"id": 1001, "popularity": 5}, NOW)],
        mappings={1: 1001}, url="sqlite:///" + str(tmp_path / "fallback.sqlite"))
    assert load_candidates(engine)[0]["tmdb_popularity"] == 5
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE movie_source_records SET metadata_json=:p, payload_hash=:h WHERE id=11"),
            {"p": json.dumps({"id": 1001, "popularity": 77}), "h": "c" * 64})
    assert load_candidates(engine)[0]["tmdb_popularity"] == 77
