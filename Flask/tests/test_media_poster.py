"""海报媒体接口回归测试：WebP 必须以 image/webp 返回，否则 nosniff 下浏览器不渲染。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path


FLASK_ROOT = Path(__file__).resolve().parents[1]
if str(FLASK_ROOT) not in sys.path:
    sys.path.insert(0, str(FLASK_ROOT))

os.environ.setdefault("MYSQL_HOST", "127.0.0.1")
os.environ.setdefault("MYSQL_PORT", "3306")
os.environ.setdefault("MYSQL_USER", "smoke")
os.environ.setdefault("MYSQL_PASSWORD", "smoke")
os.environ.setdefault("MYSQL_DATABASE", "smoke")
os.environ.setdefault("SECRET_KEY", "smoke-test-only")

import app as app_module


WEBP_BYTES = b"RIFF" + (36).to_bytes(4, "little") + b"WEBPVP8 " + b"\x00" * 32


class CoverMediaTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        target = self.root / "ab" / "cd" / "poster.webp"
        target.parent.mkdir(parents=True)
        target.write_bytes(WEBP_BYTES)

        self._original_root = app_module.app.config.get("POSTER_STORAGE_ROOT")
        app_module.app.config.update(
            TESTING=True, POSTER_STORAGE_ROOT=str(self.root)
        )
        self.client = app_module.app.test_client()

    def tearDown(self):
        if self._original_root is not None:
            app_module.app.config.update(POSTER_STORAGE_ROOT=self._original_root)
        self._tmp.cleanup()

    def test_webp_is_served_as_image_webp(self):
        response = self.client.get("/media/covers/ab/cd/poster.webp")
        self.assertEqual(200, response.status_code)
        # mimetypes 库在部分环境下不认识 .webp，接口必须显式声明类型。
        self.assertEqual("image/webp", response.headers.get("Content-Type"))
        self.assertEqual("nosniff", response.headers.get("X-Content-Type-Options"))
        self.assertIn("immutable", response.headers.get("Cache-Control", ""))
        self.assertEqual(WEBP_BYTES, response.data)
        response.close()

    def test_non_webp_suffix_is_rejected(self):
        (self.root / "ab" / "cd" / "poster.jpg").write_bytes(b"not a poster")
        response = self.client.get("/media/covers/ab/cd/poster.jpg")
        self.assertEqual(404, response.status_code)


if __name__ == "__main__":
    unittest.main()