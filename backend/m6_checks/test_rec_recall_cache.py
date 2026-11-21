import json
import threading
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import event, select, text
from sqlalchemy.dialects.mysql import dialect as mysql_dialect

from catalog import admin_importer, m6_business, public_catalog_cache, recommendation_boards as boards, recommendation_data
from catalog.ingest_hub.pipeline import _invalidate_public_catalog
from catalog.recommendation_data import _load_candidates
from test_current_state_cache import NOW, _catalog_engine, _meta, _row, _source


class Activity:
    def __init__(self):
        self.tables = {}
        self.users = {7: {'1': {'favorite': True}, '2': {'want': True}, '3': {'not_interested': True},
                          '4': {'watched': True}, '5': {'watched': True, 'want': True}}}
        self.snapshots = 0

    def snapshot(self, user):
        self.snapshots += 1
        return {mid: dict(row) for mid, row in self.users.get(user, {}).items()}

    def apply(self, user, event):
        self.users.setdefault(user, {})[str(event['movie_id'])] = {'not_interested': True}
        return {'duplicate': False, 'version': 8}


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setenv('MI2_TMDB_BOARD_DIR', str(tmp_path))
    monkeypatch.delenv('MI2_RECOMMENDATION_CACHE_DIR', raising=False)
    rows = [_row(mid, title=f'Film {mid}', original_title='Shared', runtime_minutes=88,
                 original_language='en', release_year=1980 if mid >= 1038 else 2020,
                 is_adult=mid == 1049, catalog_status='inactive' if mid == 1050 else 'active')
            for mid in range(1, 1051)]
    mapped = {mid: 1000 + mid for mid in range(1, 1051)}
    sources = [_source(mid, mid, dict(id=tid, vote_count=mid + 100, vote_average=7.8,
                   popularity=mid, revenue=mid * 200), NOW) for mid, tid in mapped.items()]
    sources += [_source(2001, 1, dict(id=1001, vote_count=150, vote_average=8.9, popularity=4), NOW + timedelta(days=1)),
                _source(2002, 2, dict(id=8888, vote_count=9999, vote_average=10, revenue=999999999), NOW + timedelta(days=1))]
    assets = [dict(asset_id=mid, movie_id=mid, asset_type='backdrop', source='tmdb',
                   source_path=f'/hero{mid}.jpg', is_primary=True) for mid in range(1, 1051)]
    credits = [dict(movie_id=mid, person_id=12, role_type='crew', job='Director', sort_order=1)
               for mid in [1, 2, 1047]]
    meta = [_meta(mid, mid) for mid in range(3, 1051)]
    meta += [_meta(1, 2001, base_at=NOW + timedelta(days=1)), _meta(2, 2, topics={'metrics': {'state': 'EMPTY'}})]
    engine = _catalog_engine(rows, sources, mapped, meta, assets, credits)
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE movie_ratings_summary (movie_id INTEGER, source TEXT, score_value NUMERIC, vote_count INTEGER)'))
        connection.execute(text("INSERT INTO movie_ratings_summary SELECT movie_id, 'tmdb', 7.8, movie_id+100 FROM movies"))
    app = Flask(__name__)
    app.config.update(TESTING=True, MEDIA_CACHE_DIR=str(tmp_path))
    app.extensions.update(auth_engine=engine, m6_behavior=Activity())
    return app, engine, tmp_path


def _observe_sql(engine):
    statements = []

    @event.listens_for(engine, 'before_cursor_execute')
    def observe(connection, cursor, statement, parameters, context, many):
        statements.append((statement, parameters, context))

    return statements


def _official(folder, now, ids, channel='popular'):
    doc = {'schema': 1, 'channel': channel, 'captured_at': now.isoformat(), 'items': [
        {'id': tid, 'title': f'TMDB {tid}', 'source_rank': rank, 'vote_average': 8.2, 'vote_count': 121}
        for rank, tid in enumerate(ids, 1)]}
    (folder / f'{channel}.json').write_text(json.dumps(doc), encoding='utf-8')


@pytest.mark.parametrize('channel,expected,maximum', [
    ('daily', 1045, 500), ('weekly', 1045, 500), ('personal', 1045, 500),
    ('because', 1044, 460), ('watchlist', 1, 1), ('top_rated', 1046, 400),
    ('revenue', 1047, 400), ('popular', 3, 2),
])
def test_bounded_recall_and_exact_counts(catalog, monkeypatch, channel, expected, maximum):
    app, engine, folder = catalog
    now = NOW.replace(tzinfo=timezone.utc)
    _official(folder, now, [1001, 1002, 9000])
    calls = []

    def bounded(engine, *, movie_ids):
        calls.append(set(movie_ids))
        return _load_candidates(engine, movie_ids=movie_ids)

    monkeypatch.setattr(boards, '_load_candidates', bounded)
    sql = _observe_sql(engine)
    result = boards.for_app(app, 7, channel, now=now, limit=13)
    assert result['catalog_total'] == 1048
    assert result['popularity_covered'] == 1047
    assert result['candidate_count'] == expected
    assert 0 < len(set.union(*calls)) <= maximum
    assert all(item['backdrop'].startswith('/api/media/poster?u=') if item['in_catalog'] else item['backdrop'] is None
               for item in result['items'])
    backdrops = [params for stmt, params, _ in sql if 'movie_assets.asset_type = ?' in stmt and 'backdrop' in params]
    assert len(backdrops) == 1 and len(backdrops[0]) <= 14
    for stmt, params, _ in sql:
        if stmt.startswith('SELECT movie_id, tmdb_id,'):
            assert ' WHERE movie_id IN (' in stmt and len(params) <= 300
        if stmt.startswith('SELECT movie_source_records.id, movie_source_records.metadata_json'):
            assert ' WHERE movie_source_records.id IN (' in stmt
    if channel == 'because':
        assert result['items'][0]['movie_id'] in (2, 1047)
    if channel == 'top_rated':
        baseline = boards.board(_load_candidates(engine), app.extensions['m6_behavior'].snapshot(7),
                                7, channel, now=now, limit=13)
        assert [item['movie_id'] for item in result['items']] == [item['movie_id'] for item in baseline['items']]
        assert result['items'][0]['score'] == pytest.approx(baseline['items'][0]['score'])
    if channel == 'revenue':
        assert result['items'][0]['revenue'] == 1048 * 200
    for _, _, context in sql:
        if context.compiled is not None and context.compiled.statement.is_select:
            context.compiled.statement.compile(dialect=mysql_dialect())


def test_quality_recall_ranks_douban_only_ratings(catalog):
    _, engine, _ = catalog
    with engine.begin() as connection:
        connection.execute(text('DELETE FROM movie_ratings_summary'))
        connection.execute(text('ALTER TABLE movie_ratings_summary ADD COLUMN score_scale INTEGER'))
        connection.execute(text(
            'INSERT INTO movie_ratings_summary (movie_id, source, score_value, vote_count, score_scale) '
            "VALUES (1, 'douban', 8.5, 200, 10), (2, 'douban', 9.5, 200, 10), (3, 'douban', 95, 200, 100)"
        ))
        eligible = boards._active_ids().cte('rec_eligible')
        query, quality = boards._quality(connection, select(eligible.c.movie_id), eligible, eligible)
        query = query.order_by(*quality).limit(2)
        query.compile(dialect=mysql_dialect())
        assert connection.execute(query).scalars().all() == [2, 1]


def test_hard_filters_and_period_order_outside_recall(catalog):
    app, engine, folder = catalog
    now = NOW.replace(tzinfo=timezone.utc)
    opts = {'filters': {}, 'keyword': '', 'min_votes': None, 'weights': {'favorite': 3.0, 'want': 2.0, 'rating': 1.0}}
    boards.persisted_order(folder / '.private-recommendations', 7, 'daily', boards.period_for('daily', now), opts, [3, 888])
    assert boards.for_app(app, 7, 'daily', now=now)['items'][0]['movie_id'] == 888
    result = boards.for_app(app, 7, 'daily', filters={'language': 'en', 'year_to': 1980, 'genre': '剧', 'runtime_max': 90},
                            keyword='SHARED', min_votes=1145, now=now)
    assert result['candidate_count'] == 4
    assert {item['movie_id'] for item in result['items']} == {1045, 1046, 1047, 1048}
    assert boards.for_app(app, 7, 'daily', filters={'unwatched': False}, now=now)['candidate_count'] == 1047


def test_legacy_and_archive_revenue_match_full_board(catalog, monkeypatch):
    app, engine, _ = catalog
    now = NOW.replace(tzinfo=timezone.utc)
    archive = {1047: [{'id': 2047, 'archive_date': '2026-11-20', 'revenue': 400000, 'popularity': 59}],
               1048: [{'id': 2048, 'archive_date': '2026-11-20', 'revenue': 500000, 'popularity': 60}],
               2: [{'id': 2002, 'archive_date': '2026-11-20', 'revenue': 900000, 'popularity': 900}]}
    monkeypatch.setattr(recommendation_data, 'archived_features', lambda: archive)
    with engine.begin() as connection:
        connection.execute(text('DELETE FROM tmdb_movie_metadata WHERE movie_id=1048'))
        connection.execute(text('DELETE FROM movie_source_records WHERE movie_id=1048'))
    result = boards.for_app(app, 7, 'revenue', now=now)
    assert result['items'][0]['movie_id'] == 1048 and result['items'][0]['revenue'] == 500000
    assert 1047 in {item['movie_id'] for item in result['items']}
    baseline = boards.board(_load_candidates(engine), app.extensions['m6_behavior'].snapshot(7),
                            7, 'revenue', now=now)
    assert result['candidate_count'] == baseline['candidate_count']
    assert result['popularity_covered'] == baseline['popularity_covered']
    assert [(item['movie_id'], item['revenue']) for item in result['items']] == [
        (item['movie_id'], item['revenue']) for item in baseline['items']]


def test_small_catalog_keeps_full_reranker_equivalence(catalog, monkeypatch):
    app, engine, _ = catalog
    now = NOW.replace(tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(text("UPDATE movies SET catalog_status='inactive' WHERE movie_id > 120"))
    full = _load_candidates(engine)
    baseline = boards.board(full, app.extensions['m6_behavior'].snapshot(7), 7, 'daily', limit=13, now=now)
    result = boards.for_app(app, 7, 'daily', limit=13, now=now)
    assert result['catalog_total'] == 120
    assert result['candidate_count'] == baseline['candidate_count']
    assert [{key: value for key, value in item.items() if key != 'backdrop'} for item in result['items']] == baseline['items']


def test_cache_hit_returns_isolated_dtos_and_snapshot_updates(catalog):
    app, engine, folder = catalog
    sql = _observe_sql(engine)
    now = NOW.replace(tzinfo=timezone.utc)
    _official(folder, now, [1001])
    first = boards.for_app(app, 7, 'popular', now=now, filters={'year_to': 2025, 'language': 'en'})
    first['items'][0]['genres'].clear()
    first['items'][0]['backdrop'] = None
    sql.clear()
    second = boards.for_app(app, 7, 'popular', now=now, filters={'language': 'en', 'year_to': 2025})
    assert second['items'][0]['genres'] and second['items'][0]['backdrop'] and not sql
    _official(folder, now + timedelta(seconds=1), [1002])
    assert boards.for_app(app, 7, 'popular', now=now, filters={'year_to': 2025, 'language': 'en'})['items'][0]['movie_id'] == 2
    assert sql


def test_post_behavior_invalidates_only_user_and_coalesces_warm(catalog, monkeypatch):
    app, engine, _ = catalog
    activity = app.extensions['m6_behavior']
    app.extensions['auth_db'] = SimpleNamespace(begin=lambda: nullcontext(None))
    monkeypatch.setattr(m6_business, 'MySQLBehaviorRepository', lambda *args: activity)
    monkeypatch.setattr(m6_business, 'session_row', lambda db, lock: SimpleNamespace(user_id=7))
    csrf = [False]
    monkeypatch.setattr(m6_business, 'csrf_ok', lambda row: csrf[0])
    m6_business.install_m6_business(app)
    client = app.test_client()
    queued = []
    monkeypatch.setattr(boards, '_refresh_workers', SimpleNamespace(submit=queued.append))
    first = client.get('/api/catalog/recommendations?channel=daily&limit=13').json['data']
    other = boards.for_app(app, 8, 'daily', limit=13)
    blocked = first['items'][0]['movie_id']
    assert client.post('/api/catalog/behavior', json={'movie_id': blocked}).status_code == 403
    assert not queued
    csrf[0] = True
    assert client.post('/api/catalog/behavior', json={'movie_id': blocked}).status_code == 200
    assert client.post('/api/catalog/behavior', json={'movie_id': first['items'][1]['movie_id']}).status_code == 200
    assert len(queued) == 1
    queued.pop()()
    sql = _observe_sql(engine)
    fresh = client.get('/api/catalog/recommendations?channel=daily&limit=13').json['data']
    assert fresh['candidate_count'] == first['candidate_count'] - 2
    assert blocked not in {item['movie_id'] for item in fresh['items']}
    assert boards.for_app(app, 8, 'daily', limit=13) == other and not sql


def test_background_write_during_warm_cannot_republish_old_generation(catalog, monkeypatch):
    app, engine, _ = catalog
    activity = app.extensions['m6_behavior']
    started, resume, finished = threading.Event(), threading.Event(), threading.Event()
    saved_snapshot = activity.snapshot
    run_for_app = boards._for_app
    snapshots = []
    results = []

    def snapshot(user):
        state = saved_snapshot(user)
        snapshots.append(state)
        if len(snapshots) == 1:
            started.set()
            assert resume.wait(5)
        return state

    def observe(*args, **kwargs):
        result = run_for_app(*args, **kwargs)
        results.append(result['candidate_count'])
        if len(results) == 2:
            finished.set()
        return result

    monkeypatch.setattr(activity, 'snapshot', snapshot)
    monkeypatch.setattr(boards, '_for_app', observe)
    assert boards.warm_user_home_async(app, 7)
    try:
        assert started.wait(5)
        activity.apply(7, {'movie_id': 34})
        boards.invalidate_board_cache(engine, 7, behavior_version=8)
        assert not boards.warm_user_home_async(app, 7)
    finally:
        resume.set()
    assert finished.wait(5) and results == [1045, 1044]
    sql = _observe_sql(engine)
    assert boards.for_app(app, 7, 'daily', limit=13)['candidate_count'] == 1044
    assert not sql


def test_stale_while_revalidate_and_import_invalidation(catalog, monkeypatch):
    app, engine, _ = catalog
    opts = dict(keyword='Film 1048')
    first = boards.for_app(app, 7, 'personal', **opts)
    assert first['candidate_count'] == 1
    queued = []
    monkeypatch.setattr(boards, '_refresh_workers', SimpleNamespace(submit=queued.append))
    monkeypatch.setattr(boards, 'BOARD_TTL', 0)
    assert boards.for_app(app, 7, 'personal', **opts) == first
    assert boards.for_app(app, 7, 'personal', **opts) == first
    assert len(queued) == 1
    queued.pop()()
    monkeypatch.setattr(boards, 'BOARD_TTL', 120)
    with engine.begin() as connection:
        connection.execute(text("UPDATE movies SET catalog_status='inactive' WHERE movie_id=1048"))
    with engine.connect() as connection:
        monkeypatch.setattr(public_catalog_cache, 'invalidate', lambda engine: (_ for _ in ()).throw(RuntimeError('synthetic cache fault')))
        _invalidate_public_catalog(connection)
    assert boards.for_app(app, 7, 'personal', **opts)['candidate_count'] == 0
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE ingest_batches (batch_id INTEGER PRIMARY KEY, source TEXT, mode TEXT, '
                                'status TEXT, started_at DATETIME, finished_at DATETIME, source_snapshot_ref TEXT, counts_json JSON)'))
        db = connection.connection.driver_connection
        db.create_function('GET_LOCK', 2, lambda *args: 1)
        db.create_function('RELEASE_LOCK', 1, lambda *args: 1)
    monkeypatch.setattr(admin_importer, 'verify_m6_connection', lambda *args: None)
    importer = admin_importer.CatalogImporter(engine, {'M6_SCHEMA': 'synthetic'})
    monkeypatch.setattr(importer, 'plan', lambda *args: {'movie_id': None})

    def apply(connection, payload, plan, batch, now):
        connection.execute(text("UPDATE movies SET catalog_status='active' WHERE movie_id=1048"))
        return {'movie_id': 1048}

    monkeypatch.setattr(importer, '_apply_one', apply)
    assert not importer.apply('rec-cache-test', [{'id': 2048}], lambda before: None)['replayed']
    assert boards.for_app(app, 7, 'personal', **opts)['candidate_count'] == 1
