"""Targeted offline regression tests for WP-OPT-BE1 fixes and Wave 2 caching."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import threading
import time
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest import mock
import uuid

from flask import Flask
import pytest
from sqlalchemy import Column, DateTime, Integer, JSON, String, Table

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from analytics.admin_reporting import import_audit  # noqa: E402
from app import auth as auth_module  # noqa: E402
from catalog import public_catalog_cache  # noqa: E402
from catalog import tmdb_supplement_api as api_module  # noqa: E402
from catalog.import_jobs import ImportJobs  # noqa: E402
from catalog.tmdb_supplement import analytics as analytics_module  # noqa: E402
from catalog.tmdb_supplement import repository  # noqa: E402
from test_admin_dashboard import _fixture_engine, _rows  # noqa: E402
from test_admin_imports import Adapter  # noqa: E402
from test_admin_v2_resource_api import (  # noqa: E402
    LATER,
    STAMP,
    build_app as build_v2_app,
    fixture_tables,
    metadata_row,
    signed_in,
)
from test_current_state_cache import _engine_pair  # noqa: E402
from test_tmdb_supplement_analytics import make_document  # noqa: E402


# --------------------------------------------------------------- [P0-1] auth


class _FakeBeginSession:
    def __init__(self, tracker, user):
        self._tracker = tracker
        self._user = user
        self.throttle_counts = {}

    def execute(self, stmt):
        key = str(stmt)
        self.throttle_counts[key] = self.throttle_counts.get(key, 0) + 1

    def scalar(self, stmt):
        sql = str(stmt)
        if 'FROM auth_throttle' in sql:
            return 1
        if 'FROM auth_sessions' in sql:
            return SimpleNamespace(
                id=1,
                user_id=None,
                csrf_token_hash=auth_module.hashed('good-csrf'),
                revoked_at=None,
                idle_expires_at=datetime.utcnow() + timedelta(hours=1),
                absolute_expires_at=datetime.utcnow() + timedelta(hours=2),
                last_seen_at=datetime.utcnow(),
            )
        if 'FROM auth_users' in sql:
            return self._user
        return None

    def get(self, model, pk):
        return self._user

    def add(self, obj):
        if getattr(obj, 'id', None) is None:
            obj.id = 99

    def flush(self):
        pass

    @contextmanager
    def begin_nested(self):
        yield self


class _BoundedAuthDb:
    """Bounded pool tracker: deadlocks if any request borrows 2 connections simultaneously."""

    def __init__(self, pool_size, user):
        self._sem = threading.BoundedSemaphore(pool_size)
        self._lock = threading.Lock()
        self._depth = {}
        self.max_depth_per_thread = 0
        self.total_begins = 0
        self._user = user

    @contextmanager
    def begin(self):
        tid = threading.get_ident()
        acquired = self._sem.acquire(timeout=1.5)
        if not acquired:
            raise TimeoutError('QueuePool limit overflow: nested connection deadlock')
        try:
            with self._lock:
                depth = self._depth.get(tid, 0) + 1
                self._depth[tid] = depth
                self.max_depth_per_thread = max(self.max_depth_per_thread, depth)
                self.total_begins += 1
            yield _FakeBeginSession(self, self._user)
        finally:
            with self._lock:
                self._depth[tid] -= 1
            self._sem.release()


def test_p0_1_auth_credentials_never_borrows_nested_session_under_concurrency():
    app = Flask('test_auth_no_nested_pool')
    app.config.update({
        'RATE_WINDOW': 60,
        'RATE_LIMIT': 1000,
        'CSRF_RATE_LIMIT': 1000,
        'IDLE_SECONDS': 3600,
        'ABSOLUTE_SECONDS': 7200,
        'COOKIE_SECURE': False,
        'ALLOWED_ORIGINS': {'http://127.0.0.1:5174'},
        'ADMIN_USERS': ['codex'],
    })
    user = SimpleNamespace(
        id=7,
        username='codex',
        username_normalized='codex',
        password_hash=auth_module.ph.hash('secret123'),
        status='active',
    )
    # Pool size = 5 with 20 concurrent threads: if credentials() borrows 2
    # connections at once, threads holding conn#1 while waiting for conn#2 starve.
    db_pool = _BoundedAuthDb(pool_size=5, user=user)
    app.extensions['auth_db'] = db_pool
    app.register_blueprint(auth_module.bp, url_prefix='/api/v1/auth')

    barrier = threading.Barrier(20)

    def attempt(idx):
        barrier.wait(timeout=3.0)
        client = app.test_client()
        client.set_cookie('mi2_sid', 's' * 40, path='/api/v1')
        resp = client.post(
            '/api/v1/auth/login',
            json={'username': 'codex', 'password': 'secret123'},
            headers={
                'Origin': 'http://127.0.0.1:5174',
                'X-CSRF-Token': 'good-csrf',
            },
        )
        return resp.status_code

    with ThreadPoolExecutor(max_workers=20) as pool:
        codes = list(pool.map(attempt, range(20)))

    assert codes == [200] * 20
    assert db_pool.max_depth_per_thread == 1
    assert db_pool.total_begins == 20


# --------------------------------------------------- [P0-2] interleaved paging


def test_p0_2_import_audit_interleaved_timestamps_across_pages(tmp_path):
    e, md = _fixture_engine()
    Table(
        'ingest_batches', md,
        Column('batch_id', Integer), Column('source', String),
        Column('mode', String), Column('status', String),
        Column('started_at', DateTime), Column('finished_at', DateTime),
        Column('source_snapshot_ref', String), Column('counts_json', JSON),
    )
    md.create_all(e)
    _rows(e, md, 'auth_users', [dict(id=1, status='active', created_at=datetime(2026, 1, 1))])
    with e.begin() as c:
        c.exec_driver_sql('ALTER TABLE auth_users ADD COLUMN username TEXT')
        c.exec_driver_sql("UPDATE auth_users SET username='codex'")
    jobs = ImportJobs(tmp_path, Adapter(), lambda _: True)
    base = datetime(2026, 1, 2)

    # Interleave 5 file jobs (even minutes 0,2,4,6,8) among 25 DB batches (all minutes 0..24
    # or shifted by +10s so timestamps weave strictly between DB rows across page 1/2/3).
    for n in range(5):
        key = str(uuid.uuid4())
        when = base + timedelta(minutes=n * 4, seconds=30)
        doc = dict(
            job_id=key, owner=1, status='SUCCEEDED', kind='tmdb_catalog',
            created_at=when.isoformat() + 'Z', updated_at=when.isoformat() + 'Z',
            plans=[dict(title='Film ' + str(n), movie_id=n + 1, tmdb_id=n + 1)],
        )
        jobs.write(jobs.path(key), doc)

    batches = [
        dict(
            batch_id=n + 1, source='tmdb', mode='write', status='succeeded',
            started_at=base + timedelta(minutes=n),
            finished_at=base + timedelta(minutes=n),
            source_snapshot_ref='legacy:' + str(n),
            counts_json={'movies': 1},
        )
        for n in range(25)
    ]
    _rows(e, md, 'ingest_batches', batches)

    app = SimpleNamespace(extensions={'auth_engine': e, 'admin_import_jobs': jobs})
    start = base - timedelta(hours=1)
    end = base + timedelta(days=1)

    pages = [import_audit(app, start, end, p, 10, 'ALL') for p in (1, 2, 3)]
    all_items = [item for page in pages for item in page['items']]

    assert [len(page['items']) for page in pages] == [10, 10, 10]
    assert all(page['total'] == 30 for page in pages)
    assert len({item['id'] for item in all_items}) == 30
    assert [(item['at'], item['id']) for item in all_items] == sorted(
        [(item['at'], item['id']) for item in all_items], reverse=True
    )


# ----------------------------------------------- [P1-1] public_catalog_cache


def test_p1_1_concurrent_cold_miss_rebuilds_only_once(tmp_path, monkeypatch):
    monkeypatch.setenv('MI2_CATALOG_CACHE_DIR', str(tmp_path / 'cache'))
    monkeypatch.setenv('MI2_CATALOG_CACHE_TTL', '15')
    public_catalog_cache.invalidate()
    engine = _engine_pair(url='sqlite:///' + str(tmp_path / 'catalog.sqlite').replace('\\', '/'))

    builds = []
    barrier = threading.Barrier(12)

    def build_all(counters):
        builds.append(1)
        time.sleep(0.03)
        return [{'movie_id': i, 'production_countries': [{'iso2': 'US'}]} for i in range(1, 7)]

    results = []

    def worker():
        barrier.wait(timeout=3.0)
        res = public_catalog_cache.load(engine, build_all, ttl=15)
        results.append(res)

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda _: worker(), range(12)))

    assert len(builds) == 1
    assert sum(1 for r in results if r['source'] == 'rebuilt') == 1
    assert sum(1 for r in results if r['source'] == 'memory_hit') == 11

    # Verify clone isolation and readonly copy_mode
    r1 = public_catalog_cache.load(engine, build_all, ttl=15)
    r1['movies'][0]['production_countries'][0]['iso2'] = 'ZZ'
    r2 = public_catalog_cache.load(engine, build_all, ttl=15)
    assert r2['movies'][0]['production_countries'][0]['iso2'] == 'US'

    ro = public_catalog_cache.load(engine, build_all, ttl=15, copy_mode='readonly')
    assert isinstance(ro['movies'], tuple)
    assert isinstance(ro['movies'][0], MappingProxyType)
    public_catalog_cache.invalidate()


# ------------------------------ [P1-2/3 + P2-1/2] tmdb_supplement consistency


def test_p1_2_repository_queries_filter_active_catalog_status():
    for sql in (
        repository.DOCUMENTS_SQL,
        repository.COVERAGE_SQL,
        repository.MISSING_SQL,
        repository.WATERMARK_SQL,
    ):
        assert 'JOIN movies AS mv ON mv.movie_id = m.movie_id' in sql
    for sql in (
        repository.COVERAGE_SQL,
        repository.MISSING_SQL,
        repository.WATERMARK_SQL,
    ):
        assert "catalog_status = 'active'" in sql

    tables = fixture_tables()
    tables['tmdb_movie_metadata'].append(metadata_row(91))  # movie 91 is 'hidden'
    app = build_v2_app(tables)
    with signed_in(True):
        resp = app.test_client().get('/api/admin/tmdb-supplement/analytics')
    assert resp.status_code == 200
    data = resp.get_json()['data']
    assert data['scope']['movies_matched'] == 3
    assert 91 not in {item['movie_id'] for item in data['details']['items']}

    from test_admin_v2_resource_api import FakeConnection
    conn = FakeConnection(tables)
    repository.load_documents(conn, movie_ids=[1, 91])
    assert "WHERE mv.catalog_status = 'active' AND m.movie_id IN " in conn.statements[0][0]


def test_p1_3_credit_block_dedups_per_movie_and_keeps_distinct_homonyms():
    doc = make_document(1, credits=[
        {'role_type': 'cast', 'person_id': 101, 'person_name': 'Same Name', 'character': 'Role A', 'sort_order': 0},
        {'role_type': 'cast', 'person_id': 101, 'person_name': 'Same Name', 'character': 'Role B', 'sort_order': 1},
        {'role_type': 'cast', 'person_id': 202, 'person_name': 'Same Name', 'character': 'Other', 'sort_order': 2},
        {'role_type': 'crew', 'person_id': 101, 'person_name': 'Same Name', 'job': 'Director', 'sort_order': 0},
        {'role_type': 'crew', 'person_id': 101, 'person_name': 'Same Name', 'job': 'Director', 'sort_order': 1},
    ])
    res = analytics_module.aggregate_movies([doc], filters={'top': 10})
    credits_block = res['global']['credits']
    assert credits_block['directors'] == [
        {'person_id': 101, 'name': 'Same Name', 'movie_count': 1, 'top_movies': ['Movie 1']}
    ]
    assert credits_block['cast'] == [
        {'person_id': 101, 'name': 'Same Name', 'movie_count': 1, 'top_movies': ['Movie 1']},
        {'person_id': 202, 'name': 'Same Name', 'movie_count': 1, 'top_movies': ['Movie 1']},
    ]


def test_p2_1_and_p2_2_quality_duplicate_issue_not_doubled_and_note_aligned():
    d1 = make_document(1, '2026-10-01T00:00:00Z')
    d2 = make_document(1, '2026-11-01T00:00:00Z')
    res = analytics_module.aggregate_quality([d1, d2], catalog={'active_movies': 1, 'tmdb_mapped': 1, 'unmapped': 0})
    issues = {row['code']: row['count'] for row in res['issues']}
    assert issues['DUPLICATE_MOVIE_DOCUMENTS'] == 1
    assert '海报/简介' in res['notes'][2]


# ------------------------------- [Wave 2] detail_country + two-tier caching


def test_wave2_detail_country_preserves_full_denominators_and_validates():
    app = build_v2_app(fixture_tables())
    client = app.test_client()
    with signed_in(True):
        ok = client.get('/api/admin/tmdb-supplement/analytics?detail_country=JP&countries=US,JP')
        bad_iso = client.get('/api/admin/tmdb-supplement/analytics?detail_country=JPN')
        bad_many = client.get('/api/admin/tmdb-supplement/analytics?detail_country=US,CN,JP,KR')

    assert ok.status_code == 200
    data = ok.get_json()['data']
    # Denominators remain the full 3 matched active movies, while details only shows JP (movie 3).
    assert data['scope']['movies_matched'] == 3
    assert data['global']['movie_count_distinct'] == 3
    assert data['countries']['denominator']['movies'] == 3
    assert data['country_analysis']['denominator'] == 3
    assert data['details']['total_before_detail_filters'] == 3
    assert data['details']['total'] == 1
    assert [item['movie_id'] for item in data['details']['items']] == [3]
    assert data['details']['applied_filters']['detail_country'] == ['JP']
    assert 'detail_country' in data['filter_semantics']

    assert bad_iso.status_code == 400
    assert bad_many.status_code == 400


def test_wave2_two_tier_cache_prepares_once_and_evicts_lru_on_32_plus():
    tables = fixture_tables()
    app = build_v2_app(tables)
    client = app.test_client()

    prepare_calls = 0
    real_prepare = analytics_module.prepare_documents

    def counting_prepare(documents=None):
        nonlocal prepare_calls
        prepare_calls += 1
        return real_prepare(documents)

    with mock.patch.object(api_module, 'prepare_documents', side_effect=counting_prepare):
        with signed_in(True):
            # 3 distinct filter combinations under the same watermark -> 1 prepare_documents call
            r1 = client.get('/api/admin/tmdb-supplement/analytics?detail_country=US')
            r2 = client.get('/api/admin/tmdb-supplement/analytics?detail_country=JP')
            r3 = client.get('/api/admin/tmdb-supplement/quality?denominator=active')
            assert prepare_calls == 1

            # Repeat r1 & r3 -> Tier-2 LRU hit without invoking aggregate_movies / aggregate_quality
            with mock.patch.object(api_module, 'aggregate_movies', side_effect=AssertionError('should hit LRU')), \
                    mock.patch.object(api_module, 'aggregate_quality', side_effect=AssertionError('should hit LRU')):
                r1_hit = client.get('/api/admin/tmdb-supplement/analytics?detail_country=US')
                r3_hit = client.get('/api/admin/tmdb-supplement/quality?denominator=active')

            assert r1_hit.status_code == 200 and r1_hit.get_json()['data']['scope']['cache']['hit'] is True
            assert r3_hit.status_code == 200 and r3_hit.get_json()['data']['cache']['hit'] is True

            # Mutating returned data in-memory must not pollute LRU entry
            base_entry = api_module._DOCUMENT_CACHE._lru[next(iter(api_module._DOCUMENT_CACHE._lru))]
            assert 'cache' not in base_entry['scope']
            assert 'catalog' not in base_entry['scope']

            # Exceed LRU maxsize=32 -> bounded at 32
            for votes in range(1, 40):
                resp = client.get(f'/api/admin/tmdb-supplement/analytics?min_votes={votes}')
                assert resp.status_code == 200
            assert len(api_module._DOCUMENT_CACHE._lru) == 32

            # Watermark bump clears old generation LRU and triggers a single new prepare
            tables['tmdb_movie_metadata'][0]['updated_at'] = LATER
            r_new = client.get('/api/admin/tmdb-supplement/analytics?detail_country=US')
            assert r_new.status_code == 200
            assert r_new.get_json()['data']['scope']['cache']['hit'] is False
            assert prepare_calls == 2


# ----------------------------------------------- [P2-6] poster_fallback


def test_p2_6_poster_fallback_selection_and_board_exposure(tmp_path, monkeypatch):
    from datetime import timezone
    from catalog import recommendation_boards, recommendation_data
    from test_current_state_cache import NOW, _catalog_engine, _meta, _row, _source

    monkeypatch.setenv('MI2_CATALOG_CACHE_DIR', str(tmp_path / 'cache'))
    monkeypatch.setenv('MI2_CATALOG_CACHE_TTL', '0')
    public_catalog_cache.invalidate()

    rows = [_row(1), _row(2)]
    sources = [
        _source(11, 1, {'id': 1001, 'popularity': 10, 'vote_average': 8.2, 'vote_count': 500}, NOW),
        _source(21, 2, {'id': 1002, 'popularity': 8, 'vote_average': 7.5, 'vote_count': 300}, NOW),
    ]
    assets = [
        dict(asset_id=10, movie_id=1, source='tmdb', asset_type='poster', source_path='/primary1.jpg', is_primary=True),
        dict(asset_id=11, movie_id=1, source='tmdb', asset_type='poster', source_path='/fallback1.jpg', is_primary=False),
        dict(asset_id=12, movie_id=1, source='tmdb', asset_type='poster', source_path='/third1.jpg', is_primary=False),
        dict(asset_id=20, movie_id=2, source='tmdb', asset_type='poster', source_path='/same2.jpg', is_primary=True),
        dict(asset_id=21, movie_id=2, source='tmdb', asset_type='poster', source_path='/same2.jpg', is_primary=False),
    ]
    engine = _catalog_engine(
        rows,
        sources,
        mappings={1: 1001, 2: 1002},
        metadata=[_meta(1, 11), _meta(2, 21)],
        assets=assets,
    )
    try:
        candidates = {m['movie_id']: m for m in recommendation_data.load_candidates(engine)}
        assert candidates[1]['poster'].endswith('%2Fprimary1.jpg')
        assert candidates[1]['poster_fallback'].endswith('%2Ffallback1.jpg')
        assert candidates[2]['poster'].endswith('%2Fsame2.jpg')
        assert candidates[2]['poster_fallback'] is None

        board_res = recommendation_boards.board(
            list(candidates.values()), {}, 'codex', 'daily',
            now=datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc),
            cache_dir=tmp_path / 'rec',
        )
        by_id = {item['movie_id']: item for item in board_res['items']}
        assert by_id[1]['poster'] == candidates[1]['poster']
        assert by_id[1]['poster_fallback'] == candidates[1]['poster_fallback']
        assert by_id[2]['poster_fallback'] is None
    finally:
        public_catalog_cache.invalidate()
        engine.dispose()

