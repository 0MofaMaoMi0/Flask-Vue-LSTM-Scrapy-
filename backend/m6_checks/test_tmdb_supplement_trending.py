"""Offline tests for the bounded day/week trending capture."""
import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from catalog.tmdb_supplement import trending
from catalog.tmdb_supplement.capture import CaptureSecurityError, _validate_request


def body(page, ids, total_pages=2):
    payload = {"page": page, "total_pages": total_pages,
               "results": [{"id": i, "title": "t%d" % i, "release_date": "2026-01-01"}
                           for i in ids]}
    return json.dumps(payload).encode("utf-8")


class ScriptedFetch:
    def __init__(self, script=None, default_status=200):
        self.requests = []
        self.script = list(script or [])
        self.default_status = default_status

    def __call__(self, request):
        _validate_request(request)
        self.requests.append(request)
        status = self.default_status
        if self.script:
            status = self.script.pop(0)
        page = int(request["url"].rsplit("page=", 1)[1])
        ids = list(range(page * 100, page * 100 + 20))
        return {"status": status, "headers": {}, "body": body(page, ids),
                "url": request["url"]}


class RequestShapeTests(unittest.TestCase):
    def test_request_url_is_fixed_and_validated(self):
        request = trending.build_request("trending_day", 2, "x" * 40)
        self.assertEqual(
            request["url"],
            "https://api.themoviedb.org/3/trending/movie/day?language=zh-CN&page=2")
        url, headers, timeout, max_bytes = _validate_request(request)
        self.assertEqual(set(headers), {"Accept", "Authorization", "User-Agent"})
        self.assertEqual(timeout, trending.DEFAULT_TIMEOUT)

    def test_only_two_channels_and_pages(self):
        for channel in ("trending_day", "trending_week"):
            trending.build_request(channel, 1, "x" * 40)
        for channel, page in (("popular", 1), ("trending_day", 3), ("trending_day", 0)):
            with self.subTest(channel=channel, page=page):
                with self.assertRaises(trending.TrendingInputError):
                    trending.build_request(channel, page, "x" * 40)

    def test_transport_rejects_foreign_trending_urls(self):
        for url in ("https://api.themoviedb.org/3/trending/movie/day?language=zh-CN&page=3",
                    "https://api.themoviedb.org/3/trending/tv/day?language=zh-CN&page=1",
                    "http://api.themoviedb.org/3/trending/movie/day?language=zh-CN&page=1",
                    "https://api.themoviedb.org/3/movie/278?language=zh-CN&page=1"):
            with self.subTest(url=url):
                with self.assertRaises(CaptureSecurityError):
                    _validate_request({"kind": "trending", "url": url,
                                       "headers": {"Accept": "application/json",
                                                   "Authorization": "Bearer x",
                                                   "User-Agent": "u"},
                                       "timeout": 15.0, "max_bytes": 1000})

    def test_page_must_be_numeric(self):
        request = trending.build_request("trending_day", 1, "x" * 40)
        request["url"] = request["url"].replace("page=1", "page=01")
        with self.assertRaises(CaptureSecurityError):
            _validate_request(request)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._token = os.environ.get("TMDB_READ_TOKEN")
        os.environ["TMDB_READ_TOKEN"] = "x" * 40  # test double, never a real token

    def tearDown(self):
        if self._token is None:
            os.environ.pop("TMDB_READ_TOKEN", None)
        else:
            os.environ["TMDB_READ_TOKEN"] = self._token
        self.tmp.cleanup()

    def test_dry_run_never_calls_fetch(self):
        fetch = ScriptedFetch()
        result = trending.capture_trending(self.root, job_id="job-1", fetch=fetch,
                                           execute=False)
        self.assertEqual(result["status"], "DRY_RUN")
        self.assertEqual(result["requests_planned"], 4)
        self.assertEqual(fetch.requests, [])
        self.assertFalse((self.root / "job-1").exists())

    def test_requires_fetch_and_existing_root(self):
        with self.assertRaises(trending.TrendingInputError):
            trending.capture_trending(self.root, job_id="job-1", execute=True)
        with self.assertRaises(trending.TrendingArchiveError):
            trending.capture_trending(self.root / "missing", job_id="job-1",
                                      fetch=ScriptedFetch(), execute=True)

    def test_full_capture_writes_both_channels(self):
        fetch = ScriptedFetch()
        result = trending.capture_trending(self.root, job_id="job-1", fetch=fetch,
                                           execute=True, sleep=lambda s: None)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["requests_used"], 4)
        self.assertEqual(len(fetch.requests), 4)
        for channel in trending.CHANNELS:
            self.assertEqual(result["channels"][channel]["status"], "succeeded")
            self.assertEqual(result["channels"][channel]["entries"], 40)
        job_dir = self.root / "job-1"
        self.assertTrue((job_dir / "raw" / "trending_day-p1.json").is_file())
        self.assertTrue((job_dir / "raw" / "trending_week-p2.json").is_file())
        manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], trending.TRENDING_SCHEMA)
        self.assertNotIn("Bearer", json.dumps(manifest))
        entries = json.loads((job_dir / "entries.json").read_text(encoding="utf-8"))
        self.assertEqual(entries["trending_day"][0]["tmdb_id"], 100)

    def test_entries_are_deduped_and_ranked(self):
        duplicate = body(1, [500, 501]) + b""
        calls = {"n": 0}

        def fetch(request):
            _validate_request(request)
            calls["n"] += 1
            if "day" in request["url"] and "page=1" in request["url"]:
                payload = json.loads(duplicate)
                payload["results"].append({"id": 501, "title": "dup"})
                return {"status": 200, "headers": {},
                        "body": json.dumps(payload).encode(), "url": request["url"]}
            page = int(request["url"].rsplit("page=", 1)[1])
            return {"status": 200, "headers": {}, "body": body(page, [900 + page]),
                    "url": request["url"]}

        trending.capture_trending(self.root, job_id="job-2", fetch=fetch,
                                  execute=True, pages=2, sleep=lambda s: None)
        entries = json.loads((self.root / "job-2" / "entries.json").read_text(encoding="utf-8"))
        day_ids = [entry["tmdb_id"] for entry in entries["trending_day"]]
        self.assertEqual(day_ids, [500, 501, 902])

    def test_http_error_then_success_records_both_attempts(self):
        fetch = ScriptedFetch(script=[500])
        result = trending.capture_trending(self.root, job_id="job-3", fetch=fetch,
                                           execute=True, sleep=lambda s: None)
        self.assertEqual(result["requests_used"], 5)
        manifest = json.loads((self.root / "job-3" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual([attempt["status"] for attempt in manifest["attempts"]][:2],
                         ["FAILED", "HTTP_200"])

    def test_budget_stops_the_run_honestly(self):
        fetch = ScriptedFetch()
        result = trending.capture_trending(self.root, job_id="job-4", fetch=fetch,
                                           execute=True, max_requests=2,
                                           sleep=lambda s: None)
        self.assertEqual(result["requests_used"], 2)
        self.assertLessEqual(len(fetch.requests), 2)
        self.assertIn(result["channels"]["trending_week"]["status"], ("failed", "partial"))

    def test_all_pages_failing_keeps_status_failed(self):
        fetch = ScriptedFetch(default_status=503)
        result = trending.capture_trending(self.root, job_id="job-5", fetch=fetch,
                                           execute=True, sleep=lambda s: None)
        self.assertEqual(result["channels"]["trending_day"]["status"], "failed")
        self.assertEqual(result["requests_used"], 6)  # 3 attempts per channel, budget 12
        self.assertFalse((self.root / "job-5" / "raw" / "trending_day-p1.json").exists())

    def test_captured_at_is_stable_per_channel_and_not_in_the_future(self):
        moments = [datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc)]
        fetch = ScriptedFetch()
        result = trending.capture_trending(
            self.root, job_id="job-6", fetch=fetch, execute=True,
            now=lambda: moments[0], sleep=lambda s: None)
        for channel in trending.CHANNELS:
            self.assertEqual(result["channels"][channel]["captured_at"],
                             "2026-11-17T00:00:00+00:00")

    def test_load_archive_rejects_incomplete_dirs(self):
        with self.assertRaises(trending.TrendingArchiveError):
            trending.load_archive(self.root / "nothing")
        fetch = ScriptedFetch()
        trending.capture_trending(self.root, job_id="job-7", fetch=fetch,
                                  execute=True, sleep=lambda s: None)
        manifest, entries = trending.load_archive(self.root / "job-7")
        self.assertEqual(manifest["job_id"], "job-7")
        self.assertEqual(set(entries), set(trending.CHANNELS))

    def test_unknown_options_are_rejected(self):
        with self.assertRaises(trending.TrendingInputError):
            trending.capture_trending(self.root, job_id="job-8", pages=3)
        with self.assertRaises(trending.TrendingInputError):
            trending.capture_trending(self.root, job_id="job-8", max_requests=99)
        with self.assertRaises(trending.TrendingInputError):
            trending.capture_trending(self.root, job_id="bad id!")


if __name__ == "__main__":
    unittest.main()