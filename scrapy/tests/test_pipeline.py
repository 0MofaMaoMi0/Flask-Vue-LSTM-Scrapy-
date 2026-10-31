import unittest

from douban.items import MovieMeta, Subject
from douban.pipelines import CoverPipeline, DoubanPipeline


class FakeConnection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeCursor:
    def __init__(self, existing=False):
        self.existing = existing
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))

    def fetchone(self):
        return {'id': 1} if self.existing else None


class FakeLogger:
    def exception(self, *args, **kwargs):
        pass


class FakeSpider:
    logger = FakeLogger()


class PipelineTests(unittest.TestCase):
    def make_pipeline(self, existing=False):
        pipeline = DoubanPipeline()
        pipeline.connection = FakeConnection()
        pipeline.cursor = FakeCursor(existing)
        return pipeline

    def test_subject_lookup_and_insert_use_bound_parameters(self):
        pipeline = self.make_pipeline(existing=False)
        item = Subject(douban_id='1292052', type='movie')

        result = pipeline.process_item(item, FakeSpider())

        self.assertIs(result, item)
        self.assertEqual(('1292052',), pipeline.cursor.calls[0][1])
        self.assertIn('WHERE `douban_id`=%s', pipeline.cursor.calls[0][0])
        self.assertEqual(('1292052', 'movie'), pipeline.cursor.calls[1][1])
        self.assertEqual(1, pipeline.connection.commits)

    def test_movie_update_does_not_remove_item_key(self):
        pipeline = self.make_pipeline(existing=True)
        item = MovieMeta(
            douban_id='1292052',
            name='  Movie Name  ',
            type='movie',
        )

        pipeline.process_item(item, FakeSpider())

        self.assertEqual('1292052', item['douban_id'])
        update_sql, values = pipeline.cursor.calls[1]
        self.assertIn('WHERE `douban_id`=%s', update_sql)
        self.assertEqual(('Movie Name', 'movie', '1292052'), values)

    def test_movie_insert_supplies_missing_legacy_score_defaults(self):
        pipeline = self.make_pipeline(existing=False)
        item = MovieMeta(
            douban_id='1292052',
            name='Movie Name',
            douban_score='9.7',
        )

        pipeline.process_item(item, FakeSpider())

        insert_sql, values = pipeline.cursor.calls[1]
        self.assertIn('`avg_score`', insert_sql)
        self.assertIn('`imdb_score`', insert_sql)
        self.assertEqual(('1292052', 'Movie Name', '9.7', 0.0, 0.0), values)


class CoverPipelineTests(unittest.TestCase):
    def make_pipeline(self):
        pipeline = object.__new__(CoverPipeline)
        pipeline.public_prefix = '/api/media/covers'
        return pipeline

    def test_success_keeps_source_and_builds_versioned_local_url(self):
        pipeline = self.make_pipeline()
        item = MovieMeta(
            douban_id='1292052',
            cover='https://img.example/poster.jpg',
            cover_source_url='https://img.example/poster.jpg',
        )

        result = pipeline.item_completed([(
            True,
            {
                'path': 'ab/cd/poster.webp',
                'checksum': '1234567890abcdef',
            },
        )], item, None)

        self.assertEqual('https://img.example/poster.jpg', result['cover_source_url'])
        self.assertEqual('ab/cd/poster.webp', result['cover_path'])
        self.assertEqual('ok', result['cover_status'])
        self.assertEqual(
            '/api/media/covers/ab/cd/poster.webp?v=1234567890ab',
            result['cover'],
        )

    def test_failure_preserves_source_when_no_cached_file_exists(self):
        pipeline = self.make_pipeline()
        item = MovieMeta(
            douban_id='1292052',
            cover='https://img.example/poster.jpg',
            cover_source_url='https://img.example/poster.jpg',
        )

        result = pipeline.item_completed([], item, None)

        self.assertEqual('failed', result['cover_status'])
        self.assertEqual('https://img.example/poster.jpg', result['cover'])

    def test_download_errback_result_is_recorded(self):
        pipeline = self.make_pipeline()
        item = MovieMeta(
            douban_id='1292052',
            cover='https://img.example/poster.jpg',
            cover_source_url='https://img.example/poster.jpg',
        )

        result = pipeline.item_completed([(
            True,
            {'poster_error': 'TLS connection closed'},
        )], item, None)

        self.assertEqual('failed', result['cover_status'])
        self.assertEqual('TLS connection closed', result['cover_error'])
        self.assertEqual('https://img.example/poster.jpg', result['cover'])

    def test_empty_download_error_uses_generic_message(self):
        pipeline = self.make_pipeline()
        item = MovieMeta(
            douban_id='1292052',
            cover_source_url='https://img.example/poster.jpg',
        )

        result = pipeline.item_completed([(
            True,
            {'poster_error': ''},
        )], item, None)

        self.assertEqual('failed', result['cover_status'])
        self.assertEqual('poster download failed', result['cover_error'])

    def test_failure_keeps_previous_cached_file(self):
        pipeline = self.make_pipeline()
        item = MovieMeta(
            douban_id='1292052',
            cover='https://img.example/poster.jpg',
            cover_source_url='https://img.example/poster.jpg',
            cover_path='ab/cd/poster.webp',
            cover_hash='abcdef1234567890',
        )

        result = pipeline.item_completed([], item, None)

        self.assertEqual('stale', result['cover_status'])
        self.assertEqual(
            '/api/media/covers/ab/cd/poster.webp?v=abcdef123456',
            result['cover'],
        )


if __name__ == '__main__':
    unittest.main()

