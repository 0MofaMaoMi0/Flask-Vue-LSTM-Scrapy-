"""WP-B V2 operational analytics: offline checks on isolated SQLite fixtures.

No MySQL, no network, no writes outside the in-memory database. These tests
assert the published read-model contract (statuses, denominators, unknown
handling) and guard against the MySQL-1038 regression (ordering on large JSON
columns).
"""
import json
import math
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import (Column, DateTime, Integer, JSON, MetaData, String, Table,
                        create_engine, event, insert, text)
from sqlalchemy.exc import OperationalError
from werkzeug.datastructures import MultiDict

import admin_api
import admin_dashboard as dashboard
from catalog import recommendation_feedback

NOW = datetime(2026, 11, 18, 12, 0, 0)
START = datetime(2026, 11, 12)
END = datetime(2026, 11, 18, 12)

# SQLite only auto-increments INTEGER PRIMARY KEY, so the activity-schema DDL is
# recreated here; the SQLAlchemy Table objects under test stay the production ones.
FEEDBACK_DDL = (
    'CREATE TABLE recommendation_impressions ('
    'impression_id INTEGER PRIMARY KEY AUTOINCREMENT, request_id VARCHAR(36) NOT NULL, '
    'user_id INTEGER NOT NULL, channel VARCHAR(32) NOT NULL, period VARCHAR(16), '
    'board_version VARCHAR(32) NOT NULL, context VARCHAR(32) NOT NULL, '
    'position INTEGER NOT NULL, movie_id INTEGER, tmdb_id INTEGER, shown_at DATETIME NOT NULL)',
    'CREATE TABLE recommendation_clicks ('
    'click_id INTEGER PRIMARY KEY AUTOINCREMENT, request_id VARCHAR(36) NOT NULL, '
    'position INTEGER NOT NULL, user_id INTEGER NOT NULL, channel VARCHAR(32) NOT NULL, '
    'movie_id INTEGER, tmdb_id INTEGER, clicked_at DATETIME NOT NULL)',
)


def _fixture_engine():
    engine = create_engine('sqlite://')

    def load(value):
        if value is None:
            return None
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value

    def extract(raw, path):
        value = load(raw)
        return json.dumps(value.get(path[2:])) if isinstance(value, dict) else None

    def json_type(raw):
        value = load(raw)
        return ('BOOLEAN' if type(value) is bool else 'INTEGER' if type(value) is int
                else 'DOUBLE' if type(value) is float else 'STRING'
                if isinstance(value, str) else 'NULL')

    def json_unquote(value):
        value = load(value)
        return ('true' if value is True else 'false' if value is False
                else str(value)) if value is not None else None

    @event.listens_for(engine, 'connect')
    def configure(conn, _):
        conn.create_function('json_extract', 2, extract)
        conn.create_function('json_type', 1, json_type)
        conn.create_function('json_unquote', 1, json_unquote)
        conn.create_function('floor', 1, lambda value: math.floor(value) if value is not None else None)

    definitions = {
        'movies': [('movie_id', Integer), ('title', String), ('catalog_status', String),
                   ('release_year', Integer), ('overview', String)],
        'genres': [('genre_id', Integer), ('name_zh', String)],
        'movie_genres': [('movie_id', Integer), ('genre_id', Integer)],
        'movie_source_records': [('id', Integer), ('movie_id', Integer), ('source', String),
                                 ('metadata_json', JSON), ('fetched_at', DateTime)],
        'user_movie_events': [('seq', Integer), ('user_id', Integer), ('movie_id', Integer),
                              ('payload', JSON), ('created_at', DateTime)],
        'user_movie_states': [('user_id', Integer), ('movie_id', Integer), ('state', JSON)],
        'auth_users': [('id', Integer), ('username', String), ('status', String),
                       ('created_at', DateTime)],
        'usage': [('model', String), ('created_at', DateTime), ('status', String), ('tokens', JSON)],
    }
    md = MetaData()
    for name, columns in definitions.items():
        Table(name, md, *(Column(key, kind) for key, kind in columns))
    md.create_all(engine)
    return engine, md


def _rows(engine, md, name, rows):
    with engine.begin() as connection:
        connection.execute(insert(md.tables[name]), rows)


def _feedback(engine):
    with engine.begin() as connection:
        for statement in FEEDBACK_DDL:
            connection.execute(text(statement))
    return recommendation_feedback.tables(None)


def _repository(md):
    return SimpleNamespace(tables={'events': md.tables['user_movie_events'],
                                   'states': md.tables['user_movie_states']})


def _app(engine, repository, monkeypatch, identity='admin'):
    app = Flask(__name__)
    admin_user = SimpleNamespace(id=1, username='admin', username_normalized='admin')

    class DB:
        def get(self, *_):
            return admin_user if identity == 'admin' else SimpleNamespace(
                id=2, username='viewer', username_normalized='viewer')

    class Factory:
        @contextmanager
        def begin(self):
            yield DB()

    imp, clicks = recommendation_feedback.tables(None)
    app.config['ADMIN_USERS'] = ['admin']
    app.config['M6_ACTIVITY_SCHEMA'] = 'mi2_m6_test_schema'
    app.extensions.update(auth_db=Factory(), auth_engine=engine, m6_behavior=repository)
    monkeypatch.setattr(admin_api, 'session_row',
                        lambda db: None if identity == 'anonymous'
                        else SimpleNamespace(user_id=admin_user.id))
    monkeypatch.setattr(dashboard, 'feedback_tables', lambda _app: (imp, clicks))
    admin_api.install(app)
    return app


def _movie(engine, md, movie_id, title='影片', year=2015, status='active'):
    _rows(engine, md, 'movies', [dict(movie_id=movie_id, title=title, catalog_status=status,
                                      release_year=year, overview='')])


def _event(md, seq, user, movie, kind, action='set', value=None, at=NOW):
    return dict(seq=seq, user_id=user, movie_id=movie,
                payload={'kind': kind, 'action': action, 'value': value}, created_at=at)


# --- HTTP surface: auth, parameter guards, contract -------------------------

NEW_ENDPOINTS = ('users/analytics', 'interest-match', 'recommendation/analytics',
                 'ai/analytics', 'metrics/definitions', 'events', 'health')


@pytest.mark.parametrize('identity,status', [('anonymous', 401), ('viewer', 403)])
def test_new_admin_endpoints_require_admin_session(monkeypatch, identity, status):
    engine, md = _fixture_engine()
    client = _app(engine, _repository(md), monkeypatch, identity).test_client()
    for path in NEW_ENDPOINTS:
        response = client.get('/api/admin/' + path + '?admin=1')
        assert response.status_code == status, path
        assert response.json['data'] is None


def test_days_kind_and_dimension_parameters_are_validated(monkeypatch):
    engine, md = _fixture_engine()
    client = _app(engine, _repository(md), monkeypatch).test_client()
    for path in ('users/analytics', 'interest-match', 'recommendation/analytics', 'ai/analytics'):
        for query in ('days=0', 'days=1', 'days=7&days=30', 'days=7;DROP', 'extra=1'):
            assert client.get('/api/admin/%s?%s' % (path, query)).status_code == 400, (path, query)
    for query in ('days=7&kind=login', 'kind=favorite&kind=want'):
        assert client.get('/api/admin/users/analytics?' + query).status_code == 400
    for query in ('dimension=actor', 'dimension=genre&dimension=decade'):
        assert client.get('/api/admin/interest-match?' + query).status_code == 400
    for query in ('limit=0', 'limit=101', 'offset=-1', 'offset=abc', 'movie_id=0',
                  'from=2026-13-01', 'from=2026-11-18&to=2026-11-01', 'kind=login',
                  'sort=payload'):
        assert client.get('/api/admin/events?' + query).status_code == 400, query
    assert dashboard.parse_kind_days(MultiDict()) == (30, None)
    assert dashboard.parse_events_args(MultiDict()) == {
        'limit': 60, 'offset': 0, 'start': None, 'end': None, 'kind': None, 'movie_id': None}


def test_metrics_definitions_are_read_only_and_versioned(monkeypatch):
    engine, md = _fixture_engine()
    response = _app(engine, _repository(md), monkeypatch).test_client().get('/api/admin/metrics/definitions')
    assert response.status_code == 200
    data = response.json['data']
    assert data['version'] == 'admin-metrics-v2.0'
    assert data['timezone'] == 'UTC' and data['windows'] == [7, 30, 90]
    constants = data['constants']
    assert constants['tmdb_min_votes'] == 50
    assert constants['exposure_retention_days'] == 180
    assert constants['high_rating_threshold'] == 4
    keys = {item['key'] for item in data['definitions']}
    assert {'movies.active', 'recommendation.ctr', 'ai.tokens', 'interest.match'} <= keys
    assert all(item['status'] in {'available', 'not_instrumented'} for item in data['definitions'])
    assert 'password' not in json.dumps(data).lower()


def test_database_failure_is_reported_without_leaking(monkeypatch):
    engine, md = _fixture_engine()
    app = _app(engine, _repository(md), monkeypatch)

    def broken(*_args, **_kwargs):
        raise OperationalError('SELECT secret', {}, Exception('password=SHOULD_NOT_LEAK'))

    monkeypatch.setattr(dashboard, 'users_analytics', broken)
    response = app.test_client().get('/api/admin/users/analytics?days=7')
    assert response.status_code == 503
    assert response.json == {'code': 'DATA_UNAVAILABLE', 'data': None}
    assert 'SHOULD_NOT_LEAK' not in response.get_data(as_text=True)


# --- Page 06: users analytics ----------------------------------------------

def _users_fixture():
    engine, md = _fixture_engine()
    for movie_id, title, year in ((1, 'A', 2015), (2, 'B', 1999), (3, 'C', None)):
        _movie(engine, md, movie_id, title, year)
    _movie(engine, md, 9, 'Hidden', 2015, status='hidden')
    _rows(engine, md, 'genres', [dict(genre_id=1, name_zh='剧情'), dict(genre_id=2, name_zh='动画')])
    _rows(engine, md, 'movie_genres', [dict(movie_id=1, genre_id=1),
                                       dict(movie_id=2, genre_id=1), dict(movie_id=2, genre_id=2)])
    _rows(engine, md, 'movie_source_records', [
        dict(id=1, movie_id=1, source='tmdb', fetched_at=NOW, metadata_json={
            'production_countries': [{'iso_3166_1': 'US'}]}),
        dict(id=2, movie_id=2, source='legacy_movies2', fetched_at=NOW, metadata_json={
            'production_countries': [{'iso_3166_1': 'CN'}]}),
    ])
    _rows(engine, md, 'auth_users', [
        dict(id=1, username='u1', status='active', created_at=START + timedelta(days=1)),
        dict(id=2, username='u2', status='active', created_at=START - timedelta(days=5)),
        dict(id=3, username='u3', status='disabled', created_at=NOW - timedelta(days=1)),
        dict(id=4, username='u4', status='active', created_at=START + timedelta(days=3)),
    ])
    _rows(engine, md, 'user_movie_events', [
        _event(md, 1, 1, 1, 'favorite', at=START + timedelta(hours=1)),
        _event(md, 2, 1, 1, 'favorite', 'undo', at=START + timedelta(hours=2)),
        _event(md, 3, 2, 2, 'want', at=START + timedelta(days=1)),
        _event(md, 4, 2, 2, 'rating', value=5, at=START + timedelta(days=1, hours=1)),
        _event(md, 5, 1, 1, 'watched', at=NOW - timedelta(hours=1)),
    ])
    _rows(engine, md, 'user_movie_states', [
        dict(user_id=1, movie_id=1, state={'favorite': True, 'watched': True, 'rating': 5}),
        dict(user_id=2, movie_id=1, state={'favorite': True}),
        dict(user_id=2, movie_id=2, state={'want': True, 'rating': 4}),
        dict(user_id=4, movie_id=2, state={'favorite': True}),
        dict(user_id=2, movie_id=3, state={'rating': '5'}),  # string rating ignored
        dict(user_id=3, movie_id=3, state={'favorite': 'true'}),  # non-boolean JSON ignored
        dict(user_id=1, movie_id=9, state={'favorite': True}),  # not active
    ])
    return engine, md


def test_users_events_accounts_and_current_states_are_counted_separately():
    engine, md = _users_fixture()
    with engine.connect() as connection:
        data = dashboard.users_analytics(connection, _repository(md), START, END, now=NOW)
    assert data['status'] == 'available'
    assert data['registered'] == {'total': 4, 'active': 3, 'new_in_window': 3}
    assert data['sample_size']['events'] == 5
    assert data['sample_size']['active_accounts'] == 2
    assert len(data['daily']['registrations']) == 7
    assert sum(row['count'] for row in data['daily']['registrations']) == 3
    assert sum(row['count'] for row in data['daily']['events']) == 5
    assert [row['count'] for row in data['daily']['active_accounts']][0] == 1
    assert data['current_states'] == {'favorite': 3, 'want': 1, 'watched': 1,
                                      'rating': 2, 'not_interested': 0, 'status': 'available'}
    assert data['avg_events_per_active_account']['value'] == 2.5
    assert data['boards']['most_events'][0] == {'movie_id': 1, 'title': 'A', 'count': 3}
    assert data['boards']['most_favorited'] == [{'movie_id': 1, 'title': 'A', 'count': 2},
                                                {'movie_id': 2, 'title': 'B', 'count': 1}]
    assert data['boards']['most_wanted'] == [{'movie_id': 2, 'title': 'B', 'count': 1}]

    with engine.connect() as connection:
        filtered = dashboard.users_analytics(connection, _repository(md), START, END,
                                             kind='favorite', now=NOW)
    assert filtered['kind_filter'] == 'favorite'
    assert filtered['sample_size']['events'] == 2
    assert filtered['avg_events_per_active_account']['value'] == 2
    # Current states are a snapshot and are not filtered by event kind.
    assert filtered['current_states']['favorite'] == 3


def test_users_preference_splits_user_and_pair_denominators():
    engine, md = _users_fixture()
    with engine.connect() as connection:
        data = dashboard.users_analytics(connection, _repository(md), START, END, now=NOW)
    preference = data['preference']
    assert preference['status'] == 'available'
    favorite = preference['signals']['favorite']
    assert favorite['status'] == 'available'
    assert favorite['sample_size'] == {'users': 3, 'pairs': 3}
    by_user = {row['name']: row for row in favorite['by_user']['genre']}
    by_pair = {row['name']: row for row in favorite['by_pair']['genre']}
    assert by_user['剧情']['count'] == 3 and by_user['剧情']['share'] == 1.0
    assert by_user['动画']['count'] == 1 and by_user['动画']['share'] == round(1 / 3, 6)
    assert by_pair['剧情']['count'] == 3 and by_pair['动画']['count'] == 1
    decade = {row['name']: row for row in favorite['by_pair']['decade']}
    assert decade['2010年代']['count'] == 2 and decade['1990年代']['count'] == 1
    country = {row['name']: row for row in favorite['by_pair']['country']}
    assert country['US']['count'] == 2 and country['CN']['count'] == 1
    high = preference['signals']['high_rating']
    assert high['status'] == 'suppressed'  # only user 1 and user 2 have numeric ratings >= 4
    assert high['by_user']['genre'] == []


def test_small_sample_preference_is_suppressed_not_fabricated():
    engine, md = _fixture_engine()
    _movie(engine, md, 1)
    _rows(engine, md, 'auth_users', [dict(id=1, username='u1', status='active', created_at=START)])
    _rows(engine, md, 'user_movie_states', [dict(user_id=1, movie_id=1, state={'want': True})])
    with engine.connect() as connection:
        data = dashboard.users_analytics(connection, _repository(md), START, END, now=NOW)
    assert data['preference']['status'] == 'suppressed'
    assert data['preference']['signals']['want']['status'] == 'suppressed'
    assert data['preference']['signals']['want']['by_pair'] == {
        'genre': [], 'decade': [], 'country': []}


def test_daily_zero_fill_only_after_successful_query():
    engine, md = _fixture_engine()
    with engine.connect() as connection:
        data = dashboard.users_analytics(connection, _repository(md), START, END, now=NOW)
    assert data['status'] == 'available'
    assert all(row['count'] == 0 for row in data['daily']['events'])
    assert len(data['daily']['events']) == 7
    assert data['current_states']['favorite'] == 0
    assert data['avg_events_per_active_account']['value'] is None


# --- Page 07: interest match ----------------------------------------------

def _interest_fixture():
    engine, md = _fixture_engine()
    for movie_id, year in ((1, 2015), (2, 2016), (3, 1995)):
        _movie(engine, md, movie_id, '影片%d' % movie_id, year)
    _rows(engine, md, 'genres', [dict(genre_id=1, name_zh='剧情'), dict(genre_id=2, name_zh='动画')])
    _rows(engine, md, 'movie_genres', [dict(movie_id=1, genre_id=1),
                                       dict(movie_id=2, genre_id=1), dict(movie_id=2, genre_id=2)])
    events = [_event(md, index + 1, 1 + index % 3, movie, kind, value=value,
                     at=START + timedelta(hours=index))
              for index, (movie, kind, value) in enumerate([
                  (1, 'favorite', None), (1, 'want', None), (2, 'rating', 5),
                  (1, 'favorite', None), (2, 'want', None), (2, 'rating', 4),
                  (1, 'rating', 3),  # below the high-rating threshold
              ])]
    _rows(engine, md, 'user_movie_events', events)
    imp, clicks = _feedback(engine)
    rows = [dict(request_id='req%d' % index, user_id=1, channel='daily', period=None,
                 board_version='visible-v2', context='dashboard', position=index + 1,
                 movie_id=1 if index % 2 == 0 else 2, tmdb_id=None,
                 shown_at=START + timedelta(days=1, minutes=index))
            for index in range(12)]
    with engine.begin() as connection:
        connection.execute(insert(imp), rows)
        connection.execute(insert(clicks), [
            dict(request_id='req2', position=3, user_id=1, channel='daily', movie_id=2,
                 tmdb_id=None, clicked_at=START + timedelta(days=1, minutes=3))])
    return engine, md, (imp, clicks)


def test_interest_match_reports_four_series_and_thresholds():
    engine, md, feedback = _interest_fixture()
    with engine.connect() as connection:
        data = dashboard.interest_match(connection, _repository(md), feedback, START, END,
                                        'genre', now=NOW)
    assert data['status'] == 'available'
    assert data['supply'] == {'active_movies': 3, 'classified_movies': 2, 'unknown_movies': 1}
    assert data['interest']['signal_counts'] == {'favorite': 1, 'want': 2, 'high_rating': 1}
    assert data['interest']['any_positive_pairs'] == 4
    assert data['exposure']['attributable_impressions'] == 12
    assert data['exposure']['clicks'] == 1
    categories = {item['key']: item for item in data['categories']}
    assert set(categories) == {'剧情', '动画'}
    drama = categories['剧情']
    assert drama['supply'] == {'movies': 2, 'share': round(2 / 3, 6)}
    assert drama['interest']['favorite_pairs'] == 1
    assert drama['interest']['high_rating_pairs'] == 1
    assert drama['interest']['positive_pairs'] == 4
    assert drama['exposure']['impressions'] == 12
    assert drama['clicks'] == {'clicks': 1, 'ctr': round(1 / 12, 6)}
    assert drama['status'] == 'available'
    animation = categories['动画']
    assert animation['supply']['movies'] == 1 and animation['exposure']['impressions'] == 6
    assert animation['interest']['positive_pairs'] == 2
    assert animation['status'] == 'insufficient_sample'
    # The read model never emits a shortfall/verdict conclusion.
    assert '短缺' not in json.dumps(data['categories'], ensure_ascii=False)


def test_interest_match_insufficient_sample_across_dimensions():
    engine, md = _fixture_engine()
    _movie(engine, md, 1)
    _rows(engine, md, 'genres', [dict(genre_id=1, name_zh='剧情')])
    _rows(engine, md, 'movie_genres', [dict(movie_id=1, genre_id=1)])
    imp, clicks = _feedback(engine)
    with engine.connect() as connection:
        data = dashboard.interest_match(connection, _repository(md), (imp, clicks), START, END,
                                        'genre', now=NOW)
        country = dashboard.interest_match(connection, _repository(md), (imp, clicks), START, END,
                                           'country', now=NOW)
    assert data['status'] == 'insufficient_sample'
    assert all(item['status'] == 'insufficient_sample' for item in data['categories'])
    assert country['status'] == 'insufficient_sample'
    assert country['supply'] == {'active_movies': 1, 'classified_movies': 0, 'unknown_movies': 1}
    assert country['categories'] == [] and country['reason']
    assert 'tmdb_movie_countries/movie_source_records' in country['source']


def test_interest_match_country_dimension_aggregates_source_records():
    engine, md, feedback = _interest_fixture()
    _movie(engine, md, 9, 'Hidden', 2020, status='hidden')
    _rows(engine, md, 'movie_source_records', [
        dict(id=1, movie_id=1, source='tmdb', fetched_at=NOW, metadata_json={
            'production_countries': [{'iso_3166_1': 'US'}]}),
        dict(id=2, movie_id=2, source='legacy_movies2', fetched_at=NOW, metadata_json={
            'production_countries': [{'iso_3166_1': 'US'}, {'iso_3166_1': 'JP'}]}),
        dict(id=3, movie_id=9, source='tmdb', fetched_at=NOW, metadata_json={
            'production_countries': [{'iso_3166_1': 'FR'}]}),
    ])
    with engine.connect() as connection:
        data = dashboard.interest_match(connection, _repository(md), feedback, START, END,
                                        'country', now=NOW)
    assert data['dimension'] == 'country'
    assert data['status'] == 'available'
    assert 'tmdb_movie_countries/movie_source_records' in data['source']
    assert data['supply'] == {'active_movies': 3, 'classified_movies': 2, 'unknown_movies': 1}
    assert data['interest']['any_positive_pairs'] == 4
    assert data['exposure']['attributable_impressions'] == 12
    assert data['exposure']['clicks'] == 1
    categories = {item['key']: item for item in data['categories']}
    assert set(categories) == {'US', 'JP'}
    assert categories['US']['supply'] == {'movies': 2, 'share': round(2 / 3, 6)}
    assert categories['US']['interest']['positive_pairs'] == 4
    assert categories['US']['exposure']['impressions'] == 12
    assert categories['US']['clicks'] == {'clicks': 1, 'ctr': round(1 / 12, 6)}
    assert categories['US']['status'] == 'available'
    assert categories['JP']['supply']['movies'] == 1
    assert categories['JP']['interest']['positive_pairs'] == 2
    assert categories['JP']['exposure']['impressions'] == 6
    assert categories['JP']['status'] == 'insufficient_sample'


def test_interest_match_country_prefers_tmdb_movie_countries_with_fallback():
    engine, md, feedback = _interest_fixture()
    _movie(engine, md, 9, 'Hidden', 2020, status='hidden')
    with engine.begin() as connection:
        connection.execute(text(
            'CREATE TABLE tmdb_movie_countries ('
            'movie_id INTEGER NOT NULL, country_iso2 VARCHAR(2) NOT NULL, '
            'country_name VARCHAR(255), PRIMARY KEY (movie_id, country_iso2))'
        ))
        connection.execute(text(
            "INSERT INTO tmdb_movie_countries (movie_id, country_iso2, country_name) VALUES "
            "(1, 'cn', 'China'), (1, 'HK', 'Hong Kong'), (9, 'FR', 'France')"
        ))
    _rows(engine, md, 'movie_source_records', [
        dict(id=1, movie_id=1, source='tmdb', fetched_at=NOW, metadata_json={
            'production_countries': [{'iso_3166_1': 'ZZ'}]}),
        dict(id=2, movie_id=2, source='legacy_movies2', fetched_at=NOW, metadata_json={
            'production_countries': [{'iso_3166_1': 'JP'}]}),
    ])
    with engine.connect() as connection:
        data = dashboard.interest_match(connection, _repository(md), feedback, START, END,
                                        'country', now=NOW)
    assert data['status'] == 'available'
    categories = {item['key']: item for item in data['categories']}
    assert set(categories) == {'CN', 'HK', 'JP'}
    assert categories['CN']['supply']['movies'] == 1
    assert categories['CN']['exposure']['impressions'] == 6
    assert categories['JP']['supply']['movies'] == 1
    assert categories['JP']['exposure']['impressions'] == 6
    assert 'ZZ' not in categories and 'FR' not in categories


def test_interest_match_decade_dimension_uses_release_year():
    engine, md, feedback = _interest_fixture()
    with engine.connect() as connection:
        data = dashboard.interest_match(connection, _repository(md), feedback, START, END,
                                        'decade', now=NOW)
    assert data['dimension'] == 'decade'
    names = {item['name'] for item in data['categories']}
    assert names == {'2010年代', '1990年代'}
    assert data['supply']['unknown_movies'] == 0


# --- Page 08: recommendation analytics ------------------------------------

def _recommendation_fixture():
    engine, md = _fixture_engine()
    imp, clicks = _feedback(engine)
    rows = [
        # Effective cohort (visible-v2) with one clicked item.
        dict(request_id='r1', user_id=1, channel='daily', period=None, board_version='visible-v2',
             context='home', position=1, movie_id=1, tmdb_id=None, shown_at=START + timedelta(hours=1)),
        dict(request_id='r1', user_id=1, channel='daily', period=None, board_version='visible-v2',
             context='home', position=2, movie_id=2, tmdb_id=None, shown_at=START + timedelta(hours=1)),
        # Legacy cohort must not enter the effective denominator.
        dict(request_id='r2', user_id=2, channel='legacy', period=None, board_version='visible-v1',
             context='home', position=1, movie_id=1, tmdb_id=None, shown_at=START + timedelta(hours=2)),
        # Another effective channel.
        dict(request_id='r3', user_id=2, channel='similar', period=None, board_version='visible-v2',
             context='detail', position=1, movie_id=3, tmdb_id=None, shown_at=START + timedelta(hours=3)),
    ]
    with engine.begin() as connection:
        connection.execute(insert(imp), rows)
        connection.execute(insert(clicks), [
            dict(request_id='r1', position=2, user_id=1, channel='daily', movie_id=2,
                 tmdb_id=None, clicked_at=START + timedelta(hours=1, minutes=5)),
            dict(request_id='r2', position=1, user_id=2, channel='legacy', movie_id=1,
                 tmdb_id=None, clicked_at=START + timedelta(hours=2, minutes=5)),
            dict(request_id='r3', position=1, user_id=2, channel='similar', movie_id=3,
                 tmdb_id=None, clicked_at=START + timedelta(hours=3, minutes=5)),
        ])
    return engine, (imp, clicks)


def test_recommendation_analytics_cohort_attribution_and_unavailable_blocks():
    engine, feedback = _recommendation_fixture()
    with engine.connect() as connection:
        data = dashboard.recommendation_analytics(connection, feedback, START, END, now=NOW)
    assert data['status'] == 'available'
    assert data['totals']['impressions'] == 3
    assert data['totals']['clicks'] == 2
    assert data['totals']['channels'] == 2
    assert data['totals']['accounts'] == 2
    assert data['totals']['unattributed_clicks'] == 1
    channels = {item['channel']: item for item in data['channels']}
    assert 'legacy' not in channels  # legacy versions are not effective exposure
    assert channels['daily']['status'] == 'insufficient_sample'
    assert len(data['daily']) == 7
    assert data['funnel']['status'] == 'unavailable' and data['funnel']['reason']
    assert data['funnel']['available_steps'] == ['impression', 'click']
    assert data['offline_evaluation']['status'] == 'not_evaluable'
    assert 'recall_at_k' not in json.dumps(data['offline_evaluation'])
    assert 'ndcg_at_k' not in json.dumps(data['offline_evaluation'])


def test_recommendation_analytics_degrades_when_tables_are_missing():
    engine, _md = _fixture_engine()
    missing = recommendation_feedback.tables(None)
    with engine.connect() as connection:
        data = dashboard.recommendation_analytics(connection, missing, START, END, now=NOW)
        block = dashboard.recommendation_snapshot(connection, missing, START, END)
    assert data['status'] == 'unavailable' and data['totals']['impressions'] is None
    assert block['status'] == 'unavailable' and block['impressions'] is None
    assert block['reason'] and data['reason']


def test_dashboard_system_block_reports_generation_and_ai_failures():
    generated = {'catalog': 'a', 'behavior': 'b', 'users': 'c', 'ai': 'd', 'recommendation': 'e'}
    ai = {'status': 'AVAILABLE', 'calls': 5,
          'statuses': [{'name': 'SUCCEEDED', 'count': 3}, {'name': 'FAILED', 'count': 1},
                       {'name': 'ERROR', 'count': 1}]}
    block = dashboard.system_block(generated, ai, END)
    assert block['ai_failures'] == 2 and block['ai_failure_status'] == 'available'
    assert block['generated_at'] == generated
    missing = dashboard.system_block(generated, {'status': 'UNAVAILABLE', 'calls': None}, END)
    assert missing['ai_failures'] is None and missing['ai_failure_status'] == 'not_instrumented'


# --- Page 09: AI analytics ------------------------------------------------

def _ai_fixture():
    engine, md = _fixture_engine()
    rows = [
        dict(model='deepseek-chat', status='SUCCEEDED', created_at=NOW - timedelta(hours=1),
             tokens={'kind': 'BYOK_AGENT', 'paid_request': True, 'input_tokens': 100,
                     'output_tokens': 20, 'cached_input_tokens': 40}),
        dict(model='deepseek-chat', status='FAILED', created_at=NOW - timedelta(hours=2),
             tokens={'kind': 'BYOK_AGENT', 'paid_request': True}),
        dict(model='deepseek-chat', status='CANCELLED_UNKNOWN', created_at=NOW - timedelta(hours=3),
             tokens={'kind': 'BYOK_AGENT', 'paid_request': True, 'input_tokens': 10,
                     'output_tokens': None}),
        dict(model='mock-agent', status='SUCCEEDED', created_at=NOW - timedelta(hours=4),
             tokens={'kind': 'MOCK', 'paid_request': False, 'input_tokens': 8, 'output_tokens': 2}),
        dict(model='legacy-model', status='VERIFIED', created_at=NOW - timedelta(hours=5),
             tokens={'input_tokens': 5, 'output_tokens': 1}),
        dict(model='deepseek-chat', status='NOT_DISPATCHED', created_at=NOW,
             tokens={'kind': 'BYOK_AGENT', 'paid_request': True, 'input_tokens': 99999}),
    ]
    _rows(engine, md, 'usage', rows)
    return engine, md


def test_ai_analytics_provenance_status_and_unknown_tokens():
    engine, md = _ai_fixture()
    app = SimpleNamespace(extensions={'m7_engine': engine, 'm7_ledger': SimpleNamespace(md=md)})
    data = dashboard.ai_analytics(app, START, END, now=NOW)
    assert data['status'] == 'available'
    assert data['totals']['calls'] == 5  # NOT_DISPATCHED excluded
    assert (data['totals']['success'], data['totals']['failed'],
            data['totals']['interrupted'], data['totals']['unknown'],
            data['totals']['timeout']) == (3, 1, 1, 0, 0)
    provenance = {item['source']: item for item in data['provenance']}
    assert provenance['REAL']['calls'] == 3
    assert provenance['MOCK']['calls'] == 1
    assert provenance['UNKNOWN']['calls'] == 1
    # Known subtotals stay explicit; unknown usage is counted separately, never as zero.
    assert data['tokens']['known_input_tokens'] == 123
    assert data['tokens']['known_output_tokens'] == 23
    assert data['tokens']['known_total_tokens'] == 146
    assert data['tokens']['unknown_usage_calls'] == 2
    assert data['tokens']['status'] == 'partial'
    assert data['cached_input']['status'] == 'available'
    assert data['cached_input']['tokens'] == 40 and data['cached_input']['reported_calls'] == 1
    assert data['errors']['status'] == 'not_instrumented'
    assert data['latency']['status'] == 'not_instrumented'
    assert data['agent']['status'] == 'not_instrumented'
    models = {item['model']: item for item in data['models']}
    assert models['mock-agent']['source'] == 'MOCK'
    assert models['deepseek-chat']['calls'] == 3
    assert models['deepseek-chat']['success_rate'] == round(1 / 3, 6)
    assert len(data['daily']) == 7
    payload = json.dumps(data)
    assert 'PROMPT' not in payload and 'api_key' not in payload.lower()


def test_ai_analytics_without_ledger_is_unavailable_not_zero():
    data = dashboard.ai_analytics(SimpleNamespace(extensions={}), START, END, now=NOW)
    assert data['status'] == 'unavailable'
    assert data['totals']['calls'] is None and data['tokens']['known_input_tokens'] is None
    assert data['errors']['status'] == 'not_instrumented'


def test_ai_analytics_unknown_tokens_are_null_not_zero():
    engine, md = _fixture_engine()
    _rows(engine, md, 'usage', [
        dict(model='deepseek-chat', status='UNKNOWN', created_at=NOW - timedelta(hours=1),
             tokens={'kind': 'BYOK_AGENT', 'paid_request': True}),
        dict(model='other-model', status='SUCCEEDED', created_at=NOW - timedelta(hours=2),
             tokens=None),
    ])
    app = SimpleNamespace(extensions={'m7_engine': engine, 'm7_ledger': SimpleNamespace(md=md)})
    data = dashboard.ai_analytics(app, START, END, now=NOW)
    assert data['totals']['calls'] == 2
    assert data['tokens']['known_input_tokens'] is None  # nothing reported, do not fabricate 0
    assert data['tokens']['known_output_tokens'] is None
    assert data['tokens']['unknown_usage_calls'] == 2
    assert data['tokens']['status'] == 'partial'
    assert data['cached_input']['status'] == 'not_instrumented'
    assert all(model['known_input_tokens'] is None for model in data['models'])
    no_calls = dashboard.ai_analytics(
        SimpleNamespace(extensions={'m7_engine': engine, 'm7_ledger': SimpleNamespace(md=md)}),
        NOW + timedelta(days=2), NOW + timedelta(days=3), now=NOW)
    assert no_calls['tokens']['status'] == 'not_called'
    assert no_calls['tokens']['known_input_tokens'] == 0  # confirmed zero calls, not unknown


def test_dashboard_recommendation_block_reports_window_kpis():
    engine, feedback = _recommendation_fixture()
    with engine.connect() as connection:
        block = dashboard.recommendation_snapshot(connection, feedback, START, END)
    assert block['status'] == 'available'
    assert block['impressions'] == 3 and block['clicks'] == 2
    assert block['ctr'] == round(2 / 3, 6)
    assert block['accounts'] == 2 and block['unattributed_clicks'] == 1
    assert block['sample_size'] == 3 and block['reason'] is None
    channels = {item['channel']: item for item in block['channels']}
    assert channels['daily']['clicks'] == 1 and channels['similar']['clicks'] == 1
    assert len(block['daily']) == 7


# --- Page 11: health ------------------------------------------------------

def test_recommendation_tracking_health_states():
    engine, _md = _fixture_engine()
    imp, clicks = _feedback(engine)
    with engine.connect() as connection:
        empty = dashboard.recommendation_tracking_health(connection, (imp, clicks), NOW)
    assert empty['status'] == 'not_instrumented' and empty['last_impression_at'] is None
    rows = [dict(request_id='h%d' % index, user_id=1, channel='daily', period=None,
                 board_version='visible-v2', context='home', position=index + 1,
                 movie_id=1, tmdb_id=None, shown_at=NOW - timedelta(hours=1))
            for index in range(10)]
    with engine.begin() as connection:
        connection.execute(insert(imp), rows)
    with engine.connect() as connection:
        normal = dashboard.recommendation_tracking_health(connection, (imp, clicks), NOW)
    assert normal['status'] == 'normal' and normal['impressions_7d'] == 10
    assert normal['hours_since_last_impression'] == 1.0
    assert normal['impressions_24h'] == 10
    with engine.connect() as connection:
        gap = dashboard.recommendation_tracking_health(connection, (imp, clicks), NOW + timedelta(days=10))
    assert gap['status'] == 'abnormal'  # 7-day window empty although history exists
    assert gap['impressions_7d'] == 0 and gap['reason']


def test_ai_failure_health_observing_and_abnormal():
    engine, md = _fixture_engine()
    app = SimpleNamespace(extensions={'m7_engine': engine, 'm7_ledger': SimpleNamespace(md=md)})
    _rows(engine, md, 'usage', [dict(model='m', status='FAILED', created_at=NOW - timedelta(hours=1),
                                     tokens={})])
    low = dashboard.ai_failure_health(app, NOW)
    assert low['status'] == 'observing' and low['calls'] == 1 and low['failure_rate'] == 1.0
    rows = [dict(model='m', status='FAILED', created_at=NOW - timedelta(hours=index + 2), tokens={})
            for index in range(5)]
    rows += [dict(model='m', status='SUCCEEDED', created_at=NOW - timedelta(hours=index + 8), tokens={})
             for index in range(5)]
    _rows(engine, md, 'usage', rows)
    surge = dashboard.ai_failure_health(app, NOW)
    assert surge['status'] == 'abnormal' and surge['calls'] == 11
    assert surge['failure_rate'] == round(6 / 11, 6)
    healthy = dashboard.ai_failure_health(SimpleNamespace(extensions={}), NOW)
    assert healthy['status'] == 'not_instrumented' and healthy['calls'] is None


def test_health_report_combines_blocks(monkeypatch):
    engine, md = _fixture_engine()
    imp, clicks = _feedback(engine)
    app = SimpleNamespace(config={'M6_ACTIVITY_SCHEMA': 'mi2_m6_test_schema'},
                          extensions={'auth_engine': engine, 'm7_engine': engine,
                                      'm7_ledger': SimpleNamespace(md=md)})
    monkeypatch.setattr(dashboard, 'feedback_tables', lambda _app: (imp, clicks))
    data = dashboard.health_report(app, NOW)
    assert set(data) == {'stats', 'recommendation_tracking', 'ai_failures'}
    assert data['stats']['status'] == 'available'
    assert set(data['stats']['last_success']) == set(dashboard.GENERATION_BLOCKS)
    assert data['recommendation_tracking']['status'] == 'not_instrumented'
    assert data['ai_failures']['status'] == 'observing'  # zero calls in the window


def test_health_endpoint_keeps_legacy_fields(monkeypatch):
    engine, md = _fixture_engine()
    _movie(engine, md, 1)
    app = _app(engine, _repository(md), monkeypatch)
    response = app.test_client().get('/api/admin/health')
    assert response.status_code == 200
    data = response.json['data']
    for key in ('db_time', 'movies', 'm7_configured', 'admin', 'config',
                'stats', 'recommendation_tracking', 'ai_failures'):
        assert key in data
    assert data['movies'] == 1
    assert data['stats']['last_success']['health'] is not None


# --- Page 12: events feed -------------------------------------------------

def test_events_page_pagination_filters_and_no_payload():
    engine, md = _fixture_engine()
    _movie(engine, md, 1, 'A')
    _movie(engine, md, 2, 'B')
    _rows(engine, md, 'auth_users', [dict(id=1, username='deepseek', status='active', created_at=START)])
    _rows(engine, md, 'user_movie_events', [
        _event(md, index + 1, 1, 1 if index % 2 == 0 else 2,
               'favorite' if index % 2 == 0 else 'want',
               at=START + timedelta(hours=index)) for index in range(5)])
    repository = _repository(md)
    with engine.connect() as connection:
        first = dashboard.events_page(connection, repository,
                                      dashboard.parse_events_args(MultiDict({'limit': '3'})), now=NOW)
        second = dashboard.events_page(connection, repository,
                                       dashboard.parse_events_args(MultiDict({'limit': '3', 'offset': '3'})),
                                       now=NOW)
        filtered = dashboard.events_page(connection, repository,
                                         dashboard.parse_events_args(MultiDict({'kind': 'want'})),
                                         now=NOW)
        by_movie = dashboard.events_page(connection, repository,
                                         dashboard.parse_events_args(MultiDict({'movie_id': '1'})),
                                         now=NOW)
        ranged = dashboard.events_page(connection, repository,
                                       dashboard.parse_events_args(MultiDict({
                                           'from': '2026-11-12T02:00:00Z', 'to': '2026-11-12T05:00:00Z'})),
                                       now=NOW)
    assert first['total'] == 5 and first['has_more'] is True and len(first['items']) == 3
    assert second['offset'] == 3 and len(second['items']) == 2 and second['has_more'] is False
    assert filtered['total'] == 2 and all(item['kind'] == 'want' for item in filtered['items'])
    assert by_movie['total'] == 3 and all(item['movie_id'] == 1 for item in by_movie['items'])
    assert ranged['total'] == 3
    item = first['items'][0]
    assert set(item) == {'at', 'user', 'title', 'movie_id', 'kind', 'action'}
    assert 'payload' not in json.dumps(first['items'])


# --- Regression guard: never sort on large JSON columns -------------------

def test_no_big_json_ordering_or_window_functions():
    engine, md, feedback = _interest_fixture()
    statements = []

    @event.listens_for(engine, 'before_cursor_execute')
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with engine.connect() as connection:
        dashboard.users_analytics(connection, _repository(md), START, END, now=NOW)
        dashboard.interest_match(connection, _repository(md), feedback, START, END, 'genre', now=NOW)
        dashboard.recommendation_analytics(connection, feedback, START, END, now=NOW)
    assert statements
    for statement in statements:
        upper = statement.upper()
        assert 'ROW_NUMBER' not in upper
        assert 'JSON_TABLE' not in upper
        if 'ORDER BY' in upper:
            tail = upper.split('ORDER BY', 1)[1]
            assert 'METADATA_JSON' not in tail and 'PAYLOAD' not in tail


def test_generation_registry_rejects_unknown_blocks():
    with pytest.raises(ValueError):
        dashboard.record_generation('unknown-block')
    dashboard.record_generation('users_analytics', NOW)
    state = dashboard.generation_state(NOW)
    assert state['last_success']['users_analytics'] == '2026-11-18T12:00:00Z'
    assert state['definition'] and state['process_started_at'].endswith('Z')