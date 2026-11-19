"""Offline checks for published-snapshot TMDB heat, without DB or network."""
from datetime import datetime, timezone
import unittest
from flask import Flask
from catalog.api_read import create_read_blueprint
from catalog.tmdb_heat import NOT_FOUND, OK, STALE, STALE_AFTER_SECONDS, heat_for

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def stamp(hour, minute=0, zone=timezone.utc):
    return datetime(2026, 11, 16, hour, minute, tzinfo=zone).isoformat()


def snapshot(captured, items):
    return {'schema': 1, 'captured_at': captured, 'items': items}


def reader(snapshots, calls=None, errors=()):
    calls = calls if calls is not None else []

    def read(channel, online=True):
        calls.append((channel, online))
        assert online is False, 'heat lookup must never request an online refresh'
        if channel in errors:
            raise RuntimeError('broken snapshot')
        return snapshots.get(channel)

    return read


class HeatFunctionTests(unittest.TestCase):
    def test_latest_channel_wins_and_equal_times_keep_public_order(self):
        snapshots = {
            'popular': snapshot(stamp(9), [{'id': 42, 'popularity': 1.5}]),
            'trending_day': snapshot(stamp(10), [{'id': 42, 'popularity': 2.5}]),
            'trending_week': snapshot(stamp(11), [{'id': 42, 'popularity': 3.5}]),
        }
        result = heat_for(42, read_snapshot=reader(snapshots), now=NOW)
        self.assertEqual(result['status'], OK)
        self.assertEqual(result['popularity'], 3.5)
        self.assertEqual(result['source'], 'trending_week')
        self.assertEqual(result['observed_at'], stamp(11))

        tied = {
            'popular': snapshot(stamp(10), [{'id': 42, 'popularity': 1.5}]),
            'trending_day': snapshot(stamp(10), [{'id': 42, 'popularity': 2.5}]),
        }
        result = heat_for(42, read_snapshot=reader(tied), now=NOW)
        self.assertEqual((result['popularity'], result['source']), (1.5, 'popular'))

    def test_zero_is_valid_but_bad_popularity_is_never_zero(self):
        invalid = [
            {'id': 42, 'popularity': True},
            {'id': 42, 'popularity': None},
            {'id': 42, 'popularity': '12.5'},
            {'id': 42, 'popularity': float('nan')},
            {'id': 42, 'popularity': float('inf')},
            {'id': 42, 'popularity': -1},
        ]
        result = heat_for(42, read_snapshot=reader({'popular': snapshot(stamp(11), invalid + [{'id': 42, 'popularity': 0}])}), now=NOW)
        self.assertEqual((result['status'], result['popularity']), (OK, 0.0))

        missing = heat_for(42, read_snapshot=reader({'popular': snapshot(stamp(11), invalid)}), now=NOW)
        self.assertEqual(missing['status'], NOT_FOUND)
        self.assertIsNone(missing['popularity'])

    def test_invalid_item_ids_are_skipped_before_matching(self):
        items = [
            {'id': True, 'popularity': 99},
            {'id': '42', 'popularity': 98},
            {'id': 42.0, 'popularity': 97},
            {'id': 0, 'popularity': 96},
            {'id': -1, 'popularity': 95},
            {'id': 42, 'popularity': 7},
        ]
        result = heat_for(42, read_snapshot=reader({'popular': snapshot(stamp(11), items)}), now=NOW)
        self.assertEqual((result['status'], result['popularity']), (OK, 7.0))

    def test_exactly_six_hours_is_ok_and_one_second_over_is_stale(self):
        at_limit = heat_for(42, read_snapshot=reader({'popular': snapshot(stamp(6), [{'id': 42, 'popularity': 4}])}), now=NOW)
        self.assertEqual(at_limit['status'], OK)
        over_limit = heat_for(42, read_snapshot=reader({'popular': snapshot(stamp(5, 59), [{'id': 42, 'popularity': 4}])}), now=NOW)
        self.assertEqual(over_limit['status'], STALE)

    def test_newer_channel_without_movie_falls_back_to_older_valid_channel(self):
        snapshots = {
            'popular': snapshot(stamp(11, 30), [{'id': 7, 'popularity': 99}]),
            'trending_day': snapshot(stamp(9), [{'id': 42, 'popularity': 8}]),
        }
        result = heat_for(42, read_snapshot=reader(snapshots), now=NOW)
        self.assertEqual((result['status'], result['popularity'], result['source']), (OK, 8.0, 'trending_day'))

    def test_six_hour_staleness_and_invalid_time_are_explicit(self):
        result = heat_for(42, read_snapshot=reader({'popular': snapshot(stamp(5), [{'id': 42, 'popularity': 4}])}), now=NOW)
        self.assertEqual(result['status'], STALE)
        self.assertTrue(result['stale'])
        self.assertGreater(result['age_seconds'], STALE_AFTER_SECONDS)

        for captured in ('2026-11-16T11:00:00', 'not-a-time', None):
            result = heat_for(42, read_snapshot=reader({'popular': snapshot(captured, [{'id': 42, 'popularity': 4}])}), now=NOW)
            self.assertEqual(result['status'], NOT_FOUND)

    def test_bad_channel_does_not_hide_other_channel(self):
        snapshots = {'trending_week': snapshot(stamp(11), [{'id': 42, 'popularity': 6}])}
        calls = []
        result = heat_for(42, read_snapshot=reader(snapshots, calls=calls, errors=('popular',)), now=NOW)
        self.assertEqual((result['status'], result['popularity']), (OK, 6.0))
        self.assertEqual([online for _, online in calls], [False, False, False])

    def test_malformed_snapshot_is_skipped(self):
        snapshots = {
            'popular': {'captured_at': stamp(11), 'items': 'bad'},
            'trending_day': snapshot(stamp(10), [{'id': 42, 'popularity': 5}]),
        }
        result = heat_for(42, read_snapshot=reader(snapshots), now=NOW)
        self.assertEqual((result['status'], result['source']), (OK, 'trending_day'))


class ApiBoundaryTests(unittest.TestCase):
    def app(self, snapshots=None, errors=(), detail_rows=False):
        calls = []
        executor_calls = []
        external = [
            {'id': 1, 'movie_id': 7, 'source': 'tmdb', 'external_id': '42'},
            {'id': 2, 'movie_id': 7, 'source': 'imdb', 'external_id': 'tt42'},
        ]
        movie = {'movie_id': 7, 'title': 'Synthetic', 'catalog_status': 'active', 'release_year': 2026}

        def execute(stmt):
            executor_calls.append(stmt)
            sql = str(stmt)
            if detail_rows and 'movie_external_ids' in sql:
                return external
            if detail_rows and 'FROM movies' in sql and 'movie_external_ids' not in sql:
                return [movie]
            return []

        app = Flask(__name__)
        app.register_blueprint(create_read_blueprint(execute, heat_reader=reader(snapshots or {}, calls=calls, errors=errors), heat_now=NOW))
        return app.test_client(), calls, executor_calls

    def test_direct_endpoint_reads_only_snapshot_and_never_executes_sql(self):
        client, calls, executor_calls = self.app({'popular': snapshot(stamp(11), [{'id': 42, 'popularity': 12.5}])})
        response = client.get('/api/catalog/tmdb/42/heat')
        self.assertEqual(response.status_code, 200)
        data = response.json['data']
        self.assertEqual((data['status'], data['popularity'], data['source']), (OK, 12.5, 'popular'))
        self.assertEqual(executor_calls, [])
        self.assertEqual([online for _, online in calls], [False, False, False])

    def test_head_is_safe_and_invalid_arguments_are_rejected(self):
        client, _, executor_calls = self.app({'popular': snapshot(stamp(11), [{'id': 42, 'popularity': 12.5}])})
        head = client.head('/api/catalog/tmdb/42/heat')
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.data, b'')
        for path in ('/api/catalog/tmdb/42/heat?foo=1', '/api/catalog/tmdb/42/heat?tmdb_id=42', '/api/catalog/tmdb/0/heat'):
            self.assertEqual(client.get(path).status_code, 400)
        self.assertEqual(client.post('/api/catalog/tmdb/42/heat').status_code, 405)
        self.assertEqual(executor_calls, [])

    def test_not_found_has_its_own_code_and_clear_status(self):
        client, _, _ = self.app({})
        response = client.get('/api/catalog/tmdb/42/heat')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json['code'], 'TMDB_HEAT_NOT_FOUND')
        self.assertEqual(response.json['data']['status'], NOT_FOUND)
        self.assertIsNone(response.json['data']['popularity'])

    def test_broken_snapshot_does_not_break_endpoint_or_detail(self):
        broken_client, _, _ = self.app(errors=('popular', 'trending_day', 'trending_week'))
        self.assertEqual(broken_client.get('/api/catalog/tmdb/42/heat').status_code, 404)
        detail_client, _, _ = self.app({'popular': snapshot(stamp(11), [{'id': 42, 'popularity': 2}])}, detail_rows=True)
        response = detail_client.get('/api/catalog/movies/7')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data']['tmdb_heat']['popularity'], 2.0)
        broken_detail, _, _ = self.app(errors=('popular', 'trending_day', 'trending_week'), detail_rows=True)
        response = broken_detail.get('/api/catalog/movies/7')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json['data']['tmdb_heat'])

    def test_detail_maps_tmdb_external_id_and_keeps_existing_fields(self):
        client, calls, _ = self.app({'popular': snapshot(stamp(11), [{'id': 42, 'popularity': 2}])}, detail_rows=True)
        response = client.get('/api/catalog/movies/7')
        self.assertEqual(response.status_code, 200)
        data = response.json['data']
        self.assertEqual(data['movie_id'], 7)
        self.assertEqual(data['external_ids'], [
            {'source': 'tmdb', 'external_id': '42'},
            {'source': 'imdb', 'external_id': 'tt42'},
        ])
        self.assertEqual((data['tmdb_heat']['status'], data['tmdb_heat']['observed_at']), (OK, stamp(11)))
        self.assertEqual([online for _, online in calls], [False, False, False])

    def test_detail_without_tmdb_mapping_has_null_heat(self):
        app = Flask(__name__)

        def execute(stmt):
            sql = str(stmt)
            if 'movie_external_ids' in sql:
                return [{'id': 1, 'movie_id': 7, 'source': 'imdb', 'external_id': 'tt42'}]
            if 'FROM movies' in sql:
                return [{'movie_id': 7, 'title': 'Synthetic', 'catalog_status': 'active'}]
            return []

        calls = []
        app.register_blueprint(create_read_blueprint(execute, heat_reader=reader({}, calls=calls), heat_now=NOW))
        response = app.test_client().get('/api/catalog/movies/7')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json['data']['tmdb_heat'])
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()