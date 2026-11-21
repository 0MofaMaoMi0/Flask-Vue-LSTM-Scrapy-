"""Offline regression coverage for administrator caching and catalog pagination."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import PureWindowsPath
from threading import Barrier, Event, current_thread
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine, event, text

import admin_api
import admin_dashboard as dashboard
from catalog import tmdb_supplement_api as supplement
from m6_checks.test_admin_v2_ops import _app, _fixture_engine, _movie, _repository, _rows
from m6_checks.test_admin_v2_resource_api import LATER, build_app, fixture_tables, signed_in


def test_http_returns_stale_while_one_independent_connection_revalidates(monkeypatch):
    tables = fixture_tables()
    app = build_app(tables)
    engine = supplement._catalog_engine(app)
    cache = supplement._cache_for(app, engine)
    now = [0.0]
    cache._clock = lambda: now[0]
    calls = []
    refreshing, resume = Event(), Event()
    load_watermark = supplement.repository.load_watermark

    def blocked_watermark(connection):
        calls.append((current_thread(), connection))
        if current_thread().name == 'tmdb-supplement-refresh':
            refreshing.set()
            assert resume.wait(5)
        return load_watermark(connection)

    monkeypatch.setattr(supplement.repository, 'load_watermark', blocked_watermark)
    client = app.test_client()
    url = '/api/admin/tmdb-supplement/analytics'
    with signed_in():
        assert client.get(url).json['data']['scope']['cache']['status'] == 'MISS'
        now[0] = cache.ttl + 1
        tables['tmdb_movie_metadata'][0]['updated_at'] = LATER
        try:
            first = client.get(url).json['data']['scope']['cache']
            assert refreshing.wait(5)
            second = client.get(url).json['data']['scope']['cache']
            assert first == second
            assert first['stale'] and first['status'] == 'STALE' and first['load_count'] == 1
            assert len(calls) == 2 and calls[0][0] is not calls[1][0]
            assert calls[0][1] is not calls[1][1]
        finally:
            resume.set()
        assert cache._load_lock.acquire(timeout=5)
        cache._load_lock.release()
        fresh = client.get(url).json['data']['scope']['cache']
    assert fresh['status'] == 'HIT' and fresh['load_count'] == 2


def test_failed_swr_preserves_snapshot_and_unchanged_watermark_reuses_generation():
    now = [0.0]
    cache = supplement.DocumentCache(ttl=1, clock=lambda: now[0])
    watermark = {'document_count': 1}
    cache.fetch(watermark, lambda: ['first'])
    cache.put_result(1, 'query', 'aggregate')
    now[0] = 2.0

    def broken():
        raise RuntimeError('offline failure')

    assert cache.fetch_swr({'document_count': 2}, broken)[0] == ['first']
    assert cache._load_lock.acquire(timeout=5)
    cache._load_lock.release()
    assert cache.snapshot()[1]['stale']
    assert cache.get_result(1, 'query') == 'aggregate'

    def unexpected_reload():
        pytest.fail('an unchanged watermark must not rebuild documents')

    cache.fetch_swr(watermark, unexpected_reload)
    assert cache._load_lock.acquire(timeout=5)
    cache._load_lock.release()
    documents, report = cache.snapshot()
    assert documents == ['first']
    assert report['status'] == 'HIT' and report['load_count'] == 1
    assert cache.get_result(1, 'query') == 'aggregate'


def _disk_document(movie_id):
    return {'movie_id': movie_id, 'schema_version': supplement.repository.SCHEMA_VERSION,
            'facts': {}, 'field_status': {}}


def test_disk_restore_is_watermark_schema_and_engine_bound(tmp_path, monkeypatch):
    monkeypatch.delenv('MI2_ADMIN_CACHE_DIR', raising=False)
    app = Flask(__name__)
    app.config['MEDIA_CACHE_DIR'] = str(tmp_path)
    first_db = f'sqlite:///{(tmp_path / "first.db").as_posix()}'
    second_db = f'sqlite:///{(tmp_path / "second.db").as_posix()}'
    engines = [create_engine(url) for url in (first_db, first_db, second_db, first_db, first_db)]
    first, restored, isolated, changed, wrong_schema = [
        supplement._cache_for(app, engine) for engine in engines
    ]
    watermark = {'document_count': 1, 'max_updated_at': 'one'}

    assert first.fetch(watermark, lambda: [_disk_document(1)])[1]['status'] == 'MISS'
    assert first._disk.path.parent == tmp_path / '.admin-cache'
    documents, report = restored.fetch(watermark, lambda: pytest.fail('disk hit must avoid loader'))
    assert (documents, report['status']) == ([_disk_document(1)], 'DISK_HIT')
    assert isolated._disk.path != first._disk.path
    isolated._disk.path.write_bytes(first._disk.path.read_bytes())
    assert isolated.fetch(watermark, lambda: [_disk_document(2)])[0] == [_disk_document(2)]
    new_watermark = dict(watermark, max_updated_at='two')
    assert changed.fetch(new_watermark, lambda: [_disk_document(3)])[0] == [_disk_document(3)]
    monkeypatch.setattr(supplement, 'SNAPSHOT_VERSION', supplement.SNAPSHOT_VERSION + 1)
    assert wrong_schema.fetch(new_watermark, lambda: [_disk_document(4)])[1]['status'] == 'MISS'


def test_corrupt_disk_rebuilds_and_failed_replace_preserves_snapshot(tmp_path, monkeypatch):
    disk = supplement._DiskSnapshot(tmp_path, 'catalog')
    disk.path.write_bytes(b'bad gzip')
    cache = supplement.DocumentCache(disk=disk)
    watermark = {'document_count': 1}
    assert cache.fetch(watermark, lambda: [_disk_document(1)])[1]['status'] == 'MISS'
    before = disk.path.read_bytes()
    replace_attempts = []

    def failed_replace(source, destination):
        assert source.parent == destination.parent == tmp_path
        assert source != destination and source.exists()
        replace_attempts.append(source)
        raise OSError('offline replacement failure')

    monkeypatch.setattr(supplement.os, 'replace', failed_replace)
    assert cache.fetch(dict(watermark, max_updated_at='changed'),
                       lambda: [_disk_document(2)])[0] == [_disk_document(2)]
    assert disk.path.read_bytes() == before
    assert len(replace_attempts) == 1
    assert list(tmp_path.iterdir()) == [disk.path]


@pytest.mark.parametrize('configured, media', [
    ('C:/admin', 'F:/media'),
    (None, 'c:/media'),
    (None, '/tmp/mi2-media'),
    ('relative', 'F:/media'),
    ('//server/C$/cache', 'F:/media'),
    ('F:/junction', 'F:/media'),
])
def test_windows_rejects_unsafe_snapshot_directory(configured, media, monkeypatch):
    class WindowsPath(PureWindowsPath):
        def resolve(self):
            return PureWindowsPath('C:/redirected') if self.name == 'junction' else self

    env = {'MI2_ADMIN_CACHE_DIR': configured} if configured else {}
    monkeypatch.setattr(supplement, 'os', SimpleNamespace(name='nt', environ=env))
    monkeypatch.setattr(supplement, 'Path', WindowsPath)
    app = SimpleNamespace(config={'MEDIA_CACHE_DIR': media})
    assert supplement._snapshot_dir(app) is None
    env['MI2_ADMIN_CACHE_DIR'] = 'F:/approved/cache'
    assert supplement._snapshot_dir(app) == PureWindowsPath('F:/approved/cache')


def test_warm_async_is_nonblocking_and_runs_once(monkeypatch):
    app = build_app(fixture_tables())
    cache = supplement._cache_for(app, supplement._catalog_engine(app))
    loading, resume = Event(), Event()
    calls = []

    def loader(_):
        calls.append(current_thread())
        loading.set()
        assert resume.wait(5)
        return [_disk_document(1)]

    monkeypatch.setattr(supplement, '_load_all_documents', loader)
    try:
        supplement.warm_async(app)
        assert loading.wait(5)
        supplement.warm_async(app)
        assert calls[0] is not current_thread()
    finally:
        resume.set()
    assert cache._load_lock.acquire(timeout=5)
    cache._load_lock.release()
    supplement.warm_async(app)
    assert len(calls) == 1
    assert cache.snapshot()[0] == [_disk_document(1)]


def _watermark_engine():
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        for ddl in (
            'CREATE TABLE movies (movie_id INTEGER, updated_at TEXT)',
            'CREATE TABLE movie_assets (asset_id INTEGER, updated_at TEXT)',
            'CREATE TABLE movie_credits (credit_id INTEGER)',
        ):
            connection.execute(text(ddl))
    return engine


def test_dashboard_reuses_catalog_across_windows_and_rechecks_watermark(monkeypatch):
    first, second = _watermark_engine(), _watermark_engine()
    app = SimpleNamespace(config={}, extensions={'auth_engine': first, 'm6_behavior': None})
    clock = [0.0]
    calls = []
    monkeypatch.setattr(dashboard, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setenv('MI2_ADMIN_DASHBOARD_CACHE_TTL', '10')

    def snapshot(connection):
        calls.append(connection.engine)
        return {'generation': len(calls)}

    monkeypatch.setattr(dashboard, 'catalog_snapshot', snapshot)
    for name in ('behavior_snapshot', 'users_snapshot', 'feedback_tables',
                 'recommendation_snapshot', 'ai_snapshot'):
        monkeypatch.setattr(dashboard, name, lambda *_: {})

    def get(days=30, **kwargs):
        return dashboard.build_dashboard(app, days, **kwargs)['catalog']['generation']

    assert get(7) == get(30) == get(90) == 1
    clock[0] = 11
    assert get() == 1  # unchanged watermark revalidates without a rebuild
    with first.begin() as connection:
        connection.execute(text('CREATE TABLE tmdb_movie_metadata (fetched_at TEXT, updated_at TEXT)'))
        connection.execute(text("INSERT INTO tmdb_movie_metadata VALUES ('a', 'a')"))
    assert get() == 1
    clock[0] = 22
    assert get() == 2
    assert get(refresh_catalog=True) == 3
    dashboard.invalidate_dashboard_cache(first)
    assert get() == 4
    app.extensions['auth_engine'] = second
    assert get() == 5
    assert get(catalog_cache_ttl=0) == 6
    clock[0] = 122
    assert get() == 7  # periodic rebuild captures changes outside the high-water columns


def test_dashboard_single_flight(monkeypatch):
    engine = _watermark_engine()
    ready, resume = Event(), Event()
    barrier = Barrier(4)
    calls = []
    monkeypatch.setattr(dashboard, '_catalog_watermark', lambda _: ('catalog',))

    def snapshot(_):
        calls.append(current_thread())
        ready.set()
        assert resume.wait(5)
        return {'total': 1}

    monkeypatch.setattr(dashboard, 'catalog_snapshot', snapshot)

    def request_catalog():
        barrier.wait(timeout=5)
        return dashboard._cached_catalog_snapshot(SimpleNamespace(engine=engine), 120, False)

    with ThreadPoolExecutor(max_workers=4) as threads:
        results = [threads.submit(request_catalog) for _ in range(4)]
        try:
            assert ready.wait(5)
        finally:
            resume.set()
        snapshots = [future.result(timeout=5) for future in results]
        assert [data for data, _ in snapshots] == [{'total': 1}] * 4
        assert len({generated_at for _, generated_at in snapshots}) == 1
    assert len(calls) == 1


@pytest.mark.parametrize('normalized_table', [True, False])
def test_admin_catalog_limits_all_associations_to_current_page(monkeypatch, normalized_table):
    engine, md = _fixture_engine()
    with engine.begin() as connection:
        for ddl in (
            'CREATE TABLE movie_ratings_summary (movie_id INTEGER, score_value NUMERIC)',
            'CREATE TABLE movie_assets (movie_id INTEGER)',
            'CREATE TABLE movie_credits (movie_id INTEGER)',
        ):
            connection.execute(text(ddl))
        for table in ('movie_assets', 'movie_credits'):
            connection.execute(text(f'INSERT INTO {table} VALUES (2), (3), (3)'))
        connection.execute(text('INSERT INTO movie_ratings_summary VALUES (3, 8), (3, 9)'))
        if normalized_table:
            connection.execute(text('CREATE TABLE tmdb_movie_countries (movie_id INTEGER, country_iso2 TEXT)'))
            connection.execute(text("INSERT INTO tmdb_movie_countries VALUES (2, 'FR')"))
    for movie_id in range(1, 6):
        _movie(engine, md, movie_id, status='hidden' if movie_id == 4 else 'active')
    _rows(engine, md, 'genres', [{'genre_id': 1, 'name_zh': '剧情'}])
    _rows(engine, md, 'movie_genres', [{'movie_id': 3, 'genre_id': 1}])
    _rows(engine, md, 'movie_source_records', [
        {'id': 10 + mid, 'movie_id': mid, 'source': 'tmdb',
         'metadata_json': {'production_countries': [{'iso_3166_1': 'JP'}]}}
        for mid in (1, 2, 3, 4, 5)
    ])
    app = _app(engine, _repository(md), monkeypatch)
    client = app.test_client()
    statements = []

    @event.listens_for(engine, 'before_cursor_execute')
    def capture(_conn, _cursor, statement, params, _context, _many):
        statements.append((statement, params))

    response = client.get('/api/admin/catalog?limit=2&offset=1')
    assert response.status_code == 200
    data = response.json['data']
    assert {key: value for key, value in data.items() if key != 'items'} == {
        'total': 4, 'limit': 2, 'offset': 1, 'returned': 2, 'has_more': True,
    }
    assert [row['movie_id'] for row in data['items']] == [2, 3]
    assert data['items'][0]['countries'] == (['FR'] if normalized_table else ['JP'])
    assert data['items'][1] == {
        'movie_id': 3, 'title': '影片', 'year': 2015, 'countries': ['JP'],
        'genres': ['剧情'], 'rating': 9.0, 'credits': 2, 'assets': 2,
    }
    dependent = [(sql, params) for sql, params in statements if any(
        f'FROM {table}' in sql for table in
        ('movie_genres', 'movie_ratings_summary', 'movie_credits', 'movie_assets',
         'tmdb_movie_countries', 'movie_source_records')
    )]
    assert len(dependent) == (7 if normalized_table else 6)
    assert all(' IN (' in sql for sql, _ in dependent)
    payload_params = [params for sql, params in dependent if 'metadata_json' in sql]
    assert payload_params == ([(13,)] if normalized_table else [(12, 13)])
    assert all(set(params) == {2, 3} for sql, params in dependent
               if 'movie_source_records' not in sql)

    statements.clear()
    monkeypatch.setattr(admin_api, 'CATALOG_CHUNK_SIZE', 2)
    data = client.get('/api/admin/catalog').json['data']
    assert data['limit'] is None and data['returned'] == data['total'] == 4 and not data['has_more']
    assert [row['movie_id'] for row in data['items']] == [1, 2, 3, 5]
    payload_reads = [(sql, params) for sql, params in statements if 'metadata_json' in sql]
    assert len(payload_reads) == 2
    assert all(' IN (' in sql and len(params) <= 2 for sql, params in payload_reads)
    data = client.get('/api/admin/catalog?offset=4').json['data']
    assert data == {'items': [], 'total': 4, 'limit': 500, 'offset': 4,
                    'returned': 0, 'has_more': False}

    for query in ('limit=0', 'limit=501', 'limit=abc', 'limit=1&limit=2',
                  'offset=-1', 'offset=1&offset=2', 'offset=9223372036854775808'):
        response = client.get(f'/api/admin/catalog?{query}')
        assert response.status_code == 400
        assert response.json == {'code': 'INVALID_INPUT', 'data': None}
