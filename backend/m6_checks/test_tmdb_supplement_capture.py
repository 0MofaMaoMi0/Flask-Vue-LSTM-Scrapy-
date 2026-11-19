"""Offline checks for bounded TMDB supplement capture. No network or database."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import hashlib
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[1]
REPO = HERE.parents[2]
WORKSPACE = HERE.parents[3]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from catalog.tmdb_supplement.capture import (  # noqa: E402
    CaptureArchiveCorrupt,
    CaptureInputConflict,
    CaptureInputError,
    CaptureInputMismatch,
    CaptureSecurityError,
    capture_movies,
    urllib_fetch,
)

TOKEN = 'SYNTHETIC_TMDB_NOT_A_REAL_CREDENTIAL_1234567890'
NOW = 1700000000.0
TMP_ROOT = WORKSPACE / '项目工作日志' / '06-开发与验证' / 'TMDB补采-20261116' / '员工3-补采'
TMP_ROOT.mkdir(parents=True, exist_ok=True)


def detail(tmdb_id=101, **overrides):
    payload = {
        'id': tmdb_id,
        'title': '示例电影',
        'adult': False,
        'credits': {'cast': [], 'crew': []},
        'keywords': {'keywords': []},
        'release_dates': {'results': []},
    }
    payload.update(overrides)
    return payload


def response(payload=None, status=200, headers=None, body=None, url=None):
    if body is None:
        body = json.dumps(detail() if payload is None else payload, ensure_ascii=False).encode('utf-8')
    return {'status': status, 'headers': headers or {}, 'body': body, 'url': url}


def stub_normalize(**kwargs):
    return {
        'schema_version': 'tmdb-supplement-v1',
        'movie_id': kwargs['movie_id'],
        'tmdb_id': kwargs['tmdb_id'],
        'fetched_at': kwargs['fetched_at'],
        'job_id': kwargs['job_id'],
        'status': 'PRESENT',
    }


class FakeClock:
    def __init__(self, start=NOW):
        self.now = float(start)
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(float(seconds))
        self.now += float(seconds)


class FakeFetch:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        if not self.responses:
            raise AssertionError('unexpected extra request')
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, dict) and 'status' not in item:
            item = {'status': 200, 'headers': {}, 'body': json.dumps(item, ensure_ascii=False).encode('utf-8'), 'url': None}
        return item


class CrashSleep:
    def __call__(self, seconds):
        raise KeyboardInterrupt()


class Archive:
    def __enter__(self):
        self.path = Path(tempfile.mkdtemp(prefix='capture-', dir=str(TMP_ROOT)))
        return self.path

    def __exit__(self, exc_type, exc, tb):
        shutil.rmtree(self.path, ignore_errors=True)


def resign_checkpoint(path):
    checkpoint = json.loads(path.read_text(encoding='utf-8'))
    body = dict(checkpoint)
    body.pop('state_sha256', None)
    checkpoint['state_sha256'] = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()
    path.write_text(json.dumps(checkpoint), encoding='utf-8')


def execute(targets, responses, root, *, job_id='job', max_requests=10, clock=None, **options):
    clock = clock or FakeClock()
    fetch = FakeFetch(responses)
    options.setdefault('execute', True)
    options.setdefault('token_provider', lambda: TOKEN)
    options.setdefault('normalize', stub_normalize)
    options.setdefault('clock', clock)
    options.setdefault('sleep', clock.sleep)
    result = capture_movies(
        targets,
        fetch=fetch,
        archive_dir=str(root),
        job_id=job_id,
        max_requests=max_requests,
        **options
    )
    return result, fetch, clock


class CaptureDryRunTests(unittest.TestCase):
    def test_dry_run_never_fetches_tokens_or_writes(self):
        with Archive() as root:
            calls = []
            fetch = FakeFetch([])
            result = capture_movies(
                [{'movie_id': 1, 'tmdb_id': 101}],
                fetch=fetch,
                archive_dir=str(root),
                job_id='dry',
                max_requests=2,
                token_provider=lambda: calls.append('token'),
                normalize=stub_normalize,
            )
            self.assertEqual(result['mode'], 'dry-run')
            self.assertEqual(result['status'], 'PLANNED')
            self.assertEqual(result['minimum_requests_needed'], 1)
            self.assertEqual(fetch.calls, [])
            self.assertEqual(calls, [])
            self.assertFalse((root / 'dry').exists())

    def test_dry_run_without_archive_is_allowed(self):
        result = capture_movies([(1, 101)], archive_dir=None, job_id='dry2', max_requests=1)
        self.assertEqual(result['status'], 'PLANNED')
        self.assertIsNone(result['archive_dir'])

    def test_dry_run_inverse_alias_is_explicit(self):
        result = capture_movies([(1, 101)], archive_dir=None, job_id='dry2', max_requests=1, dry_run=True)
        self.assertEqual(result['mode'], 'dry-run')
        with Archive() as root:
            with self.assertRaises(CaptureInputError):
                capture_movies(
                    [(1, 101)], fetch=FakeFetch([detail(101)]), archive_dir=str(root),
                    job_id='dry2', max_requests=1, execute=True, dry_run=True,
                    token_provider=lambda: TOKEN, normalize=stub_normalize,
                )

    def test_duplicate_target_is_deduplicated_but_conflict_is_rejected(self):
        with Archive() as root:
            result = capture_movies(
                [{'movie_id': 1, 'tmdb_id': 101}, {'movie_id': 1, 'tmdb_id': 101}],
                archive_dir=str(root), job_id='dry', max_requests=2,
            )
            self.assertEqual(result['duplicate_targets'], 1)
            self.assertEqual(result['minimum_requests_needed'], 1)
            with self.assertRaises(CaptureInputConflict):
                capture_movies([(1, 101), (1, 102)], archive_dir=str(root), job_id='dry', max_requests=2)

    def test_invalid_inputs_are_rejected_before_writes(self):
        cases = [
            ([], 1),
            ([{'movie_id': True, 'tmdb_id': 1}], 1),
            ([{'movie_id': 1, 'tmdb_id': 0}], 1),
            ([{'movie_id': 1, 'tmdb_id': '2'}], 1),
            ([{'movie_id': 1, 'tmdb_id': 2, 'title': 'lookup'}], 1),
        ]
        for targets, budget in cases:
            with self.subTest(targets=targets):
                with self.assertRaises(CaptureInputError):
                    capture_movies(targets, archive_dir=None, job_id='j', max_requests=budget)
        with self.assertRaises(CaptureInputError):
            capture_movies([(1, 2)], archive_dir=None, job_id='../escape', max_requests=1)
        with self.assertRaises(CaptureInputError):
            capture_movies([(1, 2)], archive_dir=None, job_id='j', max_requests=0)


class CaptureExecutionTests(unittest.TestCase):
    def test_success_archives_raw_and_normalized_separately(self):
        with Archive() as root:
            result, fetch, clock = execute([(1, 101)], [response(detail(101))], root)
            self.assertEqual(result['status'], 'COMPLETED')
            self.assertEqual(result['requests_used'], 1)
            self.assertEqual(result['requests_this_run'], 1)
            target = result['targets'][0]
            self.assertEqual(target['status'], 'COMPLETED')
            self.assertEqual(target['attempts'], 1)
            self.assertTrue((root / 'job' / target['raw_path']).is_file())
            self.assertTrue((root / 'job' / target['normalized_path']).is_file())
            raw = json.loads((root / 'job' / target['raw_path']).read_text(encoding='utf-8'))
            normalized = json.loads((root / 'job' / target['normalized_path']).read_text(encoding='utf-8'))
            self.assertEqual(raw['id'], 101)
            self.assertEqual(normalized['tmdb_id'], 101)
            self.assertNotEqual(target['raw_path'], target['normalized_path'])
            checkpoint = json.loads((root / 'job' / 'checkpoint.json').read_text(encoding='utf-8'))
            self.assertEqual(checkpoint['status'], 'COMPLETED')
            self.assertEqual(checkpoint['request_count'], 1)
            self.assertTrue(checkpoint['state_sha256'])
            self.assertEqual(fetch.calls[0]['method'], 'GET')
            self.assertTrue(fetch.calls[0]['url'].startswith('https://api.themoviedb.org/3/movie/101?'))
            self.assertNotIn(TOKEN, fetch.calls[0]['url'])
            self.assertEqual(clock.sleeps, [])

    def test_real_normalizer_contract_is_consumed_when_present(self):
        try:
            from catalog.tmdb_supplement.normalize import normalize_movie
        except ImportError:
            self.skipTest('normalizer not delivered yet')
        with Archive() as root:
            result, fetch, clock = execute(
                [(1, 101)], [response(detail(101))], root, normalize=normalize_movie,
            )
            self.assertEqual(result['status'], 'COMPLETED')
            target = result['targets'][0]
            normalized = json.loads((root / 'job' / target['normalized_path']).read_text(encoding='utf-8'))
            self.assertEqual(normalized['schema_version'], 'tmdb-supplement-v1')
            self.assertEqual(len(normalized['payload_sha256']), 64)
            self.assertTrue(normalized['field_status'])

    def test_serial_pacing_between_requests(self):
        with Archive() as root:
            result, fetch, clock = execute(
                [(1, 101), (2, 202)],
                [response(detail(101)), response(detail(202))],
                root,
            )
            self.assertEqual(result['status'], 'COMPLETED')
            self.assertEqual(len(fetch.calls), 2)
            self.assertGreaterEqual(sum(clock.sleeps), 1.0)
            self.assertEqual(result['requests_used'], 2)

    def test_5xx_is_retried_with_bounded_backoff_and_counts_attempts(self):
        with Archive() as root:
            result, fetch, clock = execute(
                [(1, 101)],
                [response(status=503, body=b'busy'), response(detail(101))],
                root,
            )
            self.assertEqual(result['status'], 'COMPLETED')
            self.assertEqual(len(fetch.calls), 2)
            self.assertEqual(result['targets'][0]['attempts'], 2)
            self.assertEqual(result['requests_used'], 2)
            self.assertGreaterEqual(clock.sleeps[0], 1.0)
            attempts = json.loads((root / 'job' / 'manifest.json').read_text(encoding='utf-8'))['attempts']
            self.assertEqual([a['http_status'] for a in attempts], [503, 200])
            self.assertTrue((root / 'job' / 'raw' / '101' / 'attempt-1.json').is_file())

    def test_5xx_exhaustion_fails_target_and_keeps_all_originals(self):
        with Archive() as root:
            result, fetch, clock = execute(
                [(1, 101)],
                [response(status=500, body=b'one'), response(status=500, body=b'two'), response(status=500, body=b'three')],
                root,
            )
            self.assertEqual(result['status'], 'PARTIAL')
            self.assertEqual(result['targets'][0]['status'], 'FAILED')
            self.assertEqual(result['targets'][0]['issues'], ['UPSTREAM_5XX'])
            self.assertEqual(result['requests_used'], 3)
            self.assertEqual(len(fetch.calls), 3)
            self.assertEqual(len(list((root / 'job' / 'raw' / '101').glob('attempt-*.json'))), 3)

    def test_rate_limit_retry_after_is_respected_not_shortened(self):
        with Archive() as root:
            result, fetch, clock = execute(
                [(1, 101)],
                [response(status=429, headers={'Retry-After': '2'}, body=b'wait'), response(detail(101))],
                root,
            )
            self.assertEqual(result['status'], 'COMPLETED')
            self.assertEqual(len(fetch.calls), 2)
            self.assertGreaterEqual(clock.sleeps[0], 2.0)
            self.assertEqual(result['targets'][0]['attempts'], 2)
            self.assertEqual(result['requests_used'], 2)

    def test_rate_limit_wait_cap_pauses_without_early_retry(self):
        with Archive() as root:
            clock = FakeClock()
            start = clock()
            result, fetch, clock = execute(
                [(1, 101)],
                [response(status=429, headers={'Retry-After': '5'}, body=b'wait')],
                root,
                max_wait_seconds=1,
                clock=clock,
            )
            self.assertEqual(result['status'], 'PAUSED')
            self.assertEqual(result['pause']['reason'], 'WAIT_CAP_EXCEEDED')
            self.assertEqual(len(fetch.calls), 1)
            self.assertEqual(clock.sleeps, [])
            checkpoint = json.loads((root / 'job' / 'checkpoint.json').read_text(encoding='utf-8'))
            self.assertEqual(checkpoint['not_before'], start + 5)
            self.assertTrue(checkpoint['next_allowed_at'])

            interrupted, interrupted_fetch, _ = execute(
                [(1, 101)], [detail(101)], root, max_requests=3, max_wait_seconds=1,
                clock=clock,
            )
            self.assertEqual(interrupted['status'], 'PAUSED')
            self.assertEqual(interrupted['pause']['reason'], 'WAIT_CAP_EXCEEDED')
            self.assertEqual(interrupted_fetch.calls, [])
            self.assertEqual(interrupted['requests_used'], 1)

            resumed, resumed_fetch, _ = execute(
                [(1, 101)], [detail(101)], root, max_requests=3, max_wait_seconds=10,
                clock=clock,
            )
            self.assertEqual(resumed['status'], 'COMPLETED')
            self.assertEqual(len(resumed_fetch.calls), 1)
            self.assertEqual(resumed['requests_used'], 2)
            self.assertGreaterEqual(clock() - start, 5.0)

    def test_http_date_retry_after_is_parsed(self):
        stamp = format_datetime(datetime.fromtimestamp(NOW + 2, tz=timezone.utc), usegmt=True)
        with Archive() as root:
            result, fetch, clock = execute(
                [(1, 101)],
                [response(status=429, headers={'Retry-After': stamp}, body=b'wait'), detail(101)],
                root,
            )
            self.assertEqual(result['status'], 'COMPLETED')
            self.assertGreaterEqual(clock.sleeps[0], 1.9)

    def test_http_date_deadline_survives_resume(self):
        stamp = format_datetime(datetime.fromtimestamp(NOW + 4, tz=timezone.utc), usegmt=True)
        with Archive() as root:
            clock = FakeClock()
            start = clock()
            first, first_fetch, _ = execute(
                [(1, 101)],
                [response(status=429, headers={'Retry-After': stamp}, body=b'wait')],
                root,
                max_wait_seconds=1,
                clock=clock,
            )
            self.assertEqual(first['status'], 'PAUSED')
            self.assertEqual(first_fetch.calls and len(first_fetch.calls), 1)
            resumed, resumed_fetch, _ = execute(
                [(1, 101)], [detail(101)], root, max_wait_seconds=10, clock=clock,
            )
            self.assertEqual(resumed['status'], 'COMPLETED')
            self.assertEqual(len(resumed_fetch.calls), 1)
            self.assertGreaterEqual(clock() - start, 4.0)

    def test_past_http_date_needs_no_wait(self):
        stamp = format_datetime(datetime.fromtimestamp(NOW - 10, tz=timezone.utc), usegmt=True)
        with Archive() as root:
            result, fetch, clock = execute(
                [(1, 101)],
                [response(status=429, headers={'Retry-After': stamp}, body=b'wait'), detail(101)],
                root,
            )
            self.assertEqual(result['status'], 'COMPLETED')
            self.assertEqual(len(fetch.calls), 2)
            self.assertEqual(clock.sleeps, [1.0])

    def test_expired_deadline_needs_no_extra_wait(self):
        with Archive() as root:
            clock = FakeClock()
            first, first_fetch, _ = execute(
                [(1, 101)],
                [response(status=429, headers={'Retry-After': '5'}, body=b'wait')],
                root,
                max_wait_seconds=1,
                clock=clock,
            )
            self.assertEqual(first['status'], 'PAUSED')
            clock.now += 5.5
            resumed, resumed_fetch, _ = execute(
                [(1, 101)], [detail(101)], root, max_wait_seconds=10, clock=clock,
            )
            self.assertEqual(resumed['status'], 'COMPLETED')
            self.assertEqual(len(resumed_fetch.calls), 1)
            self.assertEqual(clock.sleeps, [])
            self.assertGreaterEqual(clock() - 1700000000.0, 5.5)

    def test_budget_pause_keeps_deadline_for_resume(self):
        with Archive() as root:
            clock = FakeClock()
            first, first_fetch, _ = execute(
                [(1, 101)],
                [response(status=429, headers={'Retry-After': '5'}, body=b'wait')],
                root,
                max_requests=1,
                max_wait_seconds=10,
                clock=clock,
            )
            self.assertEqual(first['status'], 'PAUSED')
            self.assertEqual(first['pause']['reason'], 'BUDGET_EXHAUSTED')
            self.assertEqual(first_fetch.calls and len(first_fetch.calls), 1)
            checkpoint = json.loads((root / 'job' / 'checkpoint.json').read_text(encoding='utf-8'))
            self.assertIsNotNone(checkpoint['not_before'])
            resumed, resumed_fetch, _ = execute(
                [(1, 101)], [detail(101)], root, max_requests=2, max_wait_seconds=10, clock=clock,
            )
            self.assertEqual(resumed['status'], 'COMPLETED')
            self.assertEqual(len(resumed_fetch.calls), 1)
            self.assertGreaterEqual(clock.sleeps[0], 5.0)

    def test_total_wait_pause_keeps_deadline_for_resume(self):
        with Archive() as root:
            clock = FakeClock()
            first, first_fetch, _ = execute(
                [(1, 101)],
                [response(status=429, headers={'Retry-After': '5'}, body=b'wait')],
                root,
                max_wait_seconds=10,
                max_total_wait_seconds=1,
                clock=clock,
            )
            self.assertEqual(first['status'], 'PAUSED')
            self.assertEqual(first['pause']['reason'], 'TOTAL_WAIT_CAP_EXCEEDED')
            self.assertEqual(first_fetch.calls and len(first_fetch.calls), 1)
            resumed, resumed_fetch, _ = execute(
                [(1, 101)], [detail(101)], root, max_wait_seconds=10,
                max_total_wait_seconds=10, clock=clock,
            )
            self.assertEqual(resumed['status'], 'COMPLETED')
            self.assertEqual(len(resumed_fetch.calls), 1)
            self.assertGreaterEqual(clock.sleeps[0], 5.0)

    def test_clock_rollback_waits_conservatively(self):
        with Archive() as root:
            clock = FakeClock()
            first, first_fetch, _ = execute(
                [(1, 101)],
                [response(status=429, headers={'Retry-After': '5'}, body=b'wait')],
                root,
                max_wait_seconds=1,
                clock=clock,
            )
            self.assertEqual(first['status'], 'PAUSED')
            clock.now -= 10.0
            resumed, resumed_fetch, _ = execute(
                [(1, 101)], [detail(101)], root, max_requests=3, max_wait_seconds=10, clock=clock,
            )
            self.assertEqual(resumed['status'], 'PAUSED')
            self.assertEqual(resumed['pause']['reason'], 'WAIT_CAP_EXCEEDED')
            self.assertEqual(resumed_fetch.calls, [])

    def test_nan_clock_is_rejected_before_fetch(self):
        with Archive() as root:
            fetch = FakeFetch([detail(101)])
            with self.assertRaises(CaptureInputError):
                capture_movies(
                    [(1, 101)], fetch=fetch, archive_dir=str(root), job_id='job', max_requests=1,
                    execute=True, token_provider=lambda: TOKEN, normalize=stub_normalize,
                    clock=lambda: float('nan'),
                )
            self.assertEqual(fetch.calls, [])

    def test_budget_exhaustion_pauses_and_retry_never_exceeds_budget(self):
        with Archive() as root:
            result, fetch, clock = execute(
                [(1, 101)],
                [response(status=500, body=b'one')],
                root,
                max_requests=1,
            )
            self.assertEqual(result['status'], 'PAUSED')
            self.assertEqual(result['pause']['reason'], 'BUDGET_EXHAUSTED')
            self.assertEqual(result['requests_used'], 1)
            self.assertEqual(len(fetch.calls), 1)
            self.assertEqual(clock.sleeps, [])

    def test_401_and_403_stop_the_whole_job_without_retry(self):
        for code in (401, 403):
            with self.subTest(code=code), Archive() as root:
                result, fetch, clock = execute(
                    [(1, 101), (2, 202)],
                    [response(status=code, body=b'no')],
                    root,
                )
                self.assertEqual(result['status'], 'AUTH_FAILED')
                self.assertEqual(len(fetch.calls), 1)
                self.assertEqual(result['requests_used'], 1)
                checkpoint = json.loads((root / 'job' / 'checkpoint.json').read_text(encoding='utf-8'))
                self.assertEqual(checkpoint['status'], 'AUTH_FAILED')
                self.assertFalse((root / 'job' / 'raw' / '101').exists() and any((root / 'job' / 'raw' / '101').iterdir()))

    def test_transport_failure_is_retried_without_leaking_exception_text(self):
        with Archive() as root:
            secret_error = TimeoutError('sentinel ' + TOKEN)
            result, fetch, clock = execute(
                [(1, 101)], [secret_error, detail(101)], root,
            )
            self.assertEqual(result['status'], 'COMPLETED')
            self.assertEqual(result['targets'][0]['attempts'], 2)
            manifest = (root / 'job' / 'manifest.json').read_text(encoding='utf-8')
            self.assertNotIn(TOKEN, manifest)
            self.assertNotIn('sentinel', manifest)

    def test_oversized_body_fails_without_storing_truncated_content(self):
        with Archive() as root:
            result, fetch, clock = execute(
                [(1, 101)], [response(body=b'x' * 101)], root, max_body_bytes=100,
            )
            self.assertEqual(result['status'], 'PARTIAL')
            self.assertEqual(result['targets'][0]['status'], 'FAILED')
            self.assertEqual(result['targets'][0]['issues'], ['BODY_TOO_LARGE'])
            self.assertFalse((root / 'job' / 'raw' / '101').exists())

    def test_cross_origin_or_redirect_response_is_rejected(self):
        with Archive() as root:
            with self.assertRaises(CaptureSecurityError):
                execute([(1, 101)], [response(url='https://evil.example/collect')], root)

    def test_payload_identity_mismatch_is_rejected_and_original_kept(self):
        with Archive() as root:
            result, fetch, clock = execute([(1, 101)], [detail(999)], root)
            self.assertEqual(result['status'], 'PARTIAL')
            self.assertEqual(result['targets'][0]['status'], 'FAILED')
            self.assertIn('IDENTITY_OR_UPSTREAM_MISMATCH', result['targets'][0]['issues'])
            self.assertTrue((root / 'job' / 'raw' / '101' / 'attempt-1.json').is_file())

    def test_partial_append_is_reported_and_normalized_without_erasing_raw(self):
        payload = detail(101)
        payload.pop('keywords')
        with Archive() as root:
            result, fetch, clock = execute([(1, 101)], [payload], root)
            self.assertEqual(result['status'], 'PARTIAL')
            target = result['targets'][0]
            self.assertEqual(target['status'], 'PARTIAL')
            self.assertIn('APPEND_MISSING_keywords', target['issues'])
            self.assertTrue((root / 'job' / target['raw_path']).is_file())
            self.assertTrue((root / 'job' / target['normalized_path']).is_file())

    def test_normalizer_identity_mismatch_fails_target(self):
        def wrong(**kwargs):
            doc = stub_normalize(**kwargs)
            doc['tmdb_id'] = 999
            return doc
        with Archive() as root:
            result, fetch, clock = execute([(1, 101)], [detail(101)], root, normalize=wrong)
            target = result['targets'][0]
            self.assertEqual(target['status'], 'FAILED')
            self.assertIn('NORMALIZER_IDENTITY_MISMATCH', target['issues'])
            self.assertTrue((root / 'job' / 'raw' / '101' / 'attempt-1.json').is_file())
            self.assertFalse(any((root / 'job' / 'normalized').rglob('*.json')))

    def test_resume_skips_completed_targets_and_keeps_cumulative_budget(self):
        with Archive() as root:
            first, first_fetch, _ = execute(
                [(1, 101), (2, 202)], [detail(101)], root, max_requests=1,
            )
            self.assertEqual(first['status'], 'PAUSED')
            self.assertEqual(len(first_fetch.calls), 1)
            self.assertEqual(first['requests_used'], 1)

            second, second_fetch, _ = execute(
                [(1, 101), (2, 202)], [detail(202)], root, max_requests=2,
            )
            self.assertEqual(second['status'], 'COMPLETED')
            self.assertEqual(len(second_fetch.calls), 1)
            self.assertEqual(second['requests_used'], 2)
            self.assertEqual(second['requests_this_run'], 1)
            statuses = {item['tmdb_id']: item['status'] for item in second['targets']}
            self.assertEqual(statuses, {101: 'SKIPPED', 202: 'COMPLETED'})

    def test_plan_after_checkpoint_is_read_only(self):
        with Archive() as root:
            result, fetch, clock = execute([(1, 101)], [detail(101)], root)
            before = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
            planned = capture_movies(
                [(1, 101)], fetch=None, archive_dir=str(root), job_id='job', max_requests=2,
            )
            after = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
            self.assertEqual(planned['mode'], 'dry-run')
            self.assertEqual(planned['requests_used'], 1)
            self.assertEqual(planned['minimum_requests_needed'], 0)
            self.assertEqual(planned['targets'][0]['status'], 'SKIPPED')
            self.assertEqual(before, after)

    def test_resume_rejects_changed_targets(self):
        with Archive() as root:
            result, fetch, clock = execute([(1, 101)], [detail(101)], root)
            self.assertEqual(result['status'], 'COMPLETED')
            with self.assertRaises(CaptureInputMismatch):
                execute([(1, 101), (2, 202)], [detail(202)], root, max_requests=2)

    def test_resume_rejects_corrupt_raw_original(self):
        with Archive() as root:
            result, fetch, clock = execute([(1, 101)], [detail(101)], root)
            raw = root / 'job' / result['targets'][0]['raw_path']
            raw.write_bytes(b'{"tampered":true}')
            with self.assertRaises(CaptureArchiveCorrupt):
                execute([(1, 101)], [detail(101)], root)

    def test_resume_rejects_checkpoint_tampering(self):
        with Archive() as root:
            result, fetch, clock = execute([(1, 101)], [detail(101)], root)
            checkpoint_path = root / 'job' / 'checkpoint.json'
            checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8'))
            checkpoint['request_count'] = 99
            checkpoint_path.write_text(json.dumps(checkpoint), encoding='utf-8')
            with self.assertRaises(CaptureArchiveCorrupt):
                execute([(1, 101)], [detail(101)], root)

    def test_resume_rejects_non_finite_deadline_even_with_valid_hash(self):
        with Archive() as root:
            result, fetch, clock = execute(
                [(1, 101)],
                [response(status=429, headers={'Retry-After': '5'}, body=b'wait')],
                root,
                max_wait_seconds=1,
                clock=FakeClock(),
            )
            self.assertEqual(result['status'], 'PAUSED')
            checkpoint_path = root / 'job' / 'checkpoint.json'
            checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8'))
            checkpoint['not_before'] = float('nan')
            checkpoint_path.write_text(json.dumps(checkpoint), encoding='utf-8')
            resign_checkpoint(checkpoint_path)
            with self.assertRaises(CaptureArchiveCorrupt):
                execute([(1, 101)], [detail(101)], root, max_wait_seconds=10)

    def test_resume_rejects_invalid_target_attempt_count_even_with_valid_hash(self):
        with Archive() as root:
            result, fetch, clock = execute([(1, 101)], [detail(101)], root)
            checkpoint_path = root / 'job' / 'checkpoint.json'
            checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8'))
            checkpoint['target_attempts'] = {'101': -1}
            checkpoint_path.write_text(json.dumps(checkpoint), encoding='utf-8')
            resign_checkpoint(checkpoint_path)
            with self.assertRaises(CaptureArchiveCorrupt):
                execute([(1, 101)], [detail(101)], root)

    def test_resume_rejects_corrupt_failed_original_too(self):
        with Archive() as root:
            result, fetch, clock = execute(
                [(1, 101)], [response(status=503, body=b'original failure'), detail(101)], root,
            )
            self.assertEqual(result['status'], 'COMPLETED')
            (root / 'job' / 'raw' / '101' / 'attempt-1.json').write_bytes(b'tampered')
            with self.assertRaises(CaptureArchiveCorrupt):
                execute([(1, 101)], [detail(101)], root)

    def test_crash_budget_cannot_reset_to_zero(self):
        with Archive() as root:
            first, first_fetch, _ = execute(
                [(1, 101), (2, 202)], [detail(101)], root, max_requests=1,
            )
            self.assertEqual(first['status'], 'PAUSED')
            second, second_fetch, _ = execute(
                [(1, 101), (2, 202)], [detail(202)], root, max_requests=1,
            )
            self.assertEqual(second['status'], 'PAUSED')
            self.assertEqual(second['pause']['reason'], 'BUDGET_EXHAUSTED')
            self.assertEqual(second_fetch.calls, [])
            self.assertEqual(second['requests_used'], 1)

    def test_path_traversal_and_relative_archive_are_rejected(self):
        with self.assertRaises(CaptureInputError):
            capture_movies([(1, 101)], archive_dir=None, job_id='a/b', max_requests=1)
        with Archive() as root:
            with self.assertRaises(CaptureSecurityError):
                execute([(1, 101)], [detail(101)], Path('relative'))
        with Archive() as root:
            with self.assertRaises(CaptureSecurityError):
                execute([(1, 101)], [detail(101)], root / '..' / 'escape')

    def test_token_is_absent_from_all_artifacts_and_missing_token_does_not_write(self):
        with Archive() as root:
            result, fetch, clock = execute([(1, 101)], [detail(101)], root)
            self.assertNotIn(TOKEN, json.dumps(result, ensure_ascii=False))
            for path in (root / 'job').rglob('*'):
                if path.is_file():
                    self.assertNotIn(TOKEN, path.read_text(encoding='utf-8'))
        with Archive() as root:
            with self.assertRaises(CaptureInputError):
                execute(
                    [(1, 101)], [detail(101)], root,
                    token_provider=lambda: '',
                )
            self.assertFalse((root / 'job').exists())

    def test_failed_attempt_originals_are_retained(self):
        with Archive() as root:
            result, fetch, clock = execute(
                [(1, 101)], [response(status=503, body=b'original failure'), detail(101)], root,
            )
            failed = root / 'job' / 'raw' / '101' / 'attempt-1.json'
            self.assertEqual(failed.read_bytes(), b'original failure')
            self.assertEqual(result['status'], 'COMPLETED')

    def test_repeated_429_pauses_after_retry_limit(self):
        with Archive() as root:
            result, fetch, clock = execute(
                [(1, 101)],
                [response(status=429, headers={'Retry-After': '1'}, body=b'one'),
                 response(status=429, headers={'Retry-After': '1'}, body=b'two'),
                 response(status=429, headers={'Retry-After': '1'}, body=b'three')],
                root,
            )
            self.assertEqual(result['status'], 'PAUSED')
            self.assertEqual(result['pause']['reason'], 'RATE_LIMITED')
            self.assertEqual(len(fetch.calls), 3)
            self.assertEqual(result['requests_used'], 3)

    def test_retry_exhaustion_survives_repeated_resume_without_fetch(self):
        with Archive() as root:
            clock = FakeClock()
            first, fetch, _ = execute(
                [(1, 101)],
                [response(status=429, headers={'Retry-After': '2'}, body=b'one'),
                 response(status=429, headers={'Retry-After': '2'}, body=b'two'),
                 response(status=429, headers={'Retry-After': '2'}, body=b'three')],
                root,
                max_wait_seconds=10,
                max_requests=10,
                clock=clock,
            )
            self.assertEqual(first['status'], 'PAUSED')
            self.assertEqual(first['pause']['reason'], 'RATE_LIMITED')
            self.assertEqual(len(fetch.calls), 3)
            self.assertEqual(first['requests_used'], 3)
            deadline = first['next_allowed_at']
            self.assertIsNotNone(deadline)

            for _ in range(2):
                resumed, resumed_fetch, _ = execute(
                    [(1, 101)], [detail(101)], root, max_wait_seconds=10,
                    max_requests=10, clock=clock,
                )
                self.assertEqual(resumed['status'], 'PAUSED')
                self.assertEqual(resumed['pause']['reason'], 'RETRY_LIMIT_REACHED')
                self.assertEqual(resumed_fetch.calls, [])
                self.assertEqual(resumed['requests_used'], 3)
                self.assertEqual(resumed['next_allowed_at'], deadline)

    def test_retry_budget_is_per_target_across_resume(self):
        with Archive() as root:
            clock = FakeClock()
            first, first_fetch, _ = execute(
                [(1, 101)], [response(status=500, body=b'one')], root,
                max_requests=1, clock=clock,
            )
            self.assertEqual(first['status'], 'PAUSED')
            self.assertEqual(first['pause']['reason'], 'BUDGET_EXHAUSTED')
            self.assertEqual(first['requests_used'], 1)

            second, second_fetch, _ = execute(
                [(1, 101)],
                [response(status=500, body=b'two'), response(status=500, body=b'three')],
                root,
                max_requests=4, clock=clock,
            )
            self.assertEqual(second['status'], 'PARTIAL')
            self.assertEqual(second['targets'][0]['status'], 'FAILED')
            self.assertEqual(second['targets'][0]['attempts'], 3)
            self.assertEqual(len(first_fetch.calls) + len(second_fetch.calls), 3)
            self.assertEqual(second['requests_used'], 3)

            third, third_fetch, _ = execute(
                [(1, 101)], [detail(101)], root, max_requests=10, clock=clock,
            )
            self.assertEqual(third['status'], 'PARTIAL')
            self.assertEqual(third_fetch.calls, [])
            self.assertEqual(third['targets'][0]['issues'], ['RETRY_LIMIT_REACHED'])

    def test_crash_attempt_reservations_count_toward_limit(self):
        with Archive() as root:
            with self.assertRaises(KeyboardInterrupt):
                execute(
                    [(1, 101)], [KeyboardInterrupt()], root,
                    max_retries=1, max_requests=10,
                )
            checkpoint = json.loads((root / 'job' / 'checkpoint.json').read_text(encoding='utf-8'))
            self.assertEqual(checkpoint['target_attempts']['101'], 1)
            with self.assertRaises(KeyboardInterrupt):
                execute(
                    [(1, 101)], [KeyboardInterrupt()], root,
                    max_retries=1, max_requests=10,
                )
            checkpoint = json.loads((root / 'job' / 'checkpoint.json').read_text(encoding='utf-8'))
            self.assertEqual(checkpoint['target_attempts']['101'], 2)
            third, third_fetch, _ = execute(
                [(1, 101)], [detail(101)], root, max_retries=1, max_requests=10,
            )
            self.assertEqual(third['status'], 'PARTIAL')
            self.assertEqual(third_fetch.calls, [])
            self.assertEqual(third['targets'][0]['issues'], ['RETRY_LIMIT_REACHED'])

    def test_crash_during_429_wait_keeps_deadline(self):
        with Archive() as root:
            clock = FakeClock()
            with self.assertRaises(KeyboardInterrupt):
                execute(
                    [(1, 101)],
                    [response(status=429, headers={'Retry-After': '5'}, body=b'wait')],
                    root,
                    max_wait_seconds=10,
                    clock=clock,
                    sleep=CrashSleep(),
                )
            checkpoint = json.loads((root / 'job' / 'checkpoint.json').read_text(encoding='utf-8'))
            self.assertAlmostEqual(checkpoint['not_before'], clock() + 5.0)
            resumed, resumed_fetch, _ = execute(
                [(1, 101)], [detail(101)], root, max_wait_seconds=10, clock=clock,
            )
            self.assertEqual(resumed['status'], 'COMPLETED')
            self.assertEqual(len(resumed_fetch.calls), 1)
            self.assertGreaterEqual(clock.sleeps[0], 5.0)

    def test_total_wait_cap_pauses_before_next_request(self):
        with Archive() as root:
            result, fetch, clock = execute(
                [(1, 101), (2, 202)],
                [detail(101), detail(202)],
                root,
                max_total_wait_seconds=0.5,
            )
            self.assertEqual(result['status'], 'PAUSED')
            self.assertEqual(result['pause']['reason'], 'TOTAL_WAIT_CAP_EXCEEDED')
            self.assertEqual(len(fetch.calls), 1)
            self.assertEqual(result['requests_used'], 1)

    def test_normalizer_failed_field_marks_target_partial(self):
        def degraded(**kwargs):
            document = stub_normalize(**kwargs)
            document['field_status'] = {'base': 'FAILED'}
            return document
        with Archive() as root:
            result, fetch, clock = execute([(1, 101)], [detail(101)], root, normalize=degraded)
            self.assertEqual(result['status'], 'PARTIAL')
            self.assertEqual(result['targets'][0]['status'], 'PARTIAL')
            self.assertIn('NORMALIZER_FIELD_FAILED_base', result['targets'][0]['issues'])

    def test_append_upstream_errors_are_partial(self):
        payload = detail(101)
        payload['errors'] = [{'code': 1}]
        with Archive() as root:
            result, fetch, clock = execute([(1, 101)], [payload], root)
            self.assertEqual(result['status'], 'PARTIAL')
            self.assertIn('APPEND_UPSTREAM_ERRORS', result['targets'][0]['issues'])

    def test_execute_requires_injected_fetch(self):
        with Archive() as root:
            with self.assertRaises(CaptureInputError):
                capture_movies(
                    [(1, 101)], archive_dir=str(root), job_id='job', max_requests=1,
                    execute=True, token_provider=lambda: TOKEN, normalize=stub_normalize,
                )
            self.assertFalse((root / 'job').exists())

    def test_default_transport_rejects_non_allowlisted_urls_and_headers(self):
        bad_url = {
            'url': 'https://evil.example/3/movie/101?language=zh-CN&append_to_response=credits%2Ckeywords%2Crelease_dates',
            'headers': {'Accept': 'application/json', 'Authorization': 'Bearer ' + TOKEN, 'User-Agent': 'x'},
            'timeout': 1.0, 'max_bytes': 100,
        }
        with self.assertRaises(CaptureSecurityError):
            urllib_fetch(bad_url)
        query_token = dict(bad_url)
        query_token['url'] = 'https://api.themoviedb.org/3/movie/101?language=zh-CN&append_to_response=credits%2Ckeywords%2Crelease_dates&api_key=' + TOKEN
        with self.assertRaises(CaptureSecurityError):
            urllib_fetch(query_token)
        extra_header = dict(bad_url)
        extra_header['url'] = 'https://api.themoviedb.org/3/movie/101?language=zh-CN&append_to_response=credits%2Ckeywords%2Crelease_dates'
        extra_header['headers'] = dict(bad_url['headers'], **{'X-Token': TOKEN})
        with self.assertRaises(CaptureSecurityError):
            urllib_fetch(extra_header)


class CaptureCliTests(unittest.TestCase):
    def test_plan_and_validate_are_offline(self):
        with Archive() as root:
            target_file = root / 'targets.json'
            target_file.write_text(json.dumps([{'movie_id': 1, 'tmdb_id': 101}]), encoding='utf-8')
            script = REPO / 'scripts' / 'tmdb_supplement.py'
            env = dict(os.environ)
            env['PYTHONDONTWRITEBYTECODE'] = '1'
            for command in ('plan', 'validate'):
                with self.subTest(command=command):
                    run = subprocess.run(
                        [sys.executable, '-B', str(script), command, '--targets', str(target_file),
                         '--job-id', 'cli', '--max-requests', '2'],
                        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertNotIn('Traceback', run.stderr)
                    output = json.loads(run.stdout)
                    self.assertEqual(output['mode'], 'dry-run')
                    self.assertEqual(output['status'], 'PLANNED')
                    if command == 'validate':
                        self.assertTrue(output['valid'])
            self.assertFalse((root / 'cli').exists())

    def test_capture_requires_explicit_execute(self):
        with Archive() as root:
            target_file = root / 'targets.json'
            target_file.write_text(json.dumps([{'movie_id': 1, 'tmdb_id': 101}]), encoding='utf-8')
            script = REPO / 'scripts' / 'tmdb_supplement.py'
            run = subprocess.run(
                [sys.executable, '-B', str(script), 'capture', '--targets', str(target_file),
                 '--archive', str(root), '--job-id', 'cli', '--max-requests', '1'],
                cwd=str(REPO), capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(run.returncode, 2)
            self.assertFalse((root / 'cli').exists())

    def test_capture_without_runtime_token_does_not_write(self):
        with Archive() as root:
            target_file = root / 'targets.json'
            target_file.write_text(json.dumps([{'movie_id': 1, 'tmdb_id': 101}]), encoding='utf-8')
            script = REPO / 'scripts' / 'tmdb_supplement.py'
            env = dict(os.environ)
            env.pop('TMDB_READ_TOKEN', None)
            run = subprocess.run(
                [sys.executable, '-B', str(script), 'capture', '--execute',
                 '--targets', str(target_file), '--archive', str(root),
                 '--job-id', 'cli', '--max-requests', '1'],
                cwd=str(REPO), env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(run.returncode, 2)
            self.assertNotIn('Traceback', run.stderr)
            self.assertFalse((root / 'cli').exists())

    def test_invalid_target_file_is_reported_without_traceback(self):
        with Archive() as root:
            target_file = root / 'broken.json'
            target_file.write_text('{not json', encoding='utf-8')
            script = REPO / 'scripts' / 'tmdb_supplement.py'
            run = subprocess.run(
                [sys.executable, '-B', str(script), 'plan', '--targets', str(target_file),
                 '--job-id', 'cli', '--max-requests', '1'],
                cwd=str(REPO), capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(run.returncode, 2)
            self.assertNotIn('Traceback', run.stderr)
            self.assertIn('TARGET_FILE_INVALID', run.stderr)


class DiscoverRequestValidationTests(unittest.TestCase):
    def _req(self, url, **overrides):
        req = {
            'kind': 'discover',
            'method': 'GET',
            'url': url,
            'headers': {
                'Accept': 'application/json',
                'Authorization': 'Bearer ' + TOKEN,
                'User-Agent': 'mi2-discover-test/1.0',
            },
            'timeout': 15.0,
            'max_bytes': 1500000,
        }
        req.update(overrides)
        return req

    def test_accepts_bounded_popular_top_rated_and_discover_urls(self):
        from catalog.tmdb_supplement.capture import _validate_request

        valid_urls = [
            'https://api.themoviedb.org/3/movie/popular?language=zh-CN&page=1',
            'https://api.themoviedb.org/3/movie/top_rated?language=en-US&page=500',
            'https://api.themoviedb.org/3/discover/movie?language=zh-CN&page=3&include_adult=false&sort_by=popularity.desc&vote_count.gte=100&vote_average.gte=7.5',
        ]
        for url in valid_urls:
            with self.subTest(url=url):
                validated_url, headers, timeout, max_bytes = _validate_request(self._req(url))
                self.assertEqual(validated_url, url)
                self.assertEqual(headers['Accept'], 'application/json')
                self.assertEqual(timeout, 15.0)
                self.assertEqual(max_bytes, 1500000)

    def test_rejects_disallowed_paths_pages_and_query_params(self):
        from catalog.tmdb_supplement.capture import _validate_request

        bad_cases = [
            ('https://evil.example/3/movie/popular?language=zh-CN&page=1', 'URL_NOT_ALLOWED'),
            ('https://api.themoviedb.org/3/search/movie?language=zh-CN&page=1&query=abc', 'URL_NOT_ALLOWED'),
            ('https://api.themoviedb.org/3/movie/popular?language=zh-CN&page=0', 'PAGE_NOT_ALLOWED'),
            ('https://api.themoviedb.org/3/movie/popular?language=zh-CN&page=501', 'PAGE_NOT_ALLOWED'),
            ('https://api.themoviedb.org/3/movie/popular?language=zh-CN&page=1&sort_by=popularity.desc', 'URL_QUERY_NOT_ALLOWED'),
            ('https://api.themoviedb.org/3/discover/movie?language=zh-CN&page=1', 'URL_QUERY_NOT_ALLOWED'),
            ('https://api.themoviedb.org/3/discover/movie?language=zh-CN&page=1&include_adult=true', 'URL_QUERY_NOT_ALLOWED'),
            ('https://api.themoviedb.org/3/discover/movie?language=zh-CN&page=1&include_adult=false&sort_by=revenue.desc', 'URL_QUERY_NOT_ALLOWED'),
            ('https://api.themoviedb.org/3/discover/movie?language=zh-CN&page=1&include_adult=false&vote_count.gte=-1', 'URL_QUERY_NOT_ALLOWED'),
            ('https://api.themoviedb.org/3/discover/movie?language=zh-CN&page=1&include_adult=false&vote_average.gte=11', 'URL_QUERY_NOT_ALLOWED'),
            ('https://api.themoviedb.org/3/discover/movie?language=zh-CN&page=1&include_adult=false&with_keywords=10', 'URL_QUERY_NOT_ALLOWED'),
        ]
        for url, expected_code in bad_cases:
            with self.subTest(url=url):
                with self.assertRaises(CaptureSecurityError) as caught:
                    _validate_request(self._req(url))
                self.assertEqual(caught.exception.code, expected_code)


if __name__ == '__main__':
    unittest.main()