"""Regression tests for cold-cache stampede, startup warmup, collation, dashboard & globe-summary."""
import json
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask
from sqlalchemy import create_engine, event, text
from sqlalchemy.dialects.mysql import dialect as mysql_dialect
from sqlalchemy.exc import OperationalError, SAWarning

import admin_dashboard as dashboard
from catalog import admin_import_api, current_state, data_quality, m6_business
from catalog import public_catalog_cache, recommendation_boards as boards, recommendation_data
from m6_checks.test_current_state_cache import NOW, _catalog_engine, _meta, _row, _source


class _BehaviorStub:
    def __init__(self, users=None):
        self.users = dict(users or {})

    def snapshot(self, user):
        return {mid: dict(row) for mid, row in self.users.get(user, {}).items()}


def _board_app(tmp_path, *, tmdb_service=None, users=None, count=60):
    rows = [_row(mid, title=f'Film {mid}', release_year=2000 + (mid % 25)) for mid in range(1, count + 1)]
    sources = [_source(mid, mid, {'id': 1000 + mid, 'popularity': float(mid), 'vote_average': 7.5 + (mid % 20) / 10,
                                   'vote_count': 120 + mid, 'revenue': mid * 1000}, NOW)
               for mid in range(1, count + 1)]
    meta = [_meta(mid, mid) for mid in range(1, count + 1)]
    engine = _catalog_engine(rows, sources, mappings={mid: 1000 + mid for mid in range(1, count + 1)}, metadata=meta)
    app = Flask(__name__)
    app.config.update(TESTING=True, MEDIA_CACHE_DIR=str(tmp_path))
    app.extensions.update(auth_engine=engine, m6_behavior=_BehaviorStub(users))
    if tmdb_service is not None:
        app.extensions['tmdb_connection'] = tmdb_service
    return app, engine


def test_cold_cache_single_flight_and_zero_sql_archive_literal(tmp_path, monkeypatch):
    monkeypatch.setenv('MI2_TMDB_BOARD_DIR', str(tmp_path))
    monkeypatch.delenv('MI2_RECOMMENDATION_CACHE_DIR', raising=False)
    app, engine = _board_app(tmp_path, users={7: {'1': {'favorite': True}}}, count=550)
    now = NOW.replace(tzinfo=timezone.utc)
    (tmp_path / 'popular.json').write_text(
        json.dumps({'schema': 1, 'channel': 'popular', 'captured_at': now.isoformat(),
                    'items': [{'id': 1001, 'title': 'Film 1', 'source_rank': 1}]}),
        encoding='utf-8',
    )
    sql_calls = []
    scalar_calls = []
    real_scalar = boards.sql_scalar_projection

    def count_scalar(*args, **kwargs):
        scalar_calls.append(1)
        return real_scalar(*args, **kwargs)

    @event.listens_for(engine, 'before_cursor_execute')
    def observe(_conn, _cursor, stmt, params, _ctx, _many):
        sql_calls.append((stmt, params))

    monkeypatch.setattr(boards, 'sql_scalar_projection', count_scalar)
    monkeypatch.setattr(boards, 'sql_feature_projection', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('full projection called')))

    channels = ['popular', 'top_rated', 'revenue', 'daily', 'weekly', 'personal', 'because']
    barrier = threading.Barrier(len(channels))

    def fetch(ch):
        barrier.wait(timeout=5)
        return boards.for_app(app, 7, ch, now=now, limit=5)

    try:
        with ThreadPoolExecutor(max_workers=len(channels)) as pool:
            results = list(pool.map(fetch, channels))
        assert len(results) == len(channels)
        assert len(scalar_calls) == 1
        assert all(not isinstance(v, str) or len(v) < 5000 for _, p in sql_calls for v in (p.values() if isinstance(p, dict) else p))
        for mv in range(1, 70):
            boards.for_app(app, 7, 'top_rated', now=now, min_votes=mv, limit=1)
        assert len(boards._engine_cache(engine).results) <= boards.MAX_BOARD_CACHE == 64
    finally:
        engine.dispose()


def test_warmed_public_catalog_cache_avoids_scalar_sql_and_handles_fault_invalidation(tmp_path, monkeypatch):
    monkeypatch.setenv('MI2_CATALOG_CACHE_DIR', str(tmp_path / 'cat'))
    monkeypatch.setenv('MI2_CATALOG_CACHE_TTL', '120')
    app, engine = _board_app(tmp_path, users={7: {'1': {'favorite': True}}})
    now = NOW.replace(tzinfo=timezone.utc)
    try:
        recommendation_data.load_candidates_readonly(engine)
        assert public_catalog_cache.peek(engine) is not None
        monkeypatch.setattr(boards, 'sql_scalar_projection', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('should use warmed cache')))
        res = boards.for_app(app, 7, 'top_rated', now=now, limit=5)
        assert res['catalog_total'] == 60
        assert res['candidate_count'] == 60
        assert len(res['items']) == 5
        res_daily = boards.for_app(app, 7, 'daily', now=now, limit=5)
        assert res_daily['catalog_total'] == 60 and len(res_daily['items']) == 5

        # Simulate public_catalog_cache.invalidate failing while invalidate_board_cache succeeds:
        # boards must fall back safely rather than trusting the stale un-invalidated snapshot.
        monkeypatch.undo()
        with engine.begin() as conn:
            conn.execute(text("UPDATE movies SET catalog_status='inactive' WHERE movie_id=60"))
        boards.invalidate_board_cache(engine)
        res2 = boards.for_app(app, 7, 'revenue', now=now, limit=5)
        assert res2['catalog_total'] == 59
        assert res2['items'][0]['movie_id'] == 59
    finally:
        public_catalog_cache.invalidate(engine)
        engine.dispose()


def test_non_personalized_boards_share_cache_across_users_and_attach_per_user_tmdb(tmp_path):
    assert boards.MAX_BOARD_CACHE == 64

    class TmdbService:
        def __init__(self):
            self._lock = threading.Lock()
            self._entries = {(1, 'bind-a'): {'generation': 'g1', 'enabled': True, 'verification': 'VERIFIED',
                                             'last_error': None, 'last_checked_at': None, 'expires': 1e12}}

        def status(self, user, binding):
            if user == 1 and binding == 'bind-a':
                return {'configured': True, 'enabled': True, 'verification': 'VERIFIED', 'last_error': None}
            return {'configured': True, 'enabled': True, 'verification': 'UNVERIFIED', 'last_error': 'RATE_LIMITED'}

        def snapshot(self, channel, user=None, binding=None):
            return {'schema': 1, 'channel': 'popular', 'captured_at': NOW.replace(tzinfo=timezone.utc).isoformat(),
                    'items': [{'id': 1001, 'title': 'Film 1', 'source_rank': 1},
                              {'id': 1002, 'title': 'Film 2', 'source_rank': 2}]}

    service = TmdbService()
    app, engine = _board_app(
        tmp_path,
        tmdb_service=service,
        users={
            1: {'1': {'favorite': True}, '2': {'watched': True}},
            2: {'1': {'want': True}},
            3: {'1': {'not_interested': True}},
        },
    )
    now = NOW.replace(tzinfo=timezone.utc)
    sql_calls = []

    @event.listens_for(engine, 'before_cursor_execute')
    def observe(_conn, _cursor, stmt, params, _ctx, _many):
        sql_calls.append(stmt)

    try:
        for channel in ('popular', 'top_rated', 'revenue'):
            sql_calls.clear()
            r1 = boards._for_app(app, 1, channel, binding='bind-a', now=now, limit=5)
            assert r1['tmdb_connection']['last_error'] is None
            assert sql_calls
            sql_calls.clear()
            r2 = boards._for_app(app, 2, channel, binding='bind-b', now=now, limit=5)
            assert not sql_calls
            assert r2['tmdb_connection']['last_error'] == 'RATE_LIMITED'
            assert any('TMDB在线读取失败' in msg for msg in r2['limitations'])
            assert [i['movie_id'] for i in r1['items']] == [i['movie_id'] for i in r2['items']]
            assert all('TMDB在线读取失败' not in msg for msg in r1['limitations'])

        r3 = boards._for_app(app, 3, 'top_rated', binding='bind-c', now=now, limit=5)
        assert r3['candidate_count'] == 59
        assert 1 not in {i['movie_id'] for i in r3['items']}
    finally:
        engine.dispose()


def test_archive_projection_cached_unless_monkeypatched(monkeypatch):
    first = recommendation_data._archive_projection()
    second = recommendation_data._archive_projection()
    assert first is second
    monkeypatch.setattr(recommendation_data, 'archived_features', lambda: {1: [{'id': 1001, 'archive_date': '2026-11-20', 'revenue': 99}]})
    patched = recommendation_data._archive_projection()
    assert patched[0] == (1,) and '"revenue":99' in patched[1]


def test_create_app_gates_supplement_warmup_and_warms_public_catalog(tmp_path, monkeypatch):
    from app import create_app
    import app as app_pkg
    from catalog import tmdb_supplement_api

    engine = create_engine('sqlite://')
    supp_calls = []
    pub_calls = []
    monkeypatch.setattr(app_pkg, 'create_engine', lambda *a, **kw: engine)
    monkeypatch.setattr('app.m6_database.validate_m6_url', lambda *a, **kw: None)
    monkeypatch.setattr('app.m6_database.verify_m6_connection', lambda *a, **kw: None)
    monkeypatch.setattr('catalog.m6_business.install_m6_business', lambda a: None)
    monkeypatch.setattr('assistant.credential_store.install', lambda a: None)
    monkeypatch.setattr('catalog.tmdb_api.install', lambda a: None)
    monkeypatch.setattr('assistant.api_v2.install', lambda a: None)
    monkeypatch.setattr('assistant.account_api.install', lambda a: None)
    monkeypatch.setattr('assistant.personal_api.install', lambda a: None)
    monkeypatch.setattr('analytics.api.install', lambda a: None)
    monkeypatch.setattr('admin_api.install', lambda a: None)
    monkeypatch.setattr('media_api.install', lambda a: None)
    monkeypatch.setattr('catalog.recommendation_feedback.install', lambda a: None)
    monkeypatch.setattr('catalog.admin_import_api.install', lambda a: None)
    monkeypatch.setattr('catalog.tmdb_supplement_api.install', lambda a: None)
    monkeypatch.setattr(tmdb_supplement_api, 'warm_async', lambda a: supp_calls.append(a))
    monkeypatch.setattr(recommendation_data, 'warm_async', lambda e: pub_calls.append(e))

    env = {
        'MI2_DATABASE_URL': 'mysql+pymysql://u:p@127.0.0.1:3306/mi2_m6_test',
        'MI2_M6_SCHEMA': 'mi2_m6_test',
        'MI2_M6_ACTIVITY_SCHEMA': 'mi2_m6_test',
        'MI2_MEDIA_CACHE': str(tmp_path),
    }
    try:
        with patch.dict('os.environ', env, clear=True):
            create_app({'M6_ISOLATED': True, 'TESTING': False})
        assert len(supp_calls) == 0 and len(pub_calls) == 1

        with patch.dict('os.environ', {**env, 'MI2_WARM_SUPPLEMENT_ON_START': '1'}, clear=True):
            create_app({'M6_ISOLATED': True, 'TESTING': False})
        assert len(supp_calls) == 1 and len(pub_calls) == 2

        with patch.dict('os.environ', {**env, 'MI2_WARM_SUPPLEMENT_ON_START': '1', 'MI2_SKIP_CACHE_WARMUP': '1'}, clear=True):
            create_app({'M6_ISOLATED': True, 'TESTING': False})
        assert len(supp_calls) == 1 and len(pub_calls) == 2
    finally:
        engine.dispose()


def test_current_state_mysql_collation_inventory_cache_and_safe_errno(tmp_path, monkeypatch, caplog):
    captured = []

    class FakeConn:
        dialect = SimpleNamespace(name='mysql')

        def execute(self, stmt, params=None):
            captured.append(str(stmt))
            return SimpleNamespace(all=lambda: [])

    current_state.revenue_fallbacks(FakeConn(), [1], {1: '1001'}, {1: 1001})
    assert captured and 'COLLATE utf8mb4_bin=e.external_id' in captured[0]

    with patch.object(recommendation_data, '_metadata_schema', return_value=(False, frozenset())):
        mysql_sql = str(
            recommendation_data.sql_scalar_projection(
                SimpleNamespace(dialect=mysql_dialect(), engine=None),
                boards._active_ids().cte('rec_active'),
                revenue=True,
            ).compile(dialect=mysql_dialect())
        )
    assert 'COLLATE utf8mb4_bin' in mysql_sql
    assert 'archived_features' not in mysql_sql and len(mysql_sql) < 10000

    monkeypatch.setenv('MI2_CATALOG_CACHE_DIR', str(tmp_path / 'cache'))
    monkeypatch.setenv('MI2_CATALOG_CACHE_TTL', '120')
    monkeypatch.setenv('MI2_TMDB_BOARD_DIR', str(tmp_path / 'boards'))
    (tmp_path / 'boards').mkdir(exist_ok=True)
    _, engine = _board_app(tmp_path, count=5)
    try:
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE movie_ratings_summary (movie_id INTEGER, source TEXT, score_value NUMERIC, vote_count INTEGER)'))
        r1 = data_quality.inventory(engine)
        assert r1['counts']['active'] == 5
        assert public_catalog_cache.peek(engine)['movies'] == 5
        monkeypatch.setattr(recommendation_data, '_load_candidates', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('inventory bypassed cache')))
        r2 = data_quality.inventory(engine)
        assert r2['scope_digest'] == r1['scope_digest']
    finally:
        public_catalog_cache.invalidate(engine)
        engine.dispose()

    app = Flask(__name__)
    app.config.update(TESTING=True, ADMIN_USERS=['admin'], MEDIA_CACHE_DIR=str(tmp_path / 'imp'))
    user = SimpleNamespace(id=1, status='active', username_normalized='admin')

    class Factory:
        @contextmanager
        def begin(self):
            yield SimpleNamespace(get=lambda *_: user)

    app.extensions.update(auth_engine=object(), auth_db=Factory(), tmdb_connection=SimpleNamespace())
    monkeypatch.setattr(admin_import_api, 'session_row', lambda _: SimpleNamespace(user_id=1, sid_hash='s'))
    monkeypatch.setattr(admin_import_api, 'csrf_ok', lambda _: True)
    admin_import_api.install(app)
    monkeypatch.setattr(
        data_quality,
        'inventory',
        lambda _e: (_ for _ in ()).throw(OperationalError('SELECT secret_col', {'secret': 'pw'}, Exception(1267, 'Illegal mix of collations'))),
    )
    resp = app.test_client().get('/api/admin/imports/quality')
    assert resp.status_code == 503
    assert resp.json == {'code': 'IMPORT_UNAVAILABLE', 'data': None}
    assert 'secret_col' not in caplog.text and 'Illegal mix' not in caplog.text
    assert 'errno=1267' in caplog.text


def test_dashboard_watermark_no_sawarning_and_countries_table_fast_path():
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        for ddl in (
            'CREATE TABLE movies (movie_id INTEGER, catalog_status TEXT, release_year INTEGER, overview TEXT, updated_at TEXT)',
            'CREATE TABLE movie_external_ids (movie_id INTEGER, source TEXT, external_id TEXT)',
            'CREATE TABLE genres (genre_id INTEGER, name_zh TEXT)',
            'CREATE TABLE movie_genres (movie_id INTEGER, genre_id INTEGER)',
            'CREATE TABLE movie_ratings_summary (movie_id INTEGER, source TEXT, score_value NUMERIC, score_scale INTEGER)',
            'CREATE TABLE movie_assets (asset_id INTEGER, movie_id INTEGER, asset_type TEXT, source_path TEXT, updated_at TEXT)',
            'CREATE TABLE movie_credits (credit_id INTEGER, movie_id INTEGER, person_id INTEGER, role_type TEXT)',
            'CREATE TABLE person_assets (person_id INTEGER, source_path TEXT, is_primary INTEGER)',
            'CREATE TABLE movie_source_records (id INTEGER, movie_id INTEGER, source TEXT, fetched_at TEXT, metadata_json JSON)',
            'CREATE TABLE tmdb_movie_countries (movie_id INTEGER, country_iso2 TEXT, country_name TEXT)',
        ):
            conn.execute(text(ddl))
        conn.execute(text("INSERT INTO movies VALUES (1,'active',2020,'ok','2026-11-01'),(2,'active',2021,'ok','2026-11-01'),(3,'hidden',2022,'no','2026-11-01')"))
        conn.execute(text("INSERT INTO tmdb_movie_countries VALUES (1,'us','USA'),(1,'CN','China'),(3,'FR','France')"))
        conn.execute(text("INSERT INTO movie_source_records VALUES (10,2,'legacy_movies2','2026-11-01',:p)"),
                     {'p': json.dumps({'production_countries': [{'iso_3166_1': 'JP'}]})})
    statements = []

    @event.listens_for(engine, 'before_cursor_execute')
    def observe(_conn, _cursor, stmt, params, _ctx, _many):
        statements.append(stmt)

    try:
        with engine.connect() as conn, warnings.catch_warnings(record=True):
            warnings.simplefilter('error', SAWarning)
            mark1 = dashboard._catalog_watermark(conn)
            assert len(mark1) == 8
            conn.execute(text('CREATE TABLE tmdb_movie_metadata (fetched_at TEXT, updated_at TEXT)'))
            mark2 = dashboard._catalog_watermark(conn)
            assert len(mark2) == 11
            snap = dashboard.catalog_snapshot(conn)
        assert snap['countries'] == [{'iso2': 'CN', 'count': 1}, {'iso2': 'JP', 'count': 1}, {'iso2': 'US', 'count': 1}]
        assert snap['countries_covered_movies'] == 2 and snap['countries_unknown_movies'] == 0
        assert not any('JSON_TABLE' in s.upper() for s in statements)
    finally:
        engine.dispose()


def test_production_countries_and_globe_summary_contract(tmp_path, monkeypatch):
    monkeypatch.setenv('MI2_CATALOG_CACHE_DIR', str(tmp_path / 'cache'))
    monkeypatch.setenv('MI2_CATALOG_CACHE_TTL', '120')
    rows = [_row(1), _row(2), _row(3), _row(4, catalog_status='hidden')]
    legacy = _source(31, 3, {'production_countries': [{'iso_3166_1': 'JP', 'name': 'Japan'}]}, NOW)
    legacy['source'] = 'legacy_movies2'
    sources = [
        _source(11, 1, {'id': 1001, 'production_countries': [{'iso_3166_1': 'ZZ', 'name': 'Wrong'}]}, NOW),
        _source(21, 2, {'id': 1002, 'production_countries': [{'iso_3166_1': 'FR', 'name': 'France'}]}, NOW),
        legacy,
    ]
    engine = _catalog_engine(
        rows,
        sources,
        mappings={1: 1001, 2: 1002},
        metadata=[_meta(1, 11), _meta(2, 21, topics={'countries': {'state': 'EMPTY', 'source_record_pk': 21}})],
    )
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE tmdb_movie_countries (movie_id INTEGER, country_iso2 TEXT, country_name TEXT)'))
        conn.execute(text("INSERT INTO tmdb_movie_countries VALUES (1,'US','United States'),(1,'CN','China'),(2,'DE','Germany'),(4,'GB','UK')"))
        conn.execute(text('DELETE FROM genres'))
        conn.execute(text('DELETE FROM movie_genres'))
        for gid in range(1, 13):
            conn.execute(text('INSERT INTO genres (genre_id, name_zh) VALUES (:id, :name)'), {'id': gid, 'name': f'类型{gid:02d}'})
        for gid in range(1, 13):
            conn.execute(text('INSERT INTO movie_genres (movie_id, genre_id) VALUES (1, :gid)'), {'gid': gid})
        conn.execute(text('INSERT INTO movie_genres (movie_id, genre_id) VALUES (2, 1), (3, 1), (3, 2), (4, 12)'))

    app = Flask(__name__)
    app.config.update(TESTING=True, M6_SCHEMA='mi2_m6_test_catalog', M6_ACTIVITY_SCHEMA='mi2_m6_test_activity')
    app.extensions['auth_engine'] = engine
    app.extensions['auth_db'] = SimpleNamespace(begin=lambda: (_ for _ in ()).throw(AssertionError()))
    m6_business.install_m6_business(app)
    client = app.test_client()
    try:
        pc = client.get('/api/catalog/production-countries').json['data']['items']
        by_id = {row['movie_id']: [c['iso2'] for c in row['countries']] for row in pc}
        assert by_id == {1: ['CN', 'US'], 2: [], 3: ['JP']}  # EMPTY wins over DE; hidden 4 excluded; legacy 3 preserved

        assert client.get('/api/catalog/globe-summary?extra=1').status_code == 400
        resp = client.get('/api/catalog/globe-summary')
        assert resp.status_code == 200
        summary = resp.json['data']
        assert summary['total_unique_movies'] == 3
        assert summary['covered_country_count'] == 3
        assert len(summary['overview_genres']) == 6
        assert len(summary['genre_options']) == 10
        assert summary['overview_genres'][0] == ['类型01', 3]
        assert summary['overview_genres'][1] == ['类型02', 2]
        assert [c['iso2'] for c in summary['countries']] == ['CN', 'JP', 'US']
        assert summary['countries'][1] == {'iso2': 'JP', 'catalog_count': 1, 'genres': [['类型01', 1], ['类型02', 1]]}

        # Invalidating public_catalog_cache also invalidates globe-summary and production-countries caches.
        with engine.begin() as conn:
            conn.execute(text("UPDATE movies SET catalog_status='hidden' WHERE movie_id=3"))
        public_catalog_cache.invalidate(engine)
        updated = client.get('/api/catalog/globe-summary').json['data']
        assert updated['total_unique_movies'] == 2
        assert updated['covered_country_count'] == 2
    finally:
        public_catalog_cache.invalidate(engine)
        engine.dispose()
