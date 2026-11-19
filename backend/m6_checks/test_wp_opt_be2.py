"""WP-OPT-BE2 regression tests: admin reads, cancel throttling and feedback math.

These are offline tests on purpose: no MySQL, .env or network access.
"""
import datetime
import inspect
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy import create_engine, insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import admin_api
import admin_dashboard
from analytics.api import RateLimiter
from assistant.agent_service import _CancelProbe, _cancel_requested
from catalog import m6_business
from catalog.recommendation_feedback import summarize, tables as feedback_tables


class _Result:
    def __init__(self, rows=(), mapping=None):
        self._rows = list(rows)
        self._mapping = mapping

    def all(self):
        return self._rows

    def mappings(self):
        return self

    def one(self):
        return self._mapping


class _DashboardConnection:
    """Answers the shapes users_analytics asks for without a real database."""

    def __init__(self, states):
        self.states = list(states)
        self.calls = []

    def scalar(self, statement):
        self.calls.append(('scalar', str(statement), {}))
        return 0

    def execute(self, statement):
        sql = str(statement)
        try:
            params = dict(statement.compile().params)
        except Exception:
            params = {}
        self.calls.append(('execute', sql, params))
        lowered = sql.lower()
        if ('from user_movie_states' in lowered and 'user_movie_states.state' in lowered
                and 'count(' not in lowered):
            return _Result(rows=self.states)
        if 'from auth_users' in lowered:
            return _Result(mapping={'total': 2, 'active': 2, 'new_in_window': 1})
        return _Result(rows=[])


def _users_analytics(states, kind=None):
    # events/states are owned by the auth/M6 metadata, not CatalogBase.
    from sqlalchemy import JSON, Column, DateTime, Integer, MetaData, Table
    md = MetaData()
    events = Table('user_movie_events', md,
                   Column('user_id', Integer), Column('movie_id', Integer),
                   Column('created_at', DateTime), Column('payload', JSON))
    states_table = Table('user_movie_states', md,
                         Column('user_id', Integer), Column('movie_id', Integer),
                         Column('state', JSON))
    repository = SimpleNamespace(tables={'events': events, 'states': states_table})
    connection = _DashboardConnection(states)
    start = datetime.datetime(2026, 1, 1)
    end = datetime.datetime(2026, 1, 8)
    result = admin_dashboard.users_analytics(connection, repository, start, end, kind=kind)
    return connection, result


def test_users_analytics_state_counts_and_pairs_share_one_scan():
    connection, result = _users_analytics([
        (1, 1, {'favorite': True, 'want': True, 'watched': True, 'rating': 4.5}),
        (2, 1, {'rating': 5}),
        (3, 2, {'favorite': False, 'rating': '5'}),
        (4, 3, {'rating': 5.004}),   # MySQL DECIMAL(6,2) rounds this to 5.00
        (5, 4, {'rating': 0.499}),   # and this to 0.50
        (6, 5, None),
    ])
    states = result['current_states']
    assert states == {'favorite': 1, 'want': 1, 'watched': 1, 'rating': 4,
                      'not_interested': 0, 'status': 'available'}
    assert list(states) == ['favorite', 'want', 'watched', 'rating', 'not_interested', 'status']

    scans = [call for call in connection.calls
             if call[0] == 'execute' and 'user_movie_states.state' in call[1].lower()
             and 'count(' not in call[1].lower()]
    assert len(scans) == 1
    count_scans = [call for call in connection.calls
                   if 'user_movie_states' in call[1].lower() and 'count(*)' in call[1].lower()
                   and 'group by' not in call[1].lower()]
    assert count_scans == []
    assert [call for call in connection.calls
            if call[0] == 'scalar' and 'user_movie_states' in call[1].lower()] == []
    # Counting rows is still the true sample size for the existing contract.
    assert result['sample_size']['state_pairs'] == 6


def test_users_analytics_most_events_follows_kind_filter():
    connection, result = _users_analytics([], kind='watched')
    boards = [call for call in connection.calls if 'order by count(*) desc' in call[1].lower()]
    assert len(boards) == 1
    sql, params = boards[0][1], boards[0][2]
    assert 'json_unquote' in sql.lower() and 'json_extract' in sql.lower()
    assert 'watched' in params.values()
    assert 'kind 过滤一致' in result['boards']['definition']


class _DecadeConnection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def execute(self, statement):
        self.calls.append(str(statement))
        return _Result(rows=self.rows)


def test_movie_decades_filters_by_ids_in_sql():
    connection = _DecadeConnection([(2, 1999), (3, None)])
    mapping = admin_dashboard._movie_decades(connection, {2, 3})
    assert mapping == {2: ['1990年代'], 3: ['年份未知']}
    assert len(connection.calls) == 1
    sql = connection.calls[0]
    assert 'movies.movie_id IN' in sql and 'movies.catalog_status' in sql

    empty = _DecadeConnection([])
    assert admin_dashboard._movie_decades(empty, set()) == {}
    assert empty.calls == []

    all_rows = _DecadeConnection([(1, 2001)])
    assert admin_dashboard._movie_decades(all_rows, None) == {1: ['2000年代']}
    assert 'movie_id IN' not in all_rows.calls[0]


class _HealthConnection:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def scalar(self, statement):
        sql = str(statement)
        self.calls.append(sql)
        if 'now' in sql:
            return datetime.datetime(2026, 1, 1, 0, 0, 0)
        return 3


class _HealthEngine:
    def __init__(self):
        self.connection = _HealthConnection()

    def connect(self):
        return self.connection


def test_admin_health_keeps_db_time_and_adds_utc(monkeypatch):
    app = Flask(__name__)
    app.config.update(ADMIN_USERS=['admin'], M6_SCHEMA='catalog', M6_ACTIVITY_SCHEMA=None)
    engine = _HealthEngine()
    app.extensions['auth_engine'] = engine
    app.extensions['m6_behavior'] = SimpleNamespace()
    app.extensions['auth_db'] = SimpleNamespace(
        begin=lambda: __import__('contextlib').nullcontext(
            SimpleNamespace(get=lambda model, ident: SimpleNamespace(
                id=1, username='admin', username_normalized='admin'))))
    monkeypatch.setattr(admin_api, 'session_row', lambda db: SimpleNamespace(user_id=1))
    admin_api.install(app)

    response = app.test_client().get('/api/admin/health')
    assert response.status_code == 200
    data = response.json['data']
    assert data['db_time'] == '2026-01-01T00:00:00'
    assert data['db_time_utc'].endswith('Z')
    parsed = datetime.datetime.fromisoformat(data['db_time_utc'].replace('Z', '+00:00'))
    assert parsed.utcoffset() == datetime.timedelta(0)
    assert data['movies'] == 3


def test_cancel_requested_prefers_memory_and_throttles_cross_db_reads():
    clock = SimpleNamespace(value=0.0)
    probe = _CancelProbe(interval=0.5, clock=lambda: clock.value)
    runtime = {'cancel': threading.Event()}
    counts = {'config': 0, 'ledger': 0}

    def read_config():
        counts['config'] += 1
        return {'generation': 1}

    def read_ledger():
        counts['ledger'] += 1
        return False

    deadline = 1e12
    assert _cancel_requested(runtime, deadline, {'generation': 1}, probe,
                             read_config, read_ledger) is False
    for _ in range(20):
        clock.value += 0.01
        assert _cancel_requested(runtime, deadline, {'generation': 1}, probe,
                                 read_config, read_ledger) is False
    assert counts == {'config': 1, 'ledger': 1}
    clock.value = 0.5
    assert _cancel_requested(runtime, deadline, {'generation': 1}, probe,
                             read_config, read_ledger) is False
    assert counts == {'config': 2, 'ledger': 2}

    runtime['cancel'].set()
    assert _cancel_requested(runtime, 0, {'generation': 1}, probe,
                             read_config, read_ledger) is True
    assert counts == {'config': 2, 'ledger': 2}


def test_cancel_requested_detects_generation_and_ledger_cancel():
    clock = SimpleNamespace(value=0.0)
    probe = _CancelProbe(interval=0.5, clock=lambda: clock.value)
    runtime = {'cancel': threading.Event()}
    ledger_reads = []

    def read_ledger():
        ledger_reads.append(1)
        return True

    current = {'generation': 1}
    assert _cancel_requested(runtime, 1e12, current, probe,
                             lambda: {'generation': 2}, read_ledger) is True
    assert ledger_reads == []          # disconnected config short-circuits the ledger
    clock.value = 0.5                  # let the 0.5s config cache expire
    assert _cancel_requested(runtime, 1e12, current, probe,
                             lambda: {'generation': 1}, read_ledger) is True
    assert ledger_reads == [1]

    probe = _CancelProbe(interval=0.5, clock=lambda: clock.value)
    attempts = {'count': 0}

    def flaky_config():
        attempts['count'] += 1
        if attempts['count'] == 1:
            raise RuntimeError('auth db unavailable')
        return {'generation': 1}

    with pytest.raises(RuntimeError):
        _cancel_requested(runtime, 1e12, current, probe, flaky_config,
                          lambda: False)
    # A failed read is not cached: the very next callback retries instead of
    # treating the error as a cancel decision.
    assert _cancel_requested(runtime, 1e12, current, probe, flaky_config,
                             lambda: False) is False
    assert attempts['count'] == 2


def test_rate_limiter_rejects_without_counting_and_evicts_oldest():
    clock = SimpleNamespace(value=0.0)
    limiter = RateLimiter(limit=3, window=60.0, capacity=2, clock=lambda: clock.value)
    assert [limiter.allow('a') for _ in range(4)] == [True, True, True, False]
    assert limiter._rates['a'] == (0.0, 3)
    clock.value = 61.0
    assert limiter.allow('a') is True
    assert limiter._rates['a'] == (61.0, 1)

    clock.value = 100.0
    limiter = RateLimiter(limit=3, window=60.0, capacity=2, clock=lambda: clock.value)
    assert limiter.allow('old') is True
    clock.value = 101.0
    assert limiter.allow('new') is True
    clock.value = 102.0
    assert limiter.allow('third') is True
    assert len(limiter._rates) == 2
    assert 'old' not in limiter._rates and 'third' in limiter._rates


def test_summarize_counts_unattributed_click_rows_not_impressions():
    engine = create_engine('sqlite://')
    imp, clicks = feedback_tables(None)
    imp.metadata.create_all(engine)
    start = datetime.datetime(2026, 11, 1)
    shown = start + datetime.timedelta(hours=1)
    with engine.begin() as connection:
        connection.execute(insert(imp), [
            dict(impression_id=1, request_id='r1', user_id=1, channel='home',
                 board_version='visible-v2', context='dashboard', position=1,
                 movie_id=10, shown_at=shown),
            dict(impression_id=2, request_id='r1', user_id=1, channel='home',
                 board_version='visible-v2', context='dashboard', position=2,
                 movie_id=11, shown_at=shown),
            dict(impression_id=3, request_id='r-old', user_id=1, channel='home',
                 board_version='legacy-v1', context='dashboard', position=1,
                 movie_id=10, shown_at=shown),
        ])
        connection.execute(insert(clicks), [
            dict(click_id=1, request_id='r1', user_id=1, channel='home',
                 position=1, movie_id=10, clicked_at=shown + datetime.timedelta(seconds=10)),
            dict(click_id=2, request_id='r1', user_id=1, channel='home',
                 position=1, movie_id=10, clicked_at=shown + datetime.timedelta(seconds=20)),
            dict(click_id=3, request_id='r-other', user_id=1, channel='home',
                 position=1, movie_id=10, clicked_at=shown + datetime.timedelta(seconds=30)),
            dict(click_id=4, request_id='r-old', user_id=1, channel='home',
                 position=1, movie_id=10, clicked_at=shown + datetime.timedelta(seconds=40)),
        ])
        result = summarize(connection, imp, clicks, start,
                           start + datetime.timedelta(days=1))
    assert result['impressions'] == 2
    assert result['clicks'] == 1               # distinct attributable impressions
    assert result['ctr'] == 0.5
    assert result['accounts'] == 1
    assert result['channels'][0]['clicks'] == 1
    # 4 click rows: two belong to impression 1, so only 2 rows are unattributed
    # (one with no impression at all, one only matching a legacy exposure).
    assert result['unattributed_clicks'] == 2
    assert set(result) == {'impressions', 'clicks', 'ctr', 'accounts', 'channels',
                           'daily', 'unattributed_clicks'}


def test_dead_recommendation_closure_removed():
    source = inspect.getsource(m6_business)
    assert 'movies_for_recommendation' not in source