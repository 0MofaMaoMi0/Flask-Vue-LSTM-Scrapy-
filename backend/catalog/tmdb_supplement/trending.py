"""Bounded capture for the two TMDB trending boards (day / week).

This is deliberately *not* a generalisation of the movie transport: the only
URLs it can ever request are ``https://api.themoviedb.org/3/trending/movie/day``
and ``.../week``, pages 1-2, with a ``language`` parameter and nothing else.
Validation happens inside :mod:`capture`, so both transports share exactly one
code path for headers, TLS, redirects, timeouts and body caps.

Budget: at most ``max_requests`` (default 12) requests in total, at most three
attempts per (channel, page), serial with a >= 1 s gap.  A 429 ``Retry-After``
is honoured but capped, and the run reports what it could not collect instead of
retrying forever.

Output layout (``<archive>/<job_id>/``)::

    job.json          inputs, language, pages, budget
    manifest.json     per-attempt status/bytes/sha256, captured_at per channel
    raw/<channel>-p<page>.json   verbatim response body
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from .capture import (
    CaptureError,
    CaptureInputError,
    CaptureSecurityError,
    MAX_BODY_BYTES,
    _read_token,
    _validate_job_id,
)

TRENDING_SCHEMA = 'tmdb-supplement-trending-v1'
TRENDING_JOB_SCHEMA = 'tmdb-supplement-trending-job-v1'
CHANNEL_PATHS = {
    'trending_day': '/3/trending/movie/day',
    'trending_week': '/3/trending/movie/week',
}
CHANNELS = tuple(CHANNEL_PATHS)
MAX_PAGES = 2
MAX_REQUESTS = 12
MAX_ATTEMPTS = 3
DEFAULT_TIMEOUT = 15.0
DEFAULT_MIN_INTERVAL = 1.0
MAX_RETRY_AFTER = 60.0


class TrendingInputError(CaptureError):
    code = 'INPUT_INVALID'


class TrendingArchiveError(CaptureError):
    code = 'ARCHIVE_INVALID'


def _iso(moment):
    return moment.astimezone(timezone.utc).isoformat()


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')


def build_request(channel, page, token, *, language='zh-CN',
                  timeout=DEFAULT_TIMEOUT, max_bytes=MAX_BODY_BYTES):
    """One strict trending request; unknown channels/pages are rejected."""
    if channel not in CHANNEL_PATHS:
        raise TrendingInputError('CHANNEL_INVALID')
    if page not in (1, 2):
        raise TrendingInputError('PAGE_INVALID')
    query = urllib.parse.urlencode({'language': language, 'page': page})
    return {
        'kind': 'trending',
        'method': 'GET',
        'url': 'https://api.themoviedb.org' + CHANNEL_PATHS[channel] + '?' + query,
        'headers': {
            'Accept': 'application/json',
            'Authorization': 'Bearer ' + token,
            'User-Agent': 'movieinsight-m6-trending/1.0',
        },
        'timeout': timeout,
        'max_bytes': max_bytes,
    }


def _parse_payload(body, channel, page):
    try:
        payload = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise CaptureError('RESPONSE_NOT_JSON') from None
    if not isinstance(payload, dict):
        raise CaptureError('RESPONSE_SHAPE_INVALID')
    if payload.get('page') != page:
        raise CaptureError('RESPONSE_PAGE_MISMATCH')
    results = payload.get('results')
    if not isinstance(results, list) or not results:
        raise CaptureError('RESPONSE_RESULTS_INVALID')
    entries = []
    for item in results:
        if not isinstance(item, dict):
            raise CaptureError('RESPONSE_ITEM_INVALID')
        tmdb_id = item.get('id')
        if type(tmdb_id) is not int or tmdb_id <= 0:
            raise CaptureError('RESPONSE_ITEM_INVALID')
        title = item.get('title') or item.get('original_title') or item.get('name')
        entries.append({
            'tmdb_id': tmdb_id,
            'title': (title if isinstance(title, str) else None),
            'release_date': item.get('release_date') if isinstance(item.get('release_date'), str) else None,
        })
    return entries


def capture_trending(archive_dir, *, job_id, fetch=None, execute=False,
                     pages=MAX_PAGES, max_requests=MAX_REQUESTS,
                     language='zh-CN', token=None, now=None, sleep=None,
                     min_interval=DEFAULT_MIN_INTERVAL):
    """Collect the day and week boards; dry-run unless ``execute`` is true."""
    try:
        _validate_job_id(job_id)
    except CaptureInputError:
        raise TrendingInputError('JOB_ID_INVALID') from None
    if pages not in (1, 2):
        raise TrendingInputError('PAGES_INVALID')
    if type(max_requests) is not int or not 1 <= max_requests <= MAX_REQUESTS:
        raise TrendingInputError('MAX_REQUESTS_INVALID')
    if type(min_interval) not in (int, float) or min_interval < DEFAULT_MIN_INTERVAL:
        raise TrendingInputError('MIN_INTERVAL_INVALID')
    if not isinstance(language, str):
        raise TrendingInputError('LANGUAGE_INVALID')
    clock = now or (lambda: datetime.now(timezone.utc))
    pause = sleep or (lambda seconds: __import__('time').sleep(seconds))

    plan = [{'channel': channel, 'page': page}
            for channel in CHANNELS for page in range(1, pages + 1)]
    if not execute:
        return {
            'command': 'trending-capture',
            'status': 'DRY_RUN',
            'archive_dir': str(archive_dir),
            'job_id': job_id,
            'language': language,
            'pages': pages,
            'requests_planned': len(plan),
            'max_requests': max_requests,
            'channels': list(CHANNELS),
        }
    if fetch is None:
        raise TrendingInputError('FETCH_REQUIRED')
    root = Path(archive_dir)
    if not root.is_dir():
        raise TrendingArchiveError('ARCHIVE_DIR_MISSING')
    token_value = token if token is not None else _read_token(None)

    job_dir = root / job_id
    job_dir.mkdir(mode=0o700, exist_ok=True)
    (job_dir / 'raw').mkdir(mode=0o700, exist_ok=True)

    attempts = []
    collected = {}
    requests_used = 0
    for channel in CHANNELS:
        captured_at = clock()
        pages_for_channel = []
        for page in range(1, pages + 1):
            entries = None
            error_code = None
            for attempt in range(1, MAX_ATTEMPTS + 1):
                if requests_used >= max_requests:
                    error_code = 'BUDGET_EXHAUSTED'
                    break
                requests_used += 1
                record = {
                    'channel': channel, 'page': page, 'attempt': attempt,
                    'requested_at': _iso(clock()),
                    'http_status': None, 'body_bytes': 0,
                    'payload_sha256': None, 'status': 'FAILED',
                }
                try:
                    response = fetch(build_request(channel, page, token_value,
                                                   language=language))
                except CaptureSecurityError:
                    raise
                except CaptureError as error:
                    record['error_code'] = error.code
                except Exception:
                    record['error_code'] = 'TRANSPORT_ERROR'
                else:
                    body = response.get('body') or b''
                    record['http_status'] = response.get('status')
                    record['body_bytes'] = len(body)
                    record['payload_sha256'] = _sha256(body)
                    if response.get('status') != 200:
                        record['error_code'] = 'HTTP_%s' % response.get('status')
                    else:
                        try:
                            entries = _parse_payload(body, channel, page)
                        except CaptureError as error:
                            record['error_code'] = error.code
                            entries = None
                        else:
                            record['status'] = 'HTTP_200'
                            (job_dir / 'raw' / ('%s-p%d.json' % (channel, page))).write_bytes(body)
                attempts.append(record)
                if entries is not None:
                    break
                if record.get('error_code') == 'BUDGET_EXHAUSTED':
                    break
                if record['http_status'] == 429:
                    pause(min(MAX_RETRY_AFTER, 2.0 * attempt))
                else:
                    pause(min(8.0, 1.0 * attempt))
            if entries is None:
                error_code = error_code or 'PAGE_FAILED'
                pages_for_channel.append({'page': page, 'status': 'FAILED',
                                          'error_code': error_code})
                break
            pages_for_channel.append({'page': page, 'status': 'HTTP_200',
                                      'entries': len(entries)})
            pages_for_channel[-1]['entries_list'] = entries
            if requests_used < max_requests:
                pause(min_interval)
        good = [p for p in pages_for_channel if p['status'] == 'HTTP_200']
        merged = []
        seen = set()
        for page_info in good:
            for entry in page_info.pop('entries_list'):
                if entry['tmdb_id'] in seen:
                    continue
                seen.add(entry['tmdb_id'])
                merged.append(entry)
        status = 'succeeded' if len(good) == pages else ('partial' if good else 'failed')
        collected[channel] = {
            'captured_at': _iso(captured_at),
            'status': status,
            'pages': pages_for_channel,
            'entries': merged,
        }

    manifest = {
        'schema': TRENDING_SCHEMA,
        'job_id': job_id,
        'language': language,
        'pages': pages,
        'requests_used': requests_used,
        'max_requests': max_requests,
        'attempts': attempts,
        'channels': {
            channel: {
                'captured_at': info['captured_at'],
                'status': info['status'],
                'pages': info['pages'],
                'entry_count': len(info['entries']),
                'payload_sha256': _sha256(_canonical(info['entries'])),
            } for channel, info in collected.items()
        },
    }
    job = {
        'schema': TRENDING_JOB_SCHEMA,
        'job_id': job_id,
        'language': language,
        'pages': pages,
        'max_requests': max_requests,
        'input_hash': _sha256(_canonical({'job_id': job_id, 'language': language, 'pages': pages})),
    }
    (job_dir / 'job.json').write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding='utf-8')
    (job_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    (job_dir / 'entries.json').write_text(
        json.dumps({channel: info['entries'] for channel, info in collected.items()},
                   indent=2, ensure_ascii=False), encoding='utf-8')
    return {
        'command': 'trending-capture',
        'status': 'COMPLETED',
        'job_id': job_id,
        'archive_dir': str(job_dir),
        'requests_used': requests_used,
        'max_requests': max_requests,
        'channels': {channel: {'status': info['status'],
                               'captured_at': info['captured_at'],
                               'entries': len(info['entries'])}
                     for channel, info in collected.items()},
    }


def load_archive(archive_dir, job_id=None):
    """Read a completed trending archive (read-only)."""
    root = Path(archive_dir)
    job_dir = root / job_id if job_id else root
    manifest_path = job_dir / 'manifest.json'
    entries_path = job_dir / 'entries.json'
    if not manifest_path.is_file() or not entries_path.is_file():
        raise TrendingArchiveError('ARCHIVE_INCOMPLETE')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        entries = json.loads(entries_path.read_text(encoding='utf-8'))
    except ValueError:
        raise TrendingArchiveError('ARCHIVE_NOT_JSON') from None
    if manifest.get('schema') != TRENDING_SCHEMA:
        raise TrendingArchiveError('ARCHIVE_SCHEMA_MISMATCH')
    for channel in CHANNELS:
        info = manifest.get('channels', {}).get(channel)
        if not info or channel not in entries:
            raise TrendingArchiveError('ARCHIVE_CHANNEL_MISSING')
        if info.get('status') not in ('succeeded', 'partial', 'failed'):
            raise TrendingArchiveError('ARCHIVE_STATUS_INVALID')
    return manifest, entries