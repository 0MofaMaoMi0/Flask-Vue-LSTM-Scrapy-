"""Database-free smoke checks for routes that must remain locally callable."""

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

from app import app


class ApiSmokeTest(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_health_route_returns_standard_response(self):
        response = self.client.get("/test")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["name"], "zhang")

    def test_blueprint_test_route_does_not_require_database(self):
        response = self.client.get("/test/test2")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["code"], 0)
        self.assertEqual(body["data"]["name"], "zhang")
