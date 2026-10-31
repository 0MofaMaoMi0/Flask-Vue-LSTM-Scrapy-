"""新目录接口的参数校验回归测试。

只覆盖「在查库之前就要拦截」的分支，避免单元测试依赖真实数据库；
正常取数路径由联调脚本对着运行中的服务验证。
"""

import os
import sys
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
from api import catalogApi


class CatalogValidationTest(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def _code(self, path):
        payload = self.client.get(path).get_json()
        return payload.get("code")

    def test_list_rejects_bad_page(self):
        self.assertEqual(40002, self._code("/movie/list?page=abc"))
        self.assertEqual(40002, self._code("/movie/list?page=0"))
        self.assertEqual(40002, self._code("/movie/list?size=999"))

    def test_list_rejects_unknown_sort(self):
        self.assertEqual(40002, self._code("/movie/list?sort=bogus"))

    def test_list_rejects_inverted_year_range(self):
        self.assertEqual(
            40002, self._code("/movie/list?year_from=2020&year_to=2000")
        )

    def test_detail_rejects_non_numeric_id(self):
        self.assertEqual(40002, self._code("/movie/detail/abc"))

    def test_comments_requires_numeric_douban_id(self):
        self.assertEqual(40002, self._code("/movie/comments"))
        self.assertEqual(40002, self._code("/movie/comments?douban_id=abc"))

    def test_comments_rejects_unknown_label(self):
        self.assertEqual(
            40002, self._code("/movie/comments?douban_id=1292052&label=bad")
        )

    def test_paged_payload_shape(self):
        payload = catalogApi._paged_payload([1, 2, 3], 100, 2, 3)
        self.assertEqual(
            {"items": [1, 2, 3], "total": 100, "page": 2, "size": 3, "pages": 34},
            payload,
        )

    def test_list_sorts_include_expected_keys(self):
        self.assertEqual(
            {"score", "votes", "year", "name"}, set(catalogApi.LIST_SORTS)
        )


if __name__ == "__main__":
    unittest.main()