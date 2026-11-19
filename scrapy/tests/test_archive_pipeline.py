import json
import os
import shutil
import sys
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from douban.items import Comment, MovieMeta, PersonMeta
from douban.pipelines import ArchiveEnvelopePipeline, DoubanPipeline
from scrapy.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / 'backend'
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from catalog import data_pipeline  # noqa: E402
from catalog.ingest_hub import HubSecurityError  # noqa: E402


class FakeLogger:
    def __init__(self):
        self.infos = []

    def info(self, msg, *args):
        self.infos.append(msg % args if args else msg)

    def exception(self, *args, **kwargs):
        pass


class FakeSpider:
    name = 'movie_meta'

    def __init__(self, settings=None, archive_job_id=None):
        self.logger = FakeLogger()
        self.settings = settings
        if archive_job_id is not None:
            self.archive_job_id = archive_job_id


class FakeCrawler:
    def __init__(self, settings_dict):
        self.settings = Settings(settings_dict)


@contextmanager
def non_c_archive():
    base = REPO_ROOT / '.local-cache' / 'test-scrapy-archive'
    base.mkdir(parents=True, exist_ok=True)
    root = base / f'run-{uuid.uuid4().hex[:10]}'
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


class ArchiveEnvelopePipelineTests(unittest.TestCase):
    def test_archive_only_mode_skips_mysql_and_archives_items(self):
        with non_c_archive() as root:
            crawler = FakeCrawler({
                'DOUBAN_ARCHIVE_DIR': str(root),
                'DOUBAN_ARCHIVE_JOB_ID': 'scrapy-offline-01',
                'DOUBAN_ARCHIVE_ONLY': True,
            })
            spider = FakeSpider(settings=crawler.settings)

            with mock.patch('douban.database.get_connection', side_effect=AssertionError('MySQL must not be called')):
                db_pipe = DoubanPipeline.from_crawler(crawler)
                arch_pipe = ArchiveEnvelopePipeline.from_crawler(crawler)

                db_pipe.open_spider(spider)
                arch_pipe.open_spider(spider)

                movie = MovieMeta(
                    douban_id='1292052',
                    type='movie',
                    name='肖申克的救赎',
                    year='1994',
                    release_date='1994-09-10(多伦多电影节)',
                    mins='142分钟',
                    genres='犯罪/剧情',
                    languages='英语',
                    directors='弗兰克·德拉邦特',
                    director_ids='弗兰克·德拉邦特:1047973',
                    imdb_id='tt0111161',
                    douban_score='9.7',
                    douban_votes='3000000',
                )
                person = PersonMeta(person_id='1047973', name='弗兰克·德拉邦特', profession='导演')
                comment = Comment(
                    douban_id='1292052',
                    douban_comment_id='c-1',
                    douban_user_nickname='测试用户',
                    content='经典之作',
                    votes=10,
                    rating='5',
                    comment_time='2026-11-19 10:00:00',
                )

                for item in (movie, person, comment):
                    db_pipe.process_item(item, spider)
                    arch_pipe.process_item(item, spider)

                result = arch_pipe.close_spider(spider)
                db_pipe.close_spider(spider)

            self.assertIsNotNone(result)
            self.assertEqual({'movies': 1, 'persons': 1, 'comments': 1, 'skipped': 0}, result['counts'])
            job_dir = Path(result['archive_dir'])
            self.assertTrue((job_dir / 'envelope.json').is_file())
            self.assertTrue((job_dir / 'manifest.json').is_file())
            self.assertTrue((job_dir / 'supplementary' / 'persons.jsonl').is_file())
            self.assertTrue((job_dir / 'supplementary' / 'comments.jsonl').is_file())

            envelope = json.loads((job_dir / 'envelope.json').read_text(encoding='utf-8'))
            built = data_pipeline.build(envelope)
            self.assertEqual(1, built['quality']['accepted_movies'])
            self.assertEqual(0, built['quality']['rejected'])
            self.assertEqual(0, built['quality']['quarantined'])
            manifest = json.loads((job_dir / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(1, manifest['supplementary']['persons']['count'])
            self.assertEqual(1, manifest['supplementary']['comments']['count'])
            self.assertIn('envelope.json', result['file_hashes'])
            self.assertIn('supplementary/persons.jsonl', result['file_hashes'])
            self.assertIn('supplementary/comments.jsonl', result['file_hashes'])

    def test_archive_pipeline_rejects_c_drive_path(self):
        crawler = FakeCrawler({'DOUBAN_ARCHIVE_DIR': r'C:\Users\20876\forbidden-archive'})
        with self.assertRaises(HubSecurityError) as ctx:
            ArchiveEnvelopePipeline.from_crawler(crawler)
        self.assertEqual('ARCHIVE_ON_SYSTEM_DRIVE', ctx.exception.code)

        pipe = ArchiveEnvelopePipeline(archive_dir=r'C:\Users\20876\forbidden-archive')
        with self.assertRaises(HubSecurityError) as ctx2:
            pipe.open_spider(FakeSpider())
        self.assertEqual('ARCHIVE_ON_SYSTEM_DRIVE', ctx2.exception.code)

    def test_archive_pipeline_disabled_by_default_and_env_supported(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('MI2_DOUBAN_ARCHIVE_DIR', None)
            os.environ.pop('MI2_DOUBAN_ARCHIVE_ONLY', None)
            crawler = FakeCrawler({})
            pipe = ArchiveEnvelopePipeline.from_crawler(crawler)
            spider = FakeSpider()
            pipe.open_spider(spider)
            self.assertFalse(pipe.enabled)
            item = MovieMeta(douban_id='1', name='Test')
            self.assertIs(pipe.process_item(item, spider), item)
            self.assertIsNone(pipe.close_spider(spider))


if __name__ == '__main__':
    unittest.main()
