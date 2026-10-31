import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from douban.import_posters import (
    discover_posters,
    import_posters,
    store_poster,
)


class ImportPosterTests(unittest.TestCase):
    def test_discovery_requires_numeric_movie_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new('RGB', (20, 30), 'red').save(root / '1292052.jpg')
            Image.new('RGB', (20, 30), 'blue').save(root / 'poster.jpg')

            posters = discover_posters(root)

            self.assertEqual(['1292052'], list(posters))

    def test_store_converts_and_limits_webp(self):
        with tempfile.TemporaryDirectory() as source_directory:
            with tempfile.TemporaryDirectory() as storage_directory:
                source = Path(source_directory) / '1292052.png'
                Image.new('RGBA', (1200, 1800), (0, 0, 255, 128)).save(source)

                metadata = store_poster(
                    source,
                    storage_directory,
                    '/api/media/covers',
                )

                target = Path(storage_directory) / metadata['cover_path']
                self.assertTrue(target.is_file())
                self.assertEqual(b'RIFF', target.read_bytes()[:4])
                self.assertLessEqual(metadata['width'], 360)
                self.assertLessEqual(metadata['height'], 540)
                self.assertTrue(metadata['cover'].startswith(
                    '/api/media/covers/'
                ))

    def test_dry_run_validates_without_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new('RGB', (200, 300), 'green').save(root / '1292052.jpg')
            (root / '1295644.jpg').write_text('not an image', encoding='utf-8')

            result = import_posters(root, limit=100, apply=False)

            self.assertEqual(2, result['discovered'])
            self.assertEqual(1, result['validated'])
            self.assertEqual(1, result['invalid_images'])
            self.assertEqual(0, result['imported'])

    def test_apply_updates_raw_and_application_tables(self):
        class FakeCursor:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params=()):
                self.calls.append((sql, params))

            def fetchone(self):
                return {'id': 1}

        class FakeConnection:
            def __init__(self):
                self.cursor_instance = FakeCursor()
                self.commits = 0

            def cursor(self):
                return self.cursor_instance

            def commit(self):
                self.commits += 1

            def rollback(self):
                pass

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as source_directory:
            with tempfile.TemporaryDirectory() as storage_directory:
                source = Path(source_directory) / '1292052.jpg'
                Image.new('RGB', (200, 300), 'green').save(source)
                connection = FakeConnection()
                with patch(
                    'douban.import_posters.get_connection',
                    return_value=connection,
                ), patch(
                    'douban.import_posters.get_poster_storage_root',
                    return_value=storage_directory,
                ), patch(
                    'douban.import_posters.get_poster_public_prefix',
                    return_value='/api/media/covers',
                ):
                    result = import_posters(
                        source_directory, limit=1, apply=True
                    )

                self.assertEqual(1, result['imported'])
                self.assertEqual(1, connection.commits)
                update_sql = [
                    sql for sql, _ in connection.cursor_instance.calls
                    if sql.startswith('UPDATE')
                ]
                self.assertEqual(2, len(update_sql))
                self.assertIn('`movies`', update_sql[0])
                self.assertIn('`movies2`', update_sql[1])


if __name__ == '__main__':
    unittest.main()
