"""Offline API tests for the V2 resource endpoints and the document cache.

The Flask app is mounted on a throw-away instance with the same extensions
``admin_api`` uses and a fake catalog connection; nothing here touches MySQL, the
network or a real catalog.  Expected numbers are derived from the fixtures only.
"""
import contextlib
import datetime
import json
import re
import sys
import threading
import time
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
LATER = datetime.datetime(2026, 11, 2, 12, 0, 0)
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
    def __init__(self, tables):
        self.tables = {name.lower(): rows for name, rows in tables.items()}
        self.statements = []

    def _metadata(self):
        return list(self.tables.get('tmdb_movie_metadata', []))

    def _movies(self):
        return list(self.tables.get('movies', []))

    def _batch_rows(self, params):
        rows = [row for row in self.tables.get('ingest_batches', [])
                if row.get('source') == params.get('source')]
        if 'status' in params:
            rows = [row for row in rows if row.get('status') == params['status']]
        rows.sort(key=lambda row: (str(row.get('started_at')), int(row['batch_id'])),
                  reverse=True)
        return rows

    def execute(self, statement, params=None):
        sql = str(statement)
        params = dict(params or {})
        self.statements.append((sql, params))

        if 'AS document_count' in sql:
            movies = self._movies()
            metadata = self._metadata()
            if movies:
                active_ids = {row['movie_id'] for row in movies
                              if row.get('catalog_status') == 'active'}
                metadata = [row for row in metadata
                            if row.get('movie_id') in active_ids]
            fetched = [row.get('fetched_at') for row in metadata
                       if row.get('fetched_at') is not None]
            updated = [row.get('updated_at', row.get('fetched_at')) for row in metadata
                       if row.get('updated_at', row.get('fetched_at')) is not None]
            return FakeResult([{
                'document_count': len(metadata),
                'max_fetched_at': max(fetched) if fetched else None,
                'max_updated_at': max(updated) if updated else None,
            }])
        if 'AS active_movies' in sql:
            active = [row for row in self._movies()
                      if row.get('catalog_status') == 'active']
            active_ids = {row['movie_id'] for row in active}
            mapped = {row['movie_id'] for row in self._metadata()
                      if row['movie_id'] in active_ids}
            return FakeResult([{'active_movies': len(active),
                                'tmdb_mapped': len(mapped)}])
        if 'AS total' in sql and 'FROM ingest_batches' in sql:
            return FakeResult([{'total': len(self._batch_rows(params))}])
        if 'FROM ingest_batches' in sql:
            rows = self._batch_rows(params)
            start = int(params.get('offset', 0) or 0)
            if 'limit' in params:
                rows = rows[start:start + int(params['limit'])]
            return FakeResult(rows)
        if 'FROM ingest_items' in sql:
            wanted = set(params.get('batch_ids', []))
            rows = [row for row in self.tables.get('ingest_items', [])
                    if row.get('batch_id') in wanted]
            aggregated = {}
            if 'error_code IS NOT NULL' in sql:
                for row in rows:
                    if row.get('error_code') is None:
                        continue
                    key = (row['batch_id'], row['error_code'])
                    aggregated[key] = aggregated.get(key, 0) + 1
                return FakeResult([{'batch_id': key[0], 'error_code': key[1],
                                    'count': count}
                                   for key, count in aggregated.items()])
            for row in rows:
                key = (row['batch_id'], row.get('status'))
                aggregated[key] = aggregated.get(key, 0) + 1
            return FakeResult([{'batch_id': key[0], 'status': key[1],
                                'count': count}
                               for key, count in aggregated.items()])
        if 'FROM movies AS mv' in sql and 'm.movie_id IS NULL' in sql:
            mapped = {row['movie_id'] for row in self._metadata()}
            rows = [row for row in self._movies()
                    if row.get('catalog_status') == 'active'
                    and row['movie_id'] not in mapped]
            if 'limit' in params:
                rows = rows[:int(params['limit'])]
            return FakeResult(rows)
        if 'FROM tmdb_movie_metadata AS m' in sql and 'field_status_json' in sql:
            movies = self._movies()
            rows = self._metadata()
            if movies:
                active_ids = {row['movie_id'] for row in movies
                              if row.get('catalog_status') == 'active'}
                rows = [row for row in rows if row.get('movie_id') in active_ids]
            start = int(params.get('offset', 0) or 0)
            if 'limit' in params:
                rows = rows[start:start + int(params['limit'])]
            return FakeResult(rows)

        match = re.search(r'FROM\s+([A-Za-z_]+)', sql)
        table = match.group(1).lower() if match else ''
        rows = list(self.tables.get(table, []))
        if 'movie_ids' in params:
            rows = [row for row in rows if row.get('movie_id') in set(params['movie_ids'])]
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

    def connect(self):
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

def metadata_row(movie_id, *, statuses=None, fetched_at=STAMP, poster='/p.jpg',
                 overview='synthetic', keywords=None):
    resolved = {topic: 'PRESENT' for topic in TOPICS}
    if statuses:
        resolved.update(statuses)
    return {
        'movie_id': movie_id,
        'tmdb_id': movie_id + 1000,
        'payload_sha256': 'a' * 64,
        'fetched_at': fetched_at,
        'updated_at': fetched_at,
        'field_status_json': json.dumps(resolved),
        'metadata_json': json.dumps({
            'title': 'Movie %d' % movie_id,
            'original_title': 'Movie %d' % movie_id,
            'release_date': '1999-03-31',
            'original_language': 'en',
            'runtime': 110,
            'overview': overview,
            'poster_path': poster,
            'backdrop_path': '/b.jpg',
            'budget': 0,
            'revenue': 0,
            'genres': [{'id': 18, 'name': '剧情'}],
            'keywords': keywords or [],
        }),
    }


def fixture_tables():
    return {
        'movies': [
            {'movie_id': 1, 'title': 'Movie 1', 'release_year': 1999,
             'catalog_status': 'active'},
            {'movie_id': 2, 'title': 'Movie 2', 'release_year': 2001,
             'catalog_status': 'active'},
            {'movie_id': 3, 'title': 'Movie 3', 'release_year': 2010,
             'catalog_status': 'active'},
            {'movie_id': 90, 'title': 'Unmapped movie', 'release_year': 1990,
             'catalog_status': 'active'},
            {'movie_id': 91, 'title': 'Hidden movie', 'release_year': 1991,
             'catalog_status': 'hidden'},
        ],
        'tmdb_movie_metadata': [
            metadata_row(1),
            metadata_row(2, statuses={'keywords': 'FAILED'}, poster=None),
            metadata_row(3, statuses={'keywords': 'EMPTY', 'companies': 'EMPTY',
                                      'credits': 'NOT_FETCHED'}),
        ],
        'tmdb_movie_countries': [
            {'movie_id': 1, 'country_iso2': 'US', 'country_name': 'United States'},
            {'movie_id': 1, 'country_iso2': 'CN', 'country_name': 'China'},
            {'movie_id': 2, 'country_iso2': 'US', 'country_name': 'United States'},
            {'movie_id': 3, 'country_iso2': 'JP', 'country_name': 'Japan'},
        ],
        'tmdb_movie_companies': [
            {'movie_id': 1, 'tmdb_company_id': 7, 'name': 'Studio A',
             'origin_country': 'US', 'logo_path': None},
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
        ],
        'movie_credits': [
            {'movie_id': 1, 'person_id': 15, 'role_type': 'crew', 'job': 'Director',
             'character_name': None, 'sort_order': 0, 'person_name': 'Director'},
            {'movie_id': 1, 'person_id': 16, 'role_type': 'cast', 'job': None,
             'character_name': 'Hero', 'sort_order': 1, 'person_name': 'Actor'},
        ],
        'tmdb_metric_observations': [
            {'movie_id': 1, 'observed_at': STAMP, 'vote_average': 8.5,
             'vote_count': 1200, 'popularity': 33.5},
            {'movie_id': 2, 'observed_at': STAMP, 'vote_average': 6.0,
             'vote_count': 10, 'popularity': 2.0},
            {'movie_id': 3, 'observed_at': STAMP, 'vote_average': 7.0,
             'vote_count': 300, 'popularity': 8.0},
        ],
        'ingest_batches': [
            {'batch_id': 501, 'source': 'tmdb', 'mode': 'write',
             'status': 'succeeded', 'started_at': STAMP, 'finished_at': STAMP,
             'source_snapshot_ref': 'tmdb-supplement:job-1',
             'counts_json': json.dumps({'documents': 3, 'success': 3,
                                        'skipped': 1, 'failed': 0,
                                        'executed_operations': 5}),
             'error_summary': None},
            {'batch_id': 502, 'source': 'tmdb', 'mode': 'write',
             'status': 'failed', 'started_at': LATER, 'finished_at': LATER,
             'source_snapshot_ref': 'tmdb-supplement:job-2',
             'counts_json': json.dumps({}), 'error_summary': 'HTTP_500'},
        ],
        'ingest_items': [
            {'batch_id': 501, 'status': 'inserted', 'error_code': None},
            {'batch_id': 501, 'status': 'updated', 'error_code': None},
            {'batch_id': 501, 'status': 'skipped', 'error_code': None},
            {'batch_id': 501, 'status': 'error', 'error_code': 'HTTP_500'},
            {'batch_id': 501, 'status': 'error', 'error_code': 'HTTP_429'},
        ],
    }


def build_app(tables, *, username='deepseek'):
    app = Flask('test_admin_v2_resource_api')
    app.config['ADMIN_USERS'] = ['deepseek']
    app.extensions['auth_db'] = FakeAuthDb(FakeUser(username))
    app.extensions['catalog_engine'] = FakeEngine(tables)
    api_module._DOCUMENT_CACHE = api_module.DocumentCache()
    api_module.install(app)
    return app


@contextlib.contextmanager
def signed_in(enabled=True):
    row = FakeSessionRow() if enabled else None
    lookup = lambda db: row  # noqa: E731
    with mock.patch.object(api_module, '_session_row', lambda: lookup), \
            mock.patch.object(api_module, '_user_model', lambda: object):
        yield


# ------------------------------------------------------------------ cache

class DocumentCacheTests(unittest.TestCase):
    def test_hit_within_ttl_and_miss_after_expiry(self):
        now = [0.0]
        loads = []

        def loader():
            loads.append(1)
            return ['doc']

        cache = api_module.DocumentCache(ttl=15, clock=lambda: now[0])
        watermark = {'document_count': 1, 'max_fetched_at': 'a', 'max_updated_at': 'a'}
        documents, report = cache.fetch(watermark, loader)
        self.assertEqual(['doc'], documents)
        self.assertFalse(report['hit'])

        now[0] = 10.0
        documents, report = cache.fetch(watermark, loader)
        self.assertTrue(report['hit'])
        self.assertEqual(1, len(loads))

        now[0] = 16.0
        documents, report = cache.fetch(watermark, loader)
        self.assertFalse(report['hit'])
        self.assertEqual(2, len(loads))

    def test_watermark_change_invalidates_even_inside_ttl(self):
        now = [0.0]
        loads = []
        cache = api_module.DocumentCache(ttl=15, clock=lambda: now[0])
        cache.fetch({'document_count': 1}, lambda: loads.append(1) or ['a'])
        cache.fetch({'document_count': 2}, lambda: loads.append(2) or ['b'])
        self.assertEqual([1, 2], loads)
        self.assertTrue(cache.fetch({'document_count': 2}, lambda: [])[1]['hit'])

    def test_failed_load_does_not_replace_the_snapshot(self):
        cache = api_module.DocumentCache()
        watermark = {'document_count': 1}

        def broken():
            raise repository.RepositoryError('offline')

        with self.assertRaises(repository.RepositoryError):
            cache.fetch(watermark, broken)
        self.assertEqual(0, cache.load_count)
        documents, report = cache.fetch(watermark, lambda: ['ok'])
        self.assertEqual(['ok'], documents)
        self.assertFalse(report['hit'])
        self.assertEqual(1, cache.load_count)
        documents, report = cache.fetch(watermark, lambda: ['never'])
        self.assertTrue(report['hit'])
        self.assertEqual(['ok'], documents)

    def test_concurrent_misses_load_documents_once(self):
        cache = api_module.DocumentCache()
        calls = []

        def loader():
            calls.append(1)
            time.sleep(0.05)
            return ['shared']

        results = []
        errors = []
        barrier = threading.Barrier(8)

        def worker():
            try:
                barrier.wait()
                documents, report = cache.fetch({'document_count': 1}, loader)
                results.append((documents, report['hit']))
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([], errors)
        self.assertEqual(1, len(calls))
        self.assertEqual(8, len(results))
        self.assertEqual(1, sum(1 for _, hit in results if hit is False))


# ------------------------------------------------------------------- api

class AnalyticsEndpointTests(unittest.TestCase):
    def test_scope_catalog_and_cache_miss_then_hit(self):
        app = build_app(fixture_tables())
        client = app.test_client()
        with signed_in(True):
            first = client.get('/api/admin/tmdb-supplement/analytics')
            second = client.get('/api/admin/tmdb-supplement/analytics')
        self.assertEqual(200, first.status_code)
        data = first.get_json()['data']
        catalog = data['scope']['catalog']
        self.assertEqual(4, catalog['active_movies'])
        self.assertEqual(3, catalog['tmdb_mapped'])
        self.assertEqual(1, catalog['unmapped'])
        self.assertFalse(data['scope']['cache']['hit'])
        self.assertEqual(1, data['scope']['cache']['load_count'])
        self.assertTrue(data['scope']['cache']['loaded_at'])
        self.assertEqual(3, data['scope']['cache']['watermark']['document_count'])
        self.assertTrue(second.get_json()['data']['scope']['cache']['hit'])

    def test_watermark_change_forces_reload(self):
        tables = fixture_tables()
        app = build_app(tables)
        client = app.test_client()
        with signed_in(True):
            client.get('/api/admin/tmdb-supplement/analytics')
            tables['tmdb_movie_metadata'][0]['updated_at'] = LATER
            response = client.get('/api/admin/tmdb-supplement/analytics')
        cache = response.get_json()['data']['scope']['cache']
        self.assertFalse(cache['hit'])
        self.assertEqual(2, cache['load_count'])

    def test_detail_search_parameters_are_forwarded(self):
        app = build_app(fixture_tables())
        client = app.test_client()
        with signed_in(True):
            response = client.get(
                '/api/admin/tmdb-supplement/analytics'
                '?keyword=99&company=7&sort=votes&order=desc'
                '&overlap=US,CN&limit=5')
            missing = client.get(
                '/api/admin/tmdb-supplement/analytics?missing=poster&limit=5')
        self.assertEqual(200, response.status_code)
        data = response.get_json()['data']
        applied = data['details']['applied_filters']
        self.assertEqual(99, applied['keyword'])
        self.assertEqual(7, applied['company'])
        self.assertEqual([], applied['missing'])
        self.assertEqual('votes', applied['sort'])
        self.assertEqual('desc', applied['order'])
        self.assertEqual(['CN', 'US'], applied['overlap'])
        self.assertEqual(1, data['details']['total'])
        self.assertEqual(1, data['details']['items'][0]['movie_id'])
        missing_data = missing.get_json()['data']
        self.assertEqual(['poster'], missing_data['details']['applied_filters']['missing'])
        self.assertEqual(1, missing_data['details']['total'])
        self.assertEqual(2, missing_data['details']['items'][0]['movie_id'])
        # Detail-only filters leave the aggregate scope intact.
        self.assertEqual(3, data['scope']['movies_matched'])

    def test_detail_search_rejects_invalid_values(self):
        app = build_app(fixture_tables())
        client = app.test_client()
        for query in ('keyword=0', 'keyword=true', 'company=-1',
                      'missing=trailer', 'missing=poster,trailer',
                      'sort=tmdb_id', 'sort=title&order=up', 'order=desc',
                      'overlap=US,CN,JP,KR', 'overlap=USA'):
            with self.subTest(query=query):
                with signed_in(True):
                    response = client.get(
                        '/api/admin/tmdb-supplement/analytics?' + query)
                self.assertEqual(400, response.status_code)
                self.assertEqual('INVALID_INPUT', response.get_json()['code'])

    def test_analytics_requires_authentication_and_admin(self):
        app = build_app(fixture_tables())
        with signed_in(False):
            anonymous = app.test_client().get('/api/admin/tmdb-supplement/analytics')
        self.assertEqual(401, anonymous.status_code)
        self.assertEqual('AUTHENTICATION_REQUIRED', anonymous.get_json()['code'])

        other = build_app(fixture_tables(), username='stranger')
        with signed_in(True):
            forbidden = other.test_client().get('/api/admin/tmdb-supplement/analytics')
        self.assertEqual(403, forbidden.status_code)
        self.assertEqual('FORBIDDEN', forbidden.get_json()['code'])


class QualityEndpointTests(unittest.TestCase):
    def test_quality_active_denominator_includes_unmapped(self):
        app = build_app(fixture_tables())
        with signed_in(True):
            response = app.test_client().get(
                '/api/admin/tmdb-supplement/quality?denominator=active&limit=50')
        self.assertEqual(200, response.status_code)
        data = response.get_json()['data']
        self.assertEqual('tmdb-supplement-quality-v1', data['contract'])
        self.assertEqual('active', data['denominator']['mode'])
        self.assertEqual(4, data['denominator']['movies'])
        self.assertEqual(1, data['topics']['keywords']['UNMAPPED'])
        self.assertEqual(1, data['topics']['keywords']['FAILED'])
        self.assertEqual(1, data['topics']['keywords']['EMPTY'])
        self.assertEqual(1, data['states']['unmapped'])
        self.assertTrue(data['cache']['hit'] is False)
        categories = {(row['movie_id'], row['category'])
                      for row in data['problems']['items']}
        self.assertIn((90, 'UNMAPPED'), categories)

    def test_quality_mapped_denominator_hides_unmapped(self):
        app = build_app(fixture_tables())
        with signed_in(True):
            response = app.test_client().get(
                '/api/admin/tmdb-supplement/quality?denominator=mapped')
        data = response.get_json()['data']
        self.assertEqual(3, data['denominator']['movies'])
        self.assertEqual(0, data['topics']['keywords']['UNMAPPED'])
        self.assertNotIn(90, {row['movie_id'] for row in data['problems']['items']})

    def test_quality_filters_and_pagination(self):
        app = build_app(fixture_tables())
        with signed_in(True):
            failed = app.test_client().get(
                '/api/admin/tmdb-supplement/quality?topic=keywords&category=FAILED')
            page = app.test_client().get(
                '/api/admin/tmdb-supplement/quality?limit=1&offset=0')
        failed_data = failed.get_json()['data']['problems']
        self.assertEqual(1, failed_data['total'])
        self.assertEqual(2, failed_data['items'][0]['movie_id'])
        page_data = page.get_json()['data']['problems']
        self.assertEqual(1, page_data['returned'])
        self.assertTrue(page_data['has_more'])

    def test_quality_rejects_invalid_parameters(self):
        app = build_app(fixture_tables())
        for query in ('denominator=all', 'topic=trailer', 'category=BROKEN',
                      'limit=0', 'limit=101', 'offset=-1', 'top=0', 'top=51'):
            with self.subTest(query=query):
                with signed_in(True):
                    response = app.test_client().get(
                        '/api/admin/tmdb-supplement/quality?' + query)
                self.assertEqual(400, response.status_code)
                self.assertEqual('INVALID_INPUT', response.get_json()['code'])

    def test_quality_requires_authentication(self):
        app = build_app(fixture_tables())
        with signed_in(False):
            response = app.test_client().get('/api/admin/tmdb-supplement/quality')
        self.assertEqual(401, response.status_code)

    def test_quality_data_unavailable_when_catalog_fails(self):
        app = build_app(fixture_tables())
        with signed_in(True), mock.patch.object(
                repository, 'load_catalog_counts',
                side_effect=repository.RepositoryError('offline')):
            response = app.test_client().get('/api/admin/tmdb-supplement/quality')
        self.assertEqual(503, response.status_code)
        self.assertEqual('DATA_UNAVAILABLE', response.get_json()['code'])


class BatchesEndpointTests(unittest.TestCase):
    def test_batches_page_reports_missing_counts_as_null(self):
        app = build_app(fixture_tables())
        with signed_in(True):
            response = app.test_client().get(
                '/api/admin/tmdb-supplement/batches?limit=10')
        self.assertEqual(200, response.status_code)
        data = response.get_json()['data']
        self.assertEqual(2, data['total'])
        self.assertTrue(data['has_more'] is False)
        items = {item['batch_id']: item for item in data['items']}
        self.assertEqual(3, items[501]['reported_counts']['success_count'])
        self.assertEqual(5, items[501]['reported_counts']['executed_operations'])
        self.assertEqual({'inserted': 1, 'updated': 1, 'skipped': 1,
                          'review': 0, 'error': 2, 'total': 5},
                         items[501]['item_status'])
        self.assertEqual([{'error_code': 'HTTP_429', 'count': 1},
                          {'error_code': 'HTTP_500', 'count': 1}],
                         sorted(items[501]['error_codes'],
                                key=lambda row: row['error_code']))
        self.assertEqual([], items[501]['missing_fields'])

        failed = items[502]
        self.assertIsNone(failed['reported_counts']['target_documents'])
        self.assertIsNone(failed['reported_counts']['success_count'])
        self.assertIsNone(failed['item_status'])
        self.assertIn('target_documents', failed['missing_fields'])
        self.assertIn('item_status', failed['missing_fields'])
        self.assertEqual('failed', failed['status'])

    def test_batches_status_filter_and_pagination(self):
        app = build_app(fixture_tables())
        with signed_in(True):
            failed = app.test_client().get(
                '/api/admin/tmdb-supplement/batches?status=failed')
            page = app.test_client().get(
                '/api/admin/tmdb-supplement/batches?limit=1&offset=1')
        failed_data = failed.get_json()['data']
        self.assertEqual(1, failed_data['total'])
        self.assertEqual(502, failed_data['items'][0]['batch_id'])
        page_data = page.get_json()['data']
        self.assertEqual(1, page_data['returned'])
        self.assertEqual(1, page_data['offset'])
        self.assertTrue(page_data['has_more'] is False)

    def test_batches_rejects_invalid_parameters(self):
        app = build_app(fixture_tables())
        for query in ('status=broken', 'limit=0', 'limit=101', 'offset=-1',
                      'started_from=nope', 'source=bad source'):
            with self.subTest(query=query):
                with signed_in(True):
                    response = app.test_client().get(
                        '/api/admin/tmdb-supplement/batches?' + query)
                self.assertEqual(400, response.status_code)
                self.assertEqual('INVALID_INPUT', response.get_json()['code'])

    def test_batches_requires_authentication(self):
        app = build_app(fixture_tables())
        with signed_in(False):
            response = app.test_client().get('/api/admin/tmdb-supplement/batches')
        self.assertEqual(401, response.status_code)

    def test_batches_data_unavailable_on_repository_error(self):
        app = build_app(fixture_tables())
        with signed_in(True), mock.patch.object(
                repository, 'load_batches',
                side_effect=repository.RepositoryError('offline')):
            response = app.test_client().get('/api/admin/tmdb-supplement/batches')
        self.assertEqual(503, response.status_code)
        self.assertEqual('DATA_UNAVAILABLE', response.get_json()['code'])


if __name__ == '__main__':
    unittest.main()