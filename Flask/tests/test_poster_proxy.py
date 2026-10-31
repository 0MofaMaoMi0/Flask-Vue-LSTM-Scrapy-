"""海报回源代理的回归测试：本地图与 TMDB 只做跳转，豆瓣图才回源。"""

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


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
from api import posterProxy


class _FakeResponse:
    def __init__(self, status_code=200, body=b"image-bytes", content_type="image/jpeg"):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self._body = body
        self.raw = SimpleNamespace(read=lambda size, decode_content=True: self._body)

    def close(self):
        pass


class PosterProxyTest(unittest.TestCase):
    def setUp(self):
        posterProxy._host_next_slot.clear()
        posterProxy._inflight.clear()
        self.client = app_module.app.test_client()
        app_module.app.config.update(
            TESTING=True,
            POSTER_PROXY_CACHE_MB=0,
            POSTER_PROXY_USER_AGENT="test-agent",
        )

    def _patch_row(self, cover="", source=""):
        row = SimpleNamespace(
            douban_id="1292052", cover=cover, cover_source_url=source
        )
        fake_db = mock.MagicMock()
        fake_db.session.query.return_value.filter.return_value.first.return_value = row
        return mock.patch.object(posterProxy, "db", fake_db)

    def test_local_webp_redirects_without_touching_upstream(self):
        with self._patch_row(cover="/api/media/covers/a6/30/x.webp?v=1"):
            with mock.patch.object(posterProxy.requests, "get") as get:
                response = self.client.get("/media/poster/1292052")
        self.assertEqual(302, response.status_code)
        self.assertIn("/api/media/covers/a6/30/x.webp", response.headers["Location"])
        get.assert_not_called()

    def test_tmdb_source_redirects_to_cdn(self):
        source = "https://image.tmdb.org/t/p/w500/aAdnwqwk.jpg"
        with self._patch_row(cover="https://img2.doubanio.com/x.jpg", source=source):
            with mock.patch.object(posterProxy.requests, "get") as get:
                response = self.client.get("/media/poster/1292052")
        self.assertEqual(302, response.status_code)
        self.assertEqual(source, response.headers["Location"])
        get.assert_not_called()

    def test_douban_cover_is_fetched_with_referer_and_cached_headers(self):
        cover = "https://img2.doubanio.com/view/photo/p480747492.jpg"
        with self._patch_row(cover=cover):
            with mock.patch.object(
                posterProxy.requests, "get", return_value=_FakeResponse()
            ) as get:
                response = self.client.get("/media/poster/1292052")
        self.assertEqual(200, response.status_code)
        self.assertEqual("image/jpeg", response.headers["Content-Type"])
        self.assertIn("immutable", response.headers["Cache-Control"])
        self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
        referer = get.call_args.kwargs["headers"]["Referer"]
        self.assertEqual(
            "https://movie.douban.com/subject/1292052/", referer
        )

    def test_upstream_failure_becomes_502(self):
        cover = "https://img2.doubanio.com/view/photo/p480747492.jpg"
        with self._patch_row(cover=cover):
            with mock.patch.object(
                posterProxy.requests,
                "get",
                return_value=_FakeResponse(status_code=418),
            ):
                response = self.client.get("/media/poster/1292052")
        self.assertEqual(502, response.status_code)

    def test_non_numeric_id_is_404(self):
        response = self.client.get("/media/poster/not-a-number")
        self.assertEqual(404, response.status_code)

    def test_missing_row_is_404(self):
        with self._patch_row() as _:
            posterProxy.db.session.query.return_value.filter.return_value.first.return_value = None
            response = self.client.get("/media/poster/999999")
        self.assertEqual(404, response.status_code)

    def test_non_douban_host_is_not_proxied(self):
        with self._patch_row(cover="https://evil.example/poster.jpg"):
            with mock.patch.object(posterProxy.requests, "get") as get:
                response = self.client.get("/media/poster/1292052")
        self.assertEqual(404, response.status_code)
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()