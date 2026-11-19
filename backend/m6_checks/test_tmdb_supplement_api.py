"""Offline tests for the TMDB supplement read-only repository and admin API.

Nothing here touches MySQL, the network, configuration or a real catalog: the
repository is exercised against a fake connection that records SQL and returns
hand-written rows, and the Flask blueprint is mounted on a throw-away app that
injects the same extensions ``admin_api`` uses.  No expected count is copied
from a real library.
"""
import contextlib
import datetime
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from flask import Flask  # noqa: E402

from catalog import tmdb_supplement_api as api_module  # noqa: E402
from catalog.tmdb_supplement import repository  # noqa: E402

STAMP = datetime.datetime(2026, 11, 1, 12, 0, 0)
TOPICS = repository.TOPICS


# ------------------------------------------------------------------ fakes

class FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    """Routes each statement to a table by the first ``FROM <table>``.

    The three aggregate statements added for the V2 dashboard (cache watermark,
    catalog counts, batch counts) are answered from the same fake tables so the
    API tests do not need a second fake just for ``COUNT(*)``.
    """

    def __init__(self, tables):
        self.tables = {name.lower(): rows for name, rows in tables.items()}
        self.statements = []

    def _aggregate(self, sql):
        metadata = list(self.tables.get('tmdb_movie_metadata', []))
        if 'AS document_count' in sql:
            fetched = [row.get('fetched_at') for row in metadata
                       if row.get('fetched_at') is not None]
            return [{
                'document_count': len(metadata),
                'max_fetched_at': max(fetched) if fetched else None,
                'max_updated_at': max(fetched) if fetched else None,
            }]
        if 'AS active_movies' in sql:
            movies = list(self.tables.get('movies', []))
            active = [row for row in movies
                      if row.get('catalog_status') == 'active']
            active_ids = {row.get('movie_id') for row in active}
            mapped = {row.get('movie_id') for row in metadata
                      if row.get('movie_id') in active_ids}
            return [{'active_movies': len(active), 'tmdb_mapped': len(mapped)}]
        if 'AS total' in sql:
            rows = self.tables.get('ingest_batches', [])
            return [{'total': len(rows)}]
        return None

    def execute(self, statement, params=None):
        sql = str(statement)
        params = dict(params or {})
        self.statements.append((sql, params))
        aggregate = self._aggregate(sql)
        if aggregate is not None:
            return FakeResult(aggregate)
        match = re.search(r'FROM\s+([A-Za-z_]+)', sql)
        table = match.group(1).lower() if match else ''
        rows = list(self.tables.get(table, []))
        if 'limit' in params:
            start = int(params.get('offset', 0) or 0)
            rows = rows[start:start + int(params['limit'])]
        return FakeResult(rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def close(self):
        pass


class FakeEngine:
    def __init__(self, tables):
        self.tables = tables
        self.connections = 0

    def connect(self):
        self.connections += 1
        return FakeConnection(self.tables)


class FakeSessionRow:
    def __init__(self, user_id=7):
        self.user_id = user_id


class FakeUser:
    def __init__(self, username):
        self.id = 7
        self.username = username
        self.username_normalized = username


class FakeAuthDb:
    def __init__(self, user):
        self.user = user

    @contextlib.contextmanager
    def begin(self):
        yield self

    def get(self, model, identifier):
        return self.user


# ------------------------------------------------------------- fixtures

def metadata_row(movie_id, *, tmdb_id=None, statuses=None, fetched_at=STAMP,
                 metadata=None):
    statuses = statuses or {topic: 'PRESENT' for topic in TOPICS}
    return {
        'movie_id': movie_id,
        'tmdb_id': tmdb_id if tmdb_id is not None else movie_id + 1000,
        'payload_sha256': 'a' * 64,
        'fetched_at': fetched_at,
        'field_status_json': json.dumps(statuses),
        'metadata_json': json.dumps(metadata if metadata is not None else {
            'title': 'Movie %d' % movie_id,
            'original_title': 'Movie %d' % movie_id,
            'release_date': '1999-03-31',
            'original_language': 'en',
            'runtime': 120,
            'overview': 'synthetic',
            'poster_path': '/poster.jpg',
            'backdrop_path': '/backdrop.jpg',
            'budget': 0,
            'revenue': 0,
            'genres': [{'id': 18, 'name': '剧情'}],
            'private_note': 'must never be forwarded',
        }),
    }


def relation_tables():
    return {
        'tmdb_movie_metadata': [metadata_row(1), metadata_row(2)],
        'tmdb_movie_countries': [
            {'movie_id': 1, 'country_iso2': 'US', 'country_name': 'United States'},
        ],
        'tmdb_movie_companies': [
            {'movie_id': 1, 'tmdb_company_id': 420, 'name': 'Studio',
             'origin_country': 'US', 'logo_path': '/logo.png'},
        ],
        'tmdb_movie_languages': [
            {'movie_id': 1, 'language_code': 'en', 'english_name': 'English',
             'name': '英语'},
        ],
        'tmdb_movie_keywords': [
            {'movie_id': 1, 'tmdb_keyword_id': 99, 'name': 'space'},
        ],
        'tmdb_release_records': [
            {'movie_id': 1, 'country_iso2': 'US', 'release_type': 3,
             'release_date': datetime.date(1999, 3, 31), 'certification': 'PG',
             'note': None, 'certification_language': 'en'},
            {'movie_id': 1, 'country_iso2': 'CN', 'release_type': 3,
             'release_date': None, 'certification': None,
             'note': None, 'certification_language': None},
        ],
        'movie_credits': [
            {'movie_id': 1, 'person_id': 15, 'role_type': 'crew', 'job': 'Director',
             'character_name': None, 'sort_order': 0, 'person_name': 'Someone'},
            {'movie_id': 1, 'person_id': 16, 'role_type': 'cast', 'job': None,
             'character_name': 'Hero', 'sort_order': 1, 'person_name': 'Actor'},
        ],
        'tmdb_metric_observations': [
            {'movie_id': 1, 'observed_at': STAMP, 'vote_average': 8.25,
             'vote_count': 1200, 'popularity': 33.5},
        ],
    }


def build_app(tables, *, username='deepseek'):
    app = Flask('test_tmdb_supplement')
    app.config['ADMIN_USERS'] = ['deepseek']
    app.extensions['auth_db'] = FakeAuthDb(FakeUser(username))
    app.extensions['catalog_engine'] = FakeEngine(tables)
    # Each app gets an empty document cache so one test cannot leak its snapshot
    # into the next one through the module-level singleton.
    api_module._DOCUMENT_CACHE = api_module.DocumentCache()
    api_module.install(app)
    return app


@contextlib.contextmanager
def signed_in(enabled=True):
    row = FakeSessionRow() if enabled else None
    lookup = lambda db: row  # noqa: E731 - deliberately tiny fake
    with mock.patch.object(api_module, '_session_row', lambda: lookup), \
            mock.patch.object(api_module, '_user_model', lambda: object):
        yield


# ---------------------------------------------------------- repository

class RepositoryTests(unittest.TestCase):
    def test_load_documents_assembles_topic_facts(self):
        conn = FakeConnection(relation_tables())
        documents = repository.load_documents(conn)
        self.assertEqual(2, len(documents))
        first = documents[0]
        self.assertEqual('tmdb-supplement-v1', first['schema_version'])
        self.assertEqual(1, first['movie_id'])
        self.assertEqual(1001, first['tmdb_id'])
        self.assertEqual('2026-11-01T12:00:00Z', first['fetched_at'])
        self.assertEqual(
            {'iso_3166_1': 'US', 'name': 'United States'}, first['facts']['countries'][0])
        self.assertEqual(420, first['facts']['companies'][0]['company_id'])
        self.assertEqual('en', first['facts']['spoken_languages'][0]['iso_639_1'])
        self.assertEqual(99, first['facts']['keywords'][0]['keyword_id'])
        # A record without a parseable date is not usable as a release fact.
        self.assertEqual(1, len(first['facts']['releases']))
        self.assertEqual('1999-03-31', first['facts']['releases'][0]['release_date'])
        self.assertEqual(8.25, first['facts']['metrics']['vote_average'])
        self.assertEqual(1200, first['facts']['metrics']['vote_count'])
        self.assertEqual({'cast', 'crew'},
                         {credit['role_type'] for credit in first['facts']['credits']})

    def test_load_documents_never_forwards_raw_payload(self):
        conn = FakeConnection(relation_tables())
        documents = repository.load_documents(conn)
        self.assertNotIn('private_note', documents[0]['facts']['base'])
        allowed = set(repository.BASE_FIELDS) | {'genres', 'release_year'}
        self.assertTrue(set(documents[0]['facts']['base']) <= allowed)

    def test_load_documents_does_not_fabricate_failed_topics(self):
        statuses = {topic: 'PRESENT' for topic in TOPICS}
        statuses.update({'countries': 'FAILED', 'keywords': 'EMPTY',
                         'metrics': 'NOT_FETCHED', 'releases': 'MISSING'})
        tables = relation_tables()
        tables['tmdb_movie_metadata'] = [metadata_row(1, statuses=statuses)]
        documents = repository.load_documents(FakeConnection(tables))
        facts = documents[0]['facts']
        self.assertNotIn('countries', facts)
        self.assertNotIn('releases', facts)
        self.assertNotIn('metrics', facts)
        self.assertEqual([], facts['keywords'])
        self.assertEqual('FAILED', documents[0]['field_status']['countries'])

    def test_load_documents_clamps_hard_limit_and_keeps_offset(self):
        conn = FakeConnection(relation_tables())
        repository.load_documents(conn, limit=999999, offset=40)
        sql, params = conn.statements[0]
        self.assertIn('LIMIT :limit OFFSET :offset', sql)
        self.assertEqual(repository.MAX_DOCUMENTS, params['limit'])
        self.assertEqual(40, params['offset'])

    def test_load_documents_rejects_invalid_pagination(self):
        conn = FakeConnection(relation_tables())
        with self.assertRaises(repository.RepositoryError):
            repository.load_documents(conn, limit='many')
        with self.assertRaises(repository.RepositoryError):
            repository.load_documents(conn, offset=-1)

    def test_load_coverage_counts_topics_and_total(self):
        rows = []
        for topic in TOPICS:
            rows.append({'topic': topic, 'status': 'PRESENT', 'count': 4,
                         'observed_at': STAMP})
            rows.append({'topic': topic, 'status': 'MISSING', 'count': 1,
                         'observed_at': STAMP})
        rows.append({'topic': 'unknown_topic', 'status': 'PRESENT', 'count': 9,
                     'observed_at': STAMP})
        coverage = repository.load_coverage(FakeConnection({'tmdb_movie_metadata': rows}))
        self.assertEqual(5, coverage['documents'])
        self.assertEqual(4, coverage['topics']['base']['PRESENT'])
        self.assertEqual(1, coverage['topics']['metrics']['MISSING'])
        self.assertEqual(0, coverage['topics']['metrics']['FAILED'])
        self.assertNotIn('unknown_topic', coverage['topics'])
        self.assertEqual('2026-11-01T12:00:00Z', coverage['observed_at'])

    def test_load_missing_queue_returns_minimal_fields(self):
        rows = [{'movie_id': 3, 'tmdb_id': 55, 'topic': 'credits',
                 'status': 'FAILED',
                 'attempt_at_json': '2026-11-02T00:00:00.000000Z',
                 'fetched_at': STAMP, 'error_code': 'HTTP_500'}]
        items = repository.load_missing_queue(
            FakeConnection({'tmdb_movie_metadata': rows}), limit=10)
        self.assertEqual(1, len(items))
        self.assertEqual({'movie_id', 'tmdb_id', 'topic', 'status',
                          'attempt_at', 'error_code'}, set(items[0]))
        self.assertEqual('FAILED', items[0]['status'])
        self.assertEqual('HTTP_500', items[0]['error_code'])
        self.assertTrue(items[0]['attempt_at'].startswith('2026-11-02'))


# ----------------------------------------------------------------- api

class ApiTests(unittest.TestCase):
    def test_coverage_requires_authentication(self):
        app = build_app(relation_tables())
        with signed_in(False):
            response = app.test_client().get('/api/admin/tmdb-supplement/coverage')
        self.assertEqual(401, response.status_code)
        self.assertEqual('AUTHENTICATION_REQUIRED', response.get_json()['code'])

    def test_coverage_rejects_non_admin(self):
        app = build_app(relation_tables(), username='stranger')
        with signed_in(True):
            response = app.test_client().get('/api/admin/tmdb-supplement/coverage')
        self.assertEqual(403, response.status_code)
        self.assertEqual('FORBIDDEN', response.get_json()['code'])

    def test_coverage_returns_grouped_counts(self):
        rows = [{'topic': 'base', 'status': 'PRESENT', 'count': 2,
                 'observed_at': STAMP}]
        app = build_app({'tmdb_movie_metadata': rows})
        with signed_in(True):
            response = app.test_client().get('/api/admin/tmdb-supplement/coverage')
        body = response.get_json()
        self.assertEqual(200, response.status_code)
        self.assertEqual('OK', body['code'])
        self.assertEqual(2, body['data']['documents'])
        self.assertEqual(2, body['data']['topics']['base']['PRESENT'])

    def test_coverage_scoped_by_limit_uses_documents(self):
        app = build_app(relation_tables())
        with signed_in(True):
            response = app.test_client().get(
                '/api/admin/tmdb-supplement/coverage?limit=1&offset=0')
        body = response.get_json()['data']
        self.assertEqual(1, body['documents'])
        self.assertTrue(body['scope']['scoped'])
        self.assertEqual(1, body['topics']['countries']['PRESENT'])

    def test_coverage_rejects_bad_pagination(self):
        app = build_app(relation_tables())
        with signed_in(True):
            response = app.test_client().get(
                '/api/admin/tmdb-supplement/coverage?limit=0')
        self.assertEqual(400, response.status_code)
        self.assertEqual('INVALID_INPUT', response.get_json()['code'])

    def test_analytics_returns_bounded_payload(self):
        tables = relation_tables()
        tables['tmdb_movie_metadata'] = [metadata_row(1)]
        app = build_app(tables)
        with signed_in(True):
            response = app.test_client().get(
                '/api/admin/tmdb-supplement/analytics?min_votes=10&limit=5')
        body = response.get_json()
        self.assertEqual(200, response.status_code)
        data = body['data']
        self.assertEqual('tmdb-supplement-analytics-v1', data['contract'])
        self.assertEqual(1, data['scope']['movies_matched'])
        self.assertEqual(1, data['details']['total'])
        self.assertEqual(1, data['scope']['repository']['documents_loaded'])
        self.assertFalse(data['scope']['repository']['truncated'])
        # The raw document never leaves the server.
        body_text = json.dumps(data)
        self.assertNotIn('"facts":', body_text)
        self.assertNotIn('payload_sha256', body_text)
        self.assertNotIn('private_note', body_text)

    def test_analytics_reports_identity_conflict(self):
        tables = relation_tables()
        tables['tmdb_movie_metadata'] = [
            metadata_row(1, tmdb_id=500),
            metadata_row(1, tmdb_id=501),
        ]
        app = build_app(tables)
        with signed_in(True):
            response = app.test_client().get('/api/admin/tmdb-supplement/analytics')
        body = response.get_json()
        self.assertEqual(409, response.status_code)
        self.assertEqual('TMDB_IDENTITY_CONFLICT', body['code'])
        self.assertTrue(body['data']['review_required'])

    def test_analytics_rejects_more_than_three_countries(self):
        app = build_app(relation_tables())
        with signed_in(True):
            response = app.test_client().get(
                '/api/admin/tmdb-supplement/analytics?country=US,CN,JP,KR')
        self.assertEqual(400, response.status_code)
        self.assertEqual('INVALID_INPUT', response.get_json()['code'])

    def test_analytics_rejects_invalid_min_votes(self):
        app = build_app(relation_tables())
        with signed_in(True):
            response = app.test_client().get(
                '/api/admin/tmdb-supplement/analytics?min_votes=5000000')
        self.assertEqual(400, response.status_code)
        self.assertEqual('INVALID_INPUT', response.get_json()['code'])

    def test_analytics_filters_by_country_and_year(self):
        tables = relation_tables()
        tables['tmdb_movie_metadata'] = [metadata_row(1)]
        app = build_app(tables)
        with signed_in(True):
            matched = app.test_client().get(
                '/api/admin/tmdb-supplement/analytics?country=US')
            excluded = app.test_client().get(
                '/api/admin/tmdb-supplement/analytics?year_from=2000')
        self.assertEqual(1, matched.get_json()['data']['scope']['movies_matched'])
        self.assertEqual(0, excluded.get_json()['data']['scope']['movies_matched'])

    def test_missing_queue_endpoint(self):
        rows = [{'movie_id': 3, 'tmdb_id': 55, 'topic': 'credits',
                 'status': 'FAILED', 'attempt_at_json': None,
                 'fetched_at': STAMP, 'error_code': 'HTTP_500'}]
        app = build_app({'tmdb_movie_metadata': rows})
        with signed_in(True):
            response = app.test_client().get(
                '/api/admin/tmdb-supplement/missing?limit=10')
        body = response.get_json()
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, body['data']['returned'])
        self.assertFalse(body['data']['has_more'])
        self.assertEqual('credits', body['data']['items'][0]['topic'])

    def test_data_unavailable_on_repository_error(self):
        app = build_app(relation_tables())
        with signed_in(True), mock.patch.object(
                repository, 'load_coverage',
                side_effect=repository.RepositoryError('offline')):
            response = app.test_client().get('/api/admin/tmdb-supplement/coverage')
        self.assertEqual(503, response.status_code)
        self.assertEqual('DATA_UNAVAILABLE', response.get_json()['code'])

    def test_analytics_country_pagination_is_forwarded(self):
        tables = relation_tables()
        tables['tmdb_movie_metadata'] = [metadata_row(1)]
        app = build_app(tables)
        with signed_in(True):
            response = app.test_client().get(
                '/api/admin/tmdb-supplement/analytics'
                '?country_limit=1&country_offset=1&top=5')
        self.assertEqual(200, response.status_code)
        filters = response.get_json()['data']['filters']
        self.assertEqual(1, filters['country_limit'])
        self.assertEqual(1, filters['country_offset'])

    def test_analytics_accepts_dimension_filters(self):
        tables = relation_tables()
        tables['tmdb_movie_metadata'] = [metadata_row(1)]
        app = build_app(tables)
        with signed_in(True):
            response = app.test_client().get(
                '/api/admin/tmdb-supplement/analytics'
                '?genres=剧情&decades=1990s&country_known=false'
                '&observed_from=2026-01-01T00:00:00Z')
        self.assertEqual(200, response.status_code)
        data = response.get_json()['data']['filters']
        self.assertEqual(['剧情'], data['genres']['names'])
        self.assertEqual([1990], data['decades'])
        self.assertIs(False, data['country_known'])
        self.assertIsNotNone(data['observed_from'])

    def test_analytics_rejects_out_of_range_dimension_filters(self):
        app = build_app(relation_tables())
        for query in ('country_limit=0', 'country_limit=201', 'country_offset=1001',
                      'top=51', 'country_known=maybe', 'genres=', 'observed_from=nope'):
            with self.subTest(query=query):
                with signed_in(True):
                    response = app.test_client().get(
                        '/api/admin/tmdb-supplement/analytics?' + query)
                self.assertEqual(400, response.status_code)
                self.assertEqual('INVALID_INPUT', response.get_json()['code'])

    def test_analytics_three_country_comparison(self):
        tables = relation_tables()
        tables['tmdb_movie_metadata'] = [metadata_row(1)]
        app = build_app(tables)
        with signed_in(True):
            ok = app.test_client().get(
                '/api/admin/tmdb-supplement/analytics?countries=US,CN,JP')
            too_many = app.test_client().get(
                '/api/admin/tmdb-supplement/analytics?countries=US,CN,JP,KR')
        self.assertEqual(200, ok.status_code)
        self.assertEqual(400, too_many.status_code)
        self.assertEqual(len(ok.get_json()['data']['comparison']['countries']), 3)


if __name__ == '__main__':
    unittest.main()