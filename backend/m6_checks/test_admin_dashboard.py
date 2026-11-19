"""Offline query/permission checks. Does not claim real MySQL integration coverage."""
import json
import math
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import (Column, DateTime, Integer, JSON, MetaData, String, Table,
                        create_engine, event, insert)
from sqlalchemy.exc import OperationalError
from sqlalchemy.sql.selectable import Select
from werkzeug.datastructures import MultiDict

import admin_api
import admin_dashboard as dashboard


def test_window_validation_and_calendar_semantics():
    for args in [MultiDict([('days', '7'), ('days', '30')]), MultiDict({'days': '1'}),
                 MultiDict({'days': '7;DROP TABLE movies'}), MultiDict({'admin': '1'}), MultiDict({'days': '０７'})]:
        with pytest.raises(ValueError): dashboard.parse_days(args)
    assert dashboard.parse_days(MultiDict()) == 30
    start, end = dashboard.window_for(7, datetime(2026, 1, 2, 12, tzinfo=timezone(timedelta(hours=8))))
    assert start == datetime(2025, 12, 27)
    assert end == datetime(2026, 1, 2, 4)
    daily = dashboard.filled_daily([('2026-01-01', 2)], start, end)
    assert len(daily) == 7 and sum(r['count'] for r in daily) == 2
    assert daily[-1] == {'date': '2026-01-02', 'count': 0}
    with pytest.raises(ValueError): dashboard.window_for(True)


def _app(monkeypatch, identity='admin'):
    app = Flask(__name__)
    user = SimpleNamespace(id=1, username='admin', username_normalized='admin') if identity == 'admin' else SimpleNamespace(id=2, username='viewer', username_normalized='viewer')
    class DB:
        def get(self, *_): return user
    class Factory:
        @contextmanager
        def begin(self): yield DB()
    app.config['ADMIN_USERS'] = ['admin']
    app.extensions.update(auth_db=Factory(), auth_engine=object(), m6_behavior=object())
    monkeypatch.setattr(admin_api, 'session_row', lambda db: None if identity == 'anonymous' else SimpleNamespace(user_id=user.id))
    admin_api.install(app)
    return app


@pytest.mark.parametrize('identity,status', [('anonymous', 401), ('viewer', 403)])
def test_dashboard_does_not_run_queries_without_admin(monkeypatch, identity, status):
    def denied(*_): raise AssertionError('Dashboard must not execute')
    monkeypatch.setattr(dashboard, 'build_dashboard', denied)
    response = _app(monkeypatch, identity).test_client().get('/api/admin/dashboard?days=30&admin=1')
    assert response.status_code == status
    assert response.json['data'] is None


def test_admin_window_validation_and_database_failure_do_not_leak(monkeypatch):
    app = _app(monkeypatch)
    def broken(*_): raise OperationalError('SELECT secret_column', {}, Exception('password=SHOULD_NOT_LEAK'))
    monkeypatch.setattr(dashboard, 'build_dashboard', broken)
    client = app.test_client()
    assert client.get('/api/admin/dashboard?days=7&days=30').status_code == 400
    response = client.get('/api/admin/dashboard?days=7')
    assert response.status_code == 503
    assert response.json == {'code': 'DATA_UNAVAILABLE', 'data': None}
    assert 'SHOULD_NOT_LEAK' not in response.get_data(as_text=True)


def _fixture_engine():
    """Execute portable SELECTs against isolated fixtures; MySQL JSON_TABLE is separate."""
    engine = create_engine('sqlite://')
    def load(v):
        if v is None: return None
        try: return json.loads(v)
        except (ValueError, TypeError): return v
    def extract(raw, path):
        value = load(raw)
        value = value.get(path[2:]) if isinstance(value, dict) else None
        return json.dumps(value) if value is not None else None
    def json_type(raw):
        v = load(raw)
        return 'BOOLEAN' if type(v) is bool else 'INTEGER' if type(v) is int else 'DOUBLE' if type(v) is float else 'STRING' if isinstance(v, str) else 'NULL'
    @event.listens_for(engine, 'connect')
    def configure(conn, _):
        conn.create_function('json_extract', 2, extract)
        conn.create_function('json_type', 1, json_type)
        conn.create_function('json_unquote', 1, lambda v: ('true' if load(v) is True else 'false' if load(v) is False else str(load(v))) if v is not None else None)
        conn.create_function('floor', 1, lambda v: math.floor(v) if v is not None else None)
    md = MetaData()
    definitions = {
        'movies': [('movie_id', Integer), ('title', String), ('catalog_status', String), ('release_year', Integer), ('overview', String)],
        'movie_external_ids': [('movie_id', Integer), ('source', String), ('external_id', String)],
        'genres': [('genre_id', Integer), ('name_zh', String)],
        'movie_genres': [('movie_id', Integer), ('genre_id', Integer)],
        'movie_ratings_summary': [('movie_id', Integer), ('source', String), ('score_value', Integer), ('score_scale', Integer)],
        'movie_assets': [('movie_id', Integer), ('asset_type', String), ('source_path', String)],
        'movie_credits': [('movie_id', Integer), ('person_id', Integer), ('role_type', String)],
        'person_assets': [('person_id', Integer), ('source_path', String), ('is_primary', Integer)],
        'user_movie_events': [('user_id', Integer), ('movie_id', Integer), ('created_at', DateTime), ('payload', JSON)],
        'user_movie_states': [('movie_id', Integer), ('state', JSON)],
        'usage': [('model', String), ('created_at', DateTime), ('status', String), ('tokens', JSON)],
        'auth_users': [('id', Integer), ('status', String), ('created_at', DateTime)],
        'auth_sessions': [('user_id', Integer), ('revoked_at', DateTime), ('idle_expires_at', DateTime), ('absolute_expires_at', DateTime)],
    }
    for name, cols in definitions.items(): Table(name, md, *(Column(key, kind) for key, kind in cols))
    md.create_all(engine)
    return engine, md


def _rows(engine, md, name, rows):
    with engine.begin() as c: c.execute(insert(md.tables[name]), rows)


def test_catalog_snapshot_deduplicates_and_never_mixes_rating_sources():
    engine, md = _fixture_engine()
    _rows(engine, md, 'movies', [dict(movie_id=1, title='A', catalog_status='active', release_year=2001, overview='A'), dict(movie_id=2, title='B', catalog_status='active', release_year=None, overview=' '), dict(movie_id=3, title='Hidden', catalog_status='hidden', release_year=2002, overview='x')])
    _rows(engine, md, 'movie_external_ids', [dict(movie_id=1, source='tmdb', external_id='1'), dict(movie_id=1, source='imdb', external_id='a'), dict(movie_id=2, source='tmdb', external_id='2'), dict(movie_id=3, source='tmdb', external_id='3')])
    _rows(engine, md, 'genres', [dict(genre_id=1, name_zh='剧情'), dict(genre_id=2, name_zh='喜剧')])
    _rows(engine, md, 'movie_genres', [dict(movie_id=1, genre_id=1), dict(movie_id=1, genre_id=2)])
    _rows(engine, md, 'movie_ratings_summary', [dict(movie_id=1, source='douban', score_value=8, score_scale=10), dict(movie_id=1, source='tmdb', score_value=99, score_scale=100), dict(movie_id=3, source='douban', score_value=10, score_scale=10)])
    _rows(engine, md, 'movie_assets', [dict(movie_id=1, asset_type='poster', source_path='/one.jpg'), dict(movie_id=1, asset_type='poster', source_path='/two.jpg'), dict(movie_id=3, asset_type='poster', source_path='/hidden.jpg')])
    _rows(engine, md, 'movie_credits', [dict(movie_id=1, person_id=1, role_type='cast'), dict(movie_id=1, person_id=2, role_type='cast'), dict(movie_id=2, person_id=3, role_type='cast')])
    _rows(engine, md, 'person_assets', [dict(person_id=1, source_path='/a.jpg', is_primary=1), dict(person_id=1, source_path='/b.jpg', is_primary=0)])
    class CountryResult:
        def mappings(self): return self
        def all(self): return [dict(iso2='CN', movie_count=1, covered_movies=1), dict(iso2='US', movie_count=1, covered_movies=1)]
    with engine.connect() as c:
        class Connection:
            def scalar(self, stmt):
                assert isinstance(stmt, Select)
                return c.scalar(stmt)
            def execute(self, stmt):
                if stmt is dashboard.COUNTRIES_SQL: return CountryResult()
                assert isinstance(stmt, Select)
                return c.execute(stmt)
        result = dashboard.catalog_snapshot(Connection())
    assert result['total'] == 2 and result['tmdb_mapped'] == 2
    assert result['average_rating'] == 8 and result['rating_unknown_movies'] == 1
    assert result['ratings'][-1] == {'name': '8–10', 'count': 1}
    assert result['countries_covered_movies'] == 1 and result['countries_unknown_movies'] == 1
    assert sum(x['count'] for x in result['countries']) == 2
    assert result['quality'] == {'total': 2, 'tmdb_mapped': 2, 'posters': 1, 'cast': 2, 'cast_photos': 1, 'overview': 1}


def test_behavior_events_and_current_states_have_independent_semantics():
    engine, md = _fixture_engine(); start = datetime(2026, 1, 1); end = datetime(2026, 1, 3, 12)
    _rows(engine, md, 'movies', [dict(movie_id=1, title='A', catalog_status='active')])
    _rows(engine, md, 'user_movie_events', [dict(user_id=1, movie_id=1, created_at=start, payload={'kind': 'favorite', 'action': 'set'}), dict(user_id=1, movie_id=1, created_at=start + timedelta(hours=2), payload={'kind': 'favorite', 'action': 'undo'}), dict(user_id=2, movie_id=1, created_at=end, payload={'kind': 'watched'})])
    _rows(engine, md, 'user_movie_states', [dict(movie_id=1, state={'watched': True, 'favorite': False, 'want': True, 'rating': 4}), dict(movie_id=1, state={'watched': 'true', 'rating': '5'})])
    repository = SimpleNamespace(tables={'events': md.tables['user_movie_events'], 'states': md.tables['user_movie_states']})
    with engine.connect() as c: result = dashboard.behavior_snapshot(c, repository, start, end)
    assert result['events'] == 2 and result['active_users'] == 1
    assert result['current'] == {'watched': 1, 'favorite': 0, 'want': 1, 'not_interested': 0, 'rated': 1}
    assert result['daily'] == [{'date': '2026-01-01', 'count': 2}, {'date': '2026-01-02', 'count': 0}, {'date': '2026-01-03', 'count': 0}]
    assert result['top_movies'] == [{'movie_id': 1, 'title': 'A', 'count': 2}]


def test_ai_provenance_unknown_usage_and_money_are_not_fabricated():
    engine, md = _fixture_engine(); now = datetime(2026, 1, 2)
    _rows(engine, md, 'usage', [
        dict(model='deepseek-chat', status='SUCCEEDED', created_at=now, tokens={'kind': 'BYOK_AGENT', 'paid_request': True, 'input_tokens': 10, 'output_tokens': 5}),
        dict(model='deepseek-chat', status='UNKNOWN', created_at=now, tokens={'kind': 'BYOK_AGENT', 'paid_request': True, 'input_tokens': None}),
        dict(model='mock-agent', status='SUCCEEDED', created_at=now, tokens={'kind': 'BYOK_AGENT', 'paid_request': False, 'input_tokens': 100, 'output_tokens': 20}),
        dict(model='legacy', status='SUCCEEDED', created_at=now, tokens={'input_tokens': '999', 'output_tokens': -2}),
        dict(model='deepseek-chat', status='NOT_DISPATCHED', created_at=now, tokens={'kind': 'BYOK_AGENT', 'paid_request': True, 'input_tokens': 99999, 'output_tokens': 99999}),
    ])
    app = SimpleNamespace(extensions={'m7_engine': engine, 'm7_ledger': SimpleNamespace(md=md)})
    result = dashboard.ai_snapshot(app, now - timedelta(days=1), now + timedelta(days=1))
    assert result['calls'] == 4
    assert (result['real_calls'], result['mock_calls'], result['unknown_provenance_calls']) == (2, 1, 1)
    assert result['known_input_tokens'] == 110 and result['known_output_tokens'] == 25
    assert result['real_input_tokens'] == 10 and result['real_output_tokens'] == 5
    assert result['unknown_calls'] == 2 and result['real_unknown_calls'] == 1
    assert result['amount'] is None and result['amount_status'] == 'UNKNOWN'
    unavailable = dashboard.ai_snapshot(SimpleNamespace(extensions={}), now, now + timedelta(days=1))
    assert unavailable['calls'] is None and unavailable['known_input_tokens'] is None


def test_valid_sessions_exclude_expiry_anonymous_disabled_and_revoked_accounts():
    engine, md = _fixture_engine(); start = datetime(2026, 1, 1); end = datetime(2026, 1, 3, 12)
    _rows(engine, md, 'auth_users', [dict(id=1, status='active', created_at=start - timedelta(days=1)), dict(id=2, status='disabled', created_at=start), dict(id=3, status='active', created_at=start + timedelta(days=1))])
    rows = [dict(user_id=user, revoked_at=revoked, idle_expires_at=idle, absolute_expires_at=absolute)
            for user, revoked, idle, absolute in [
                (1, None, end + timedelta(hours=1), end + timedelta(days=1)),
                (1, None, end, end + timedelta(days=1)),
                (1, end - timedelta(hours=1), end + timedelta(days=1), end + timedelta(days=1)),
                (2, None, end + timedelta(hours=1), end + timedelta(days=1)),
                (3, None, end + timedelta(hours=1), end),
                (None, None, end + timedelta(hours=1), end + timedelta(days=1)),
            ]]
    _rows(engine, md, 'auth_sessions', rows)
    with engine.connect() as c: result = dashboard.users_snapshot(c, start, end)
    assert result == {'accounts_total': 3, 'accounts_active': 2, 'new_in_window': 2, 'sessions_valid': 1}
