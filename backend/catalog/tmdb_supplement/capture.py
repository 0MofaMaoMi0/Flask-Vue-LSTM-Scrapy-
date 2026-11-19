"""Bounded, auditable TMDB supplement capture.

Public entry point::

    capture_movies(targets, *, fetch=None, archive_dir=None, job_id,
                   max_requests, **options)

The tool never writes MySQL, never expands the movie catalogue and never looks
movies up by title.  A target is an explicit, already-mapped
``movie_id``/``tmdb_id`` pair.  The default is a side-effect-free dry-run; real
capture requires ``execute=True`` plus an injected ``fetch`` callable and a
runtime token provider.

Fetch contract
--------------
``fetch(request)`` receives exactly::

    {'method': 'GET', 'url': 'https://api.themoviedb.org/3/movie/<id>?...',
     'headers': {'Accept': 'application/json',
                 'Authorization': 'Bearer <runtime token>',
                 'User-Agent': '...'},
     'timeout': 15.0, 'max_bytes': 1500000}

and returns ``{'status': int, 'headers': mapping, 'body': bytes,
'url': final_url}``.  Exceptions are treated as sanitized transport failures;
their message is never stored.  Tests inject deterministic fetch functions;
``urllib_fetch`` is the only implementation that performs HTTP and is used by
the CLI only after its explicit ``--execute`` switch.

Recovery semantics
------------------
The checkpoint is atomically replaced after every budget reservation and target
completion.  A request is counted *before* it is attempted, so a crash can
over-count the budget but can never reset it.  Per-target attempt reservations
are persisted too, so repeated resumes cannot silently grant a fresh retry
budget.  A 429 ``Retry-After`` is persisted as an absolute UTC deadline
(``not_before``/``next_allowed_at``) independently of the transient ``pause``
field; every resume must honor it before any fetch and may only clear it after
the clock has reached it.  Completed raw/normalized files are hash-verified on
resume; corruption is reported and never overwritten.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import re
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

API_ROOT = 'https://api.themoviedb.org/3'
APPEND_FIELDS = ('credits', 'keywords', 'release_dates')
CAPTURE_SCHEMA = 'tmdb-supplement-capture-v1'
CHECKPOINT_SCHEMA = 'tmdb-supplement-capture-checkpoint-v1'
MANIFEST_SCHEMA = 'tmdb-supplement-capture-manifest-v1'
JOB_SCHEMA = 'tmdb-supplement-capture-job-v1'
ATTRIBUTION = {
    'notice': 'This product uses the TMDB API but is not endorsed or certified by TMDB.',
    'license': 'https://developer.themoviedb.org/docs/faq',
}
MAX_TARGETS = 5000
MAX_REQUESTS = 10000
MAX_RETRIES = 2
MAX_BODY_BYTES = 1500000
DEFAULT_TIMEOUT = 15.0
DEFAULT_MIN_INTERVAL = 1.0
DEFAULT_MAX_WAIT = 300.0
DEFAULT_MAX_TOTAL_WAIT = 1800.0
DEFAULT_BACKOFF = 1.0
MAX_BACKOFF = 8.0
DEFAULT_RETRY_AFTER = 60.0
JOB_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
TOKEN_RE = re.compile(r'^[A-Za-z0-9_.-]{20,2048}$')
_MOVIE_PATH_RE = re.compile(r'/3/movie/[1-9][0-9]*$')
TRENDING_PATHS = frozenset(('/3/trending/movie/day', '/3/trending/movie/week'))
TRENDING_PAGES = (1, 2)
TRENDING_PAGE_VALUES = frozenset(str(page) for page in TRENDING_PAGES)
DISCOVER_PATHS = frozenset(('/3/movie/popular', '/3/movie/top_rated', '/3/discover/movie'))
DISCOVER_SORT_BY = frozenset(('popularity.desc', 'vote_average.desc', 'vote_count.desc'))
_REPARSE_ATTRIBUTE = 0x400


class CaptureError(Exception):
    """Base error with a stable machine code and no secret-bearing message."""

    code = 'CAPTURE_ERROR'

    def __init__(self, code=None):
        self.code = code or self.code
        super().__init__(self.code)


class CaptureInputError(CaptureError):
    code = 'INPUT_INVALID'


class CaptureInputConflict(CaptureInputError):
    code = 'INPUT_CONFLICT'


class CaptureArchiveError(CaptureError):
    code = 'ARCHIVE_INVALID'


class CaptureArchiveCorrupt(CaptureArchiveError):
    code = 'ARCHIVE_CORRUPT'


class CaptureInputMismatch(CaptureArchiveError):
    code = 'INPUT_MISMATCH'


class CaptureSecurityError(CaptureError):
    code = 'SECURITY_BOUNDARY'


class ResponseTooLarge(CaptureError):
    code = 'RESPONSE_TOO_LARGE'


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _utc_stamp(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _utc_stamp_precise(epoch):
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _parse_utc_stamp(text):
    if not isinstance(text, str) or not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        value = parsed.timestamp()
    except (OverflowError, OSError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')


def _is_positive_int(value):
    return type(value) is int and value > 0


def _is_reparse(path):
    try:
        stat = path.lstat()
    except OSError:
        return False
    return bool(getattr(stat, 'st_file_attributes', 0) & _REPARSE_ATTRIBUTE)


def _secure_directory(path, *, must_exist):
    """Reject relative/C:/linked roots and prove the path resolves to itself."""
    if not isinstance(path, (str, os.PathLike)):
        raise CaptureInputError('ARCHIVE_PATH_INVALID')
    candidate = Path(path)
    if any(part == '..' for part in candidate.parts):
        raise CaptureSecurityError('ARCHIVE_TRAVERSAL')
    if not candidate.is_absolute():
        raise CaptureSecurityError('ARCHIVE_NOT_ABSOLUTE')
    if candidate.drive.upper().startswith('C:') and must_exist:
        raise CaptureSecurityError('ARCHIVE_ON_SYSTEM_DRIVE')
    exists = candidate.exists()
    if exists:
        if not candidate.is_dir():
            raise CaptureInputError('ARCHIVE_ROOT_NOT_DIRECTORY')
        for item in [candidate] + list(candidate.parents):
            if item.exists() and _is_reparse(item):
                raise CaptureSecurityError('ARCHIVE_REPARSE_POINT')
        resolved = candidate.resolve(strict=True)
        if os.path.normcase(str(resolved)) != os.path.normcase(str(candidate)):
            raise CaptureSecurityError('ARCHIVE_REPARSE_POINT')
    elif must_exist:
        raise CaptureInputError('ARCHIVE_ROOT_MISSING')
    else:
        for item in candidate.parents:
            if item.exists() and _is_reparse(item):
                raise CaptureSecurityError('ARCHIVE_REPARSE_POINT')
    return candidate


def _atomic_write(path, data):
    """Publish complete bytes under ``path`` with a same-directory replace."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(path))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write_new(path, data):
    """Create an immutable archive artifact; never replace an existing one."""
    path = Path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(str(path), flags, 0o600)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.unlink(str(path))
        except OSError:
            pass
        raise


def _read_limited(path, limit=4 * 1024 * 1024):
    path = Path(path)
    if path.is_symlink() or _is_reparse(path):
        raise CaptureArchiveCorrupt('ARCHIVE_LINK_REJECTED')
    try:
        if path.stat().st_size > limit:
            raise CaptureArchiveCorrupt('ARCHIVE_FILE_TOO_LARGE')
        return path.read_bytes()
    except OSError:
        raise CaptureArchiveCorrupt('ARCHIVE_FILE_UNREADABLE') from None


def _load_json_file(path, limit=4 * 1024 * 1024):
    try:
        value = json.loads(_read_limited(path, limit).decode('utf-8'))
    except (ValueError, UnicodeError):
        raise CaptureArchiveCorrupt('ARCHIVE_JSON_INVALID') from None
    if type(value) is not dict:
        raise CaptureArchiveCorrupt('ARCHIVE_JSON_ROOT')
    return value


def _prepare_targets(targets):
    if isinstance(targets, dict):
        raw = [{'movie_id': key, 'tmdb_id': value} for key, value in targets.items()]
    elif isinstance(targets, (list, tuple)):
        raw = []
        for item in targets:
            if isinstance(item, dict):
                if set(item) != {'movie_id', 'tmdb_id'}:
                    raise CaptureInputError('TARGET_KEYS_INVALID')
                raw.append(dict(item))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                raw.append({'movie_id': item[0], 'tmdb_id': item[1]})
            else:
                raise CaptureInputError('TARGET_SHAPE_INVALID')
    else:
        raise CaptureInputError('TARGETS_NOT_LIST')
    if not raw or len(raw) > MAX_TARGETS:
        raise CaptureInputError('TARGET_COUNT_INVALID')
    seen = {}
    by_movie = {}
    by_tmdb = {}
    ordered = []
    duplicates = 0
    for item in raw:
        movie_id = item.get('movie_id')
        tmdb_id = item.get('tmdb_id')
        if not _is_positive_int(movie_id) or not _is_positive_int(tmdb_id):
            raise CaptureInputError('TARGET_ID_INVALID')
        if movie_id > 2 ** 63 - 1 or tmdb_id > 9999999999:
            raise CaptureInputError('TARGET_ID_RANGE')
        pair = (movie_id, tmdb_id)
        if pair in seen:
            duplicates += 1
            continue
        if movie_id in by_movie and by_movie[movie_id] != tmdb_id:
            raise CaptureInputConflict('MOVIE_ID_MULTIPLE_TMDB')
        if tmdb_id in by_tmdb and by_tmdb[tmdb_id] != movie_id:
            raise CaptureInputConflict('TMDB_ID_MULTIPLE_MOVIE')
        seen[pair] = True
        by_movie[movie_id] = tmdb_id
        by_tmdb[tmdb_id] = movie_id
        ordered.append(pair)
    ordered.sort(key=lambda pair: (pair[0], pair[1]))
    return [{'movie_id': pair[0], 'tmdb_id': pair[1]} for pair in ordered], duplicates


def _input_material(targets, job_id, language):
    return {
        'schema': CAPTURE_SCHEMA,
        'job_id': job_id,
        'api_root': API_ROOT,
        'language': language,
        'append_to_response': list(APPEND_FIELDS),
        'targets': targets,
    }


def _validate_job_id(job_id):
    if not isinstance(job_id, str) or not JOB_RE.fullmatch(job_id):
        raise CaptureInputError('JOB_ID_INVALID')


def _validate_language(language):
    if not isinstance(language, str) or not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?', language):
        raise CaptureInputError('LANGUAGE_INVALID')


def _parse_retry_after(value, now):
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            value = value.decode('ascii')
        except UnicodeDecodeError:
            return None
    if type(value) not in (str, int, float) or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
        is_http_date = False
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        seconds = parsed.timestamp() - now
        is_http_date = True
    if seconds != seconds or seconds in (float('inf'), float('-inf')):
        return None
    if seconds < 0:
        return 0.0 if is_http_date else None
    return seconds


def _header(headers, name):
    if not headers:
        return None
    target = name.lower()
    for key, value in headers.items():
        if str(key).lower() == target:
            return value
    return None


def _same_url(left, right):
    try:
        first = urllib.parse.urlsplit(left)
        second = urllib.parse.urlsplit(right)
    except (TypeError, ValueError):
        return False
    return (
        first.scheme.lower() == second.scheme.lower()
        and first.netloc.lower() == second.netloc.lower()
        and first.path == second.path
        and urllib.parse.parse_qs(first.query, keep_blank_values=True) == urllib.parse.parse_qs(second.query, keep_blank_values=True)
        and first.fragment == second.fragment
    )


def _request_url(tmdb_id, language):
    return API_ROOT + '/movie/' + str(tmdb_id) + '?language=' + urllib.parse.quote(language, safe='-') + '&append_to_response=' + ','.join(APPEND_FIELDS)


def _validate_envelope(request):
    """Headers and budgets shared by the movie and the trending transports."""
    headers = dict(request.get('headers') or {})
    if set(headers) != {'Accept', 'Authorization', 'User-Agent'}:
        raise CaptureSecurityError('HEADERS_NOT_ALLOWED')
    timeout = request.get('timeout')
    max_bytes = request.get('max_bytes')
    if type(timeout) not in (int, float) or timeout <= 0 or not 0 < timeout <= 60:
        raise CaptureSecurityError('TIMEOUT_INVALID')
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_BODY_BYTES:
        raise CaptureSecurityError('BODY_LIMIT_INVALID')
    return headers, timeout, max_bytes


def _validate_movie_request(request):
    url = request.get('url')
    if not isinstance(url, str):
        raise CaptureSecurityError('URL_INVALID')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc != 'api.themoviedb.org' or parsed.fragment:
        raise CaptureSecurityError('URL_NOT_ALLOWED')
    if not _MOVIE_PATH_RE.fullmatch(parsed.path):
        raise CaptureSecurityError('URL_NOT_ALLOWED')
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != {'language', 'append_to_response'}:
        raise CaptureSecurityError('URL_QUERY_NOT_ALLOWED')
    if query['append_to_response'] != [','.join(APPEND_FIELDS)]:
        raise CaptureSecurityError('APPEND_SET_NOT_ALLOWED')
    headers, timeout, max_bytes = _validate_envelope(request)
    return url, headers, timeout, max_bytes


def _validate_trending_request(request):
    """Only the two fixed trending paths, page 1-2, language only."""
    url = request.get('url')
    if not isinstance(url, str):
        raise CaptureSecurityError('URL_INVALID')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc != 'api.themoviedb.org' or parsed.fragment:
        raise CaptureSecurityError('URL_NOT_ALLOWED')
    if parsed.path not in TRENDING_PATHS:
        raise CaptureSecurityError('URL_NOT_ALLOWED')
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != {'language', 'page'}:
        raise CaptureSecurityError('URL_QUERY_NOT_ALLOWED')
    pages = query.get('page') or []
    if len(pages) != 1 or pages[0] not in TRENDING_PAGE_VALUES:
        raise CaptureSecurityError('PAGE_NOT_ALLOWED')
    try:
        _validate_language(query['language'][0])
    except CaptureInputError:
        raise CaptureSecurityError('LANGUAGE_NOT_ALLOWED') from None
    headers, timeout, max_bytes = _validate_envelope(request)
    return url, headers, timeout, max_bytes


def _validate_discover_request(request):
    """Bounded validation for TMDB popular / top_rated / discover list queries."""
    url = request.get('url')
    if not isinstance(url, str):
        raise CaptureSecurityError('URL_INVALID')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc != 'api.themoviedb.org' or parsed.fragment:
        raise CaptureSecurityError('URL_NOT_ALLOWED')
    if parsed.path not in DISCOVER_PATHS:
        raise CaptureSecurityError('URL_NOT_ALLOWED')
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if 'language' not in query or 'page' not in query:
        raise CaptureSecurityError('URL_QUERY_NOT_ALLOWED')
    pages = query.get('page') or []
    if len(pages) != 1 or not pages[0].isdigit():
        raise CaptureSecurityError('PAGE_NOT_ALLOWED')
    page_num = int(pages[0])
    if not 1 <= page_num <= 500:
        raise CaptureSecurityError('PAGE_NOT_ALLOWED')
    langs = query.get('language') or []
    if len(langs) != 1:
        raise CaptureSecurityError('LANGUAGE_NOT_ALLOWED')
    try:
        _validate_language(langs[0])
    except CaptureInputError:
        raise CaptureSecurityError('LANGUAGE_NOT_ALLOWED') from None

    if parsed.path in ('/3/movie/popular', '/3/movie/top_rated'):
        if set(query) != {'language', 'page'}:
            raise CaptureSecurityError('URL_QUERY_NOT_ALLOWED')
    else:
        allowed = {'language', 'page', 'include_adult', 'sort_by', 'vote_count.gte', 'vote_average.gte'}
        if not set(query).issubset(allowed) or 'include_adult' not in query:
            raise CaptureSecurityError('URL_QUERY_NOT_ALLOWED')
        if query['include_adult'] != ['false']:
            raise CaptureSecurityError('URL_QUERY_NOT_ALLOWED')
        if 'sort_by' in query:
            sorts = query['sort_by']
            if len(sorts) != 1 or sorts[0] not in DISCOVER_SORT_BY:
                raise CaptureSecurityError('URL_QUERY_NOT_ALLOWED')
        if 'vote_count.gte' in query:
            vals = query['vote_count.gte']
            if len(vals) != 1:
                raise CaptureSecurityError('URL_QUERY_NOT_ALLOWED')
            try:
                vc = float(vals[0])
            except ValueError:
                raise CaptureSecurityError('URL_QUERY_NOT_ALLOWED') from None
            if not math.isfinite(vc) or vc < 0:
                raise CaptureSecurityError('URL_QUERY_NOT_ALLOWED')
        if 'vote_average.gte' in query:
            vals = query['vote_average.gte']
            if len(vals) != 1:
                raise CaptureSecurityError('URL_QUERY_NOT_ALLOWED')
            try:
                va = float(vals[0])
            except ValueError:
                raise CaptureSecurityError('URL_QUERY_NOT_ALLOWED') from None
            if not math.isfinite(va) or not 0.0 <= va <= 10.0:
                raise CaptureSecurityError('URL_QUERY_NOT_ALLOWED')

    headers, timeout, max_bytes = _validate_envelope(request)
    return url, headers, timeout, max_bytes


def _validate_request(request):
    """Shared request validation; every HTTPS transport goes through this."""
    kind = request.get('kind')
    if kind == 'trending':
        return _validate_trending_request(request)
    if kind == 'discover':
        return _validate_discover_request(request)
    return _validate_movie_request(request)


def _proxy_map(proxy_url):
    """Empty by default: env/system proxies never apply to capture traffic."""
    if proxy_url is None:
        return {}
    if not isinstance(proxy_url, str) or not proxy_url:
        raise CaptureInputError('PROXY_URL_INVALID')
    parsed = urllib.parse.urlsplit(proxy_url)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname
            or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        raise CaptureInputError('PROXY_URL_INVALID')
    return {'https': proxy_url}


def _build_opener(proxy_map):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None

    context = ssl.create_default_context()
    return urllib.request.build_opener(
        NoRedirect,
        urllib.request.HTTPSHandler(context=context),
        urllib.request.ProxyHandler(dict(proxy_map)),
    )


def make_urllib_fetch(proxy_url=None):
    """HTTPS transport; the proxy stays disabled unless a URL is configured."""
    proxy_map = _proxy_map(proxy_url)

    def fetch(request):
        url, headers, timeout, max_bytes = _validate_request(request)
        opener = _build_opener(proxy_map)
        try:
            response = opener.open(urllib.request.Request(url, headers=headers, method='GET'), timeout=timeout)
            final_url = response.geturl()
            status = getattr(response, 'status', response.getcode())
            body = response.read(max_bytes + 1)
            response_headers = dict(response.headers.items())
            response.close()
        except urllib.error.HTTPError as error:
            status = error.code
            final_url = error.geturl()
            response_headers = dict(error.headers.items()) if error.headers else {}
            body = error.read(max_bytes + 1)
            error.close()
        except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError):
            raise CaptureError('TRANSPORT_ERROR') from None
        if len(body) > max_bytes:
            raise ResponseTooLarge()
        return {
            'status': status,
            'headers': response_headers,
            'body': body,
            'url': final_url,
        }

    return fetch


urllib_fetch = make_urllib_fetch(None)


def make_keepalive_fetch(proxy_url=None):
    """Same validation and caps as :func:`make_urllib_fetch`, one TLS tunnel.

    A fresh ``urllib`` opener per request costs a full CONNECT + TLS handshake.
    Through the local egress proxy that handshake dominates the run (measured
    ~11-13 s versus ~0.5 s once the tunnel exists), so this transport keeps a
    single validated tunnel alive and re-opens it only when the peer closes it.
    Every request is still checked by ``_validate_request`` first, so the host,
    path, header set, timeout and body cap cannot be relaxed by callers.
    """
    proxy_map = _proxy_map(proxy_url)
    proxy = proxy_map.get('https')
    parsed = urllib.parse.urlsplit(proxy) if proxy else None
    state = {'connection': None}

    def close():
        connection = state['connection']
        state['connection'] = None
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def connect(timeout):
        context = ssl.create_default_context()
        if parsed is not None:
            connection = http.client.HTTPSConnection(
                parsed.hostname, parsed.port or 443, timeout=timeout,
                context=context)
            connection.set_tunnel('api.themoviedb.org', 443)
        else:
            connection = http.client.HTTPSConnection(
                'api.themoviedb.org', 443, timeout=timeout, context=context)
        state['connection'] = connection
        connection.connect()
        return connection

    def once(request, url, headers, timeout, max_bytes, attempt):
        target = urllib.parse.urlsplit(url)
        path = target.path + ('?' + target.query if target.query else '')
        try:
            connection = state['connection'] or connect(timeout)
            connection.timeout = timeout
            if connection.sock is not None:
                connection.sock.settimeout(timeout)
            connection.request('GET', path, headers=dict(headers, Host=target.netloc))
            response = connection.getresponse()
            status = response.status
            response_headers = dict(response.getheaders())
            body = response.read(max_bytes + 1)
            if response.will_close:
                close()
        except (http.client.HTTPException, ssl.SSLError, OSError):
            close()
            if attempt == 1:
                return once(request, url, headers, timeout, max_bytes, 2)
            raise CaptureError('TRANSPORT_ERROR') from None
        if len(body) > max_bytes:
            close()
            raise ResponseTooLarge()
        return {
            'status': status,
            'headers': response_headers,
            'body': body,
            'url': url,
        }

    def fetch(request):
        url, headers, timeout, max_bytes = _validate_request(request)
        return once(request, url, headers, timeout, max_bytes, 1)

    return fetch


keepalive_fetch = make_keepalive_fetch(None)


_DEFAULT_OPTIONS = {
    'execute': False,
    'dry_run': None,
    'language': 'zh-CN',
    'token_provider': None,
    'normalize': None,
    'clock': time.time,
    'sleep': time.sleep,
    'min_interval': DEFAULT_MIN_INTERVAL,
    'max_retries': MAX_RETRIES,
    'max_wait_seconds': DEFAULT_MAX_WAIT,
    'max_total_wait_seconds': DEFAULT_MAX_TOTAL_WAIT,
    'timeout_seconds': DEFAULT_TIMEOUT,
    'max_body_bytes': MAX_BODY_BYTES,
    'backoff_seconds': DEFAULT_BACKOFF,
    'max_backoff_seconds': MAX_BACKOFF,
    'default_retry_after': DEFAULT_RETRY_AFTER,
}


def _coerce_options(options):
    unknown = set(options) - set(_DEFAULT_OPTIONS)
    if unknown:
        raise TypeError('UNKNOWN_OPTIONS:' + ','.join(sorted(unknown)))
    cfg = dict(_DEFAULT_OPTIONS)
    cfg.update(options)
    if cfg['dry_run'] is not None:
        if type(cfg['dry_run']) is not bool:
            raise CaptureInputError('DRY_RUN_FLAG_INVALID')
        expected = not cfg['dry_run']
        if type(cfg['execute']) is not bool:
            raise CaptureInputError('EXECUTE_FLAG_INVALID')
        if 'execute' in options and cfg['execute'] != expected:
            raise CaptureInputError('EXECUTE_FLAG_CONFLICT')
        cfg['execute'] = expected
    if type(cfg['execute']) is not bool:
        raise CaptureInputError('EXECUTE_FLAG_INVALID')
    _validate_language(cfg['language'])
    if type(cfg['min_interval']) not in (int, float) or not 1.0 <= cfg['min_interval'] <= 3600:
        raise CaptureInputError('MIN_INTERVAL_INVALID')
    if type(cfg['max_retries']) is not int or not 0 <= cfg['max_retries'] <= MAX_RETRIES:
        raise CaptureInputError('MAX_RETRIES_INVALID')
    for key in ('max_wait_seconds', 'max_total_wait_seconds', 'timeout_seconds',
                'backoff_seconds', 'max_backoff_seconds', 'default_retry_after'):
        value = cfg[key]
        if type(value) not in (int, float) or not value > 0:
            raise CaptureInputError('OPTION_INVALID:' + key)
    if cfg['timeout_seconds'] > 60:
        raise CaptureInputError('TIMEOUT_TOO_LARGE')
    if type(cfg['max_body_bytes']) is not int or not 0 < cfg['max_body_bytes'] <= MAX_BODY_BYTES:
        raise CaptureInputError('MAX_BODY_BYTES_INVALID')
    if cfg['max_backoff_seconds'] < cfg['backoff_seconds']:
        raise CaptureInputError('BACKOFF_INVALID')
    if cfg['token_provider'] is not None and not callable(cfg['token_provider']):
        raise CaptureInputError('TOKEN_PROVIDER_INVALID')
    if cfg['normalize'] is not None and not callable(cfg['normalize']):
        raise CaptureInputError('NORMALIZER_INVALID')
    if not callable(cfg['clock']) or not callable(cfg['sleep']):
        raise CaptureInputError('CLOCK_INVALID')
    return cfg


def _read_token(provider):
    if provider is None:
        provider = lambda: os.environ.get('TMDB_READ_TOKEN', '')
    try:
        token = provider()
    except Exception:
        raise CaptureInputError('TOKEN_PROVIDER_FAILED') from None
    if not isinstance(token, str):
        raise CaptureInputError('TOKEN_INVALID')
    token = token.strip()
    if token.startswith('Bearer '):
        token = token[7:].strip()
    if not TOKEN_RE.fullmatch(token):
        raise CaptureInputError('TOKEN_INVALID')
    return token


def _lazy_normalizer():
    try:
        from .normalize import normalize_movie
        return normalize_movie
    except ImportError:
        try:
            from catalog.tmdb_supplement.normalize import normalize_movie
            return normalize_movie
        except ImportError:
            return None


class _Capture:
    def __init__(self, targets, fetch, archive_dir, job_id, max_requests, cfg):
        self.targets = targets
        self.fetch = fetch
        self.archive_dir = archive_dir
        self.job_id = job_id
        self.max_requests = max_requests
        self.cfg = cfg
        self.clock = cfg['clock']
        self.sleep = cfg['sleep']
        self.input_material = _input_material(targets, job_id, cfg['language'])
        self.input_hash = _sha256(_canonical(self.input_material))
        self.root = None
        self.job_dir = None
        self.state = None
        self.manifest = None
        self.attempts = []
        self.target_attempts = {}
        self.manifest_attempt_counts = {}
        self.results = {}
        self.requests_this_run = 0
        self.waited_this_run = 0.0
        self.token = None
        self.normalizer = cfg['normalize'] or _lazy_normalizer()
        self.pause_reason = None
        self.fatal_reason = None

    # ---------- filesystem / state ----------

    def _paths(self, create=True):
        if self.root is None:
            self.root = _secure_directory(self.archive_dir, must_exist=True)
            self.job_dir = self.root / self.job_id
            if self.job_dir.exists():
                if not self.job_dir.is_dir() or _is_reparse(self.job_dir):
                    raise CaptureSecurityError('JOB_DIR_LINK_OR_FILE')
                resolved = self.job_dir.resolve(strict=True)
                if os.path.normcase(str(resolved)) != os.path.normcase(str(self.job_dir)):
                    raise CaptureSecurityError('JOB_DIR_REPARSE_POINT')
            elif create:
                self.job_dir.mkdir(mode=0o700)
            else:
                return self.job_dir
            if not self.job_dir.is_dir() or _is_reparse(self.job_dir):
                raise CaptureSecurityError('JOB_DIR_INVALID')
            for name in ('raw', 'normalized'):
                directory = self.job_dir / name
                if directory.exists():
                    if not directory.is_dir() or _is_reparse(directory):
                        raise CaptureSecurityError('ARCHIVE_SUBDIR_INVALID')
                elif create:
                    directory.mkdir(mode=0o700)
        return self.job_dir

    def _ensure_job(self, read_only=False):
        jobdir = self._paths(create=not read_only)
        job_path = jobdir / 'job.json'
        checkpoint_path = jobdir / 'checkpoint.json'
        manifest_path = jobdir / 'manifest.json'
        if checkpoint_path.exists():
            job = _load_json_file(job_path)
            if job.get('schema') != JOB_SCHEMA or job.get('job_id') != self.job_id:
                raise CaptureArchiveCorrupt('JOB_FILE_INVALID')
            if job.get('input_hash') != self.input_hash:
                raise CaptureInputMismatch()
            if job.get('targets') != self.targets:
                raise CaptureInputMismatch()
            state = _load_json_file(checkpoint_path)
            self._validate_state(state)
            self.state = state
            self.target_attempts = dict(state.get('target_attempts') or {})
            self._verify_artifacts()
            if manifest_path.exists():
                manifest = _load_json_file(manifest_path)
                if manifest.get('schema') != MANIFEST_SCHEMA or manifest.get('job_id') != self.job_id or manifest.get('input_hash') != self.input_hash:
                    raise CaptureArchiveCorrupt('MANIFEST_INVALID')
                self.manifest = manifest
                self.attempts = list(manifest.get('attempts') or [])
                self._build_manifest_attempt_counts()
                for key, count in self.manifest_attempt_counts.items():
                    self.target_attempts[key] = max(self.target_attempts.get(key, 0), count)
                self.state['target_attempts'] = dict(self.target_attempts)
                self._verify_manifest_attempts()
            else:
                raise CaptureArchiveCorrupt('MANIFEST_MISSING')
        elif read_only:
            if any(jobdir.iterdir()):
                raise CaptureArchiveCorrupt('CHECKPOINT_MISSING')
        else:
            if job_path.exists() or manifest_path.exists():
                raise CaptureArchiveCorrupt('CHECKPOINT_MISSING')
            unexpected = [
                item for item in jobdir.iterdir()
                if item.name not in ('raw', 'normalized') or (item.is_dir() and any(item.iterdir()))
            ]
            if unexpected:
                raise CaptureArchiveCorrupt('JOB_DIR_NOT_EMPTY')
            now = _utc_stamp(self._now())
            job = {
                'schema': JOB_SCHEMA,
                'job_id': self.job_id,
                'input_hash': self.input_hash,
                'created_at': now,
                'targets': self.targets,
                'language': self.cfg['language'],
                'append_to_response': list(APPEND_FIELDS),
                'source': 'tmdb-api-v3',
                'api_root': API_ROOT,
                'license_ref': ATTRIBUTION['license'],
            }
            _atomic_write(job_path, _json_bytes(job))
            self.state = {
                'schema': CHECKPOINT_SCHEMA,
                'job_id': self.job_id,
                'input_hash': self.input_hash,
                'created_at': now,
                'updated_at': now,
                'status': 'RUNNING',
                'request_count': 0,
                'attempt_seq': 0,
                'waited_seconds': 0.0,
                'last_request_at': None,
                'target_attempts': {},
                'not_before': None,
                'not_before_utc': None,
                'next_allowed_at': None,
                'completed': {},
                'pause': None,
            }
            self.manifest = {
                'schema': MANIFEST_SCHEMA,
                'job_id': self.job_id,
                'input_hash': self.input_hash,
                'created_at': now,
                'attribution': dict(ATTRIBUTION),
                'source': 'tmdb-api-v3',
                'api_root': API_ROOT,
                'license_ref': ATTRIBUTION['license'],
                'language': self.cfg['language'],
                'append_to_response': list(APPEND_FIELDS),
                'attempts': [],
            }
            self.attempts = []
            self.target_attempts = {}
            self.manifest_attempt_counts = {}
            self._save_state()
            self._save_manifest()

    def _validate_state(self, state):
        if state.get('schema') != CHECKPOINT_SCHEMA:
            raise CaptureArchiveCorrupt('CHECKPOINT_SCHEMA')
        if state.get('job_id') != self.job_id or state.get('input_hash') != self.input_hash:
            raise CaptureInputMismatch()
        for key in ('request_count', 'attempt_seq'):
            if type(state.get(key)) is not int or state[key] < 0:
                raise CaptureArchiveCorrupt('CHECKPOINT_COUNTER')
        if type(state.get('waited_seconds')) not in (int, float) or state['waited_seconds'] < 0:
            raise CaptureArchiveCorrupt('CHECKPOINT_WAIT')
        if type(state.get('completed')) is not dict:
            raise CaptureArchiveCorrupt('CHECKPOINT_COMPLETED')
        stored = state.get('state_sha256')
        if stored is not None:
            copy = dict(state)
            copy.pop('state_sha256', None)
            if stored != _sha256(_canonical(copy)):
                raise CaptureArchiveCorrupt('CHECKPOINT_HASH_MISMATCH')
        if state.get('status') not in {'RUNNING', 'PAUSED', 'COMPLETED', 'PARTIAL', 'FAILED', 'AUTH_FAILED'}:
            raise CaptureArchiveCorrupt('CHECKPOINT_STATUS')
        self._validate_retry_deadline(state)
        self._validate_target_attempts(state)

    def _validate_retry_deadline(self, state):
        value = state.get('not_before')
        if value is None:
            if state.get('not_before_utc') is not None or state.get('next_allowed_at') is not None:
                raise CaptureArchiveCorrupt('CHECKPOINT_NOT_BEFORE')
            state['not_before'] = None
            state['not_before_utc'] = None
            state['next_allowed_at'] = None
            return
        if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0:
            raise CaptureArchiveCorrupt('CHECKPOINT_NOT_BEFORE')
        text = state.get('not_before_utc') or state.get('next_allowed_at')
        parsed = _parse_utc_stamp(text)
        if parsed is None or abs(parsed - float(value)) > 0.001:
            raise CaptureArchiveCorrupt('CHECKPOINT_NOT_BEFORE')
        state['not_before'] = float(value)
        state['not_before_utc'] = text
        state['next_allowed_at'] = text

    def _validate_target_attempts(self, state):
        attempts = state.get('target_attempts')
        if attempts is None:
            attempts = {}
        if type(attempts) is not dict:
            raise CaptureArchiveCorrupt('CHECKPOINT_TARGET_ATTEMPTS')
        wanted = {str(item['tmdb_id']) for item in self.targets}
        for key, value in attempts.items():
            if (not isinstance(key, str) or key not in wanted
                    or type(value) is not int or value < 0 or value > MAX_REQUESTS):
                raise CaptureArchiveCorrupt('CHECKPOINT_TARGET_ATTEMPTS')
        state['target_attempts'] = dict(attempts)

    def _verify_artifacts(self):
        wanted = {str(item['tmdb_id']): item for item in self.targets}
        for key, record in self.state['completed'].items():
            target = wanted.get(key)
            if target is None or record.get('movie_id') != target['movie_id'] or record.get('tmdb_id') != target['tmdb_id']:
                raise CaptureInputMismatch()
            raw_path = record.get('raw_path')
            if not isinstance(raw_path, str) or not raw_path.startswith('raw/%s/' % key):
                raise CaptureArchiveCorrupt('RAW_PATH_MISSING')
            raw = self._safe_job_file(raw_path)
            if _sha256(_read_limited(raw)) != record.get('raw_sha256'):
                raise CaptureArchiveCorrupt('RAW_HASH_MISMATCH')
            normalized = record.get('normalized_path')
            if normalized is not None:
                if not isinstance(normalized, str) or not normalized.startswith('normalized/%s/' % key):
                    raise CaptureArchiveCorrupt('NORMALIZED_PATH_INVALID')
                normalized_file = self._safe_job_file(normalized)
                if _sha256(_read_limited(normalized_file)) != record.get('normalized_sha256'):
                    raise CaptureArchiveCorrupt('NORMALIZED_HASH_MISMATCH')

    def _verify_manifest_attempts(self):
        for record in self.attempts:
            relative = record.get('raw_path') if isinstance(record, dict) else None
            if relative is None:
                continue
            path = self._safe_job_file(relative)
            expected = record.get('body_sha256')
            if not isinstance(expected, str) or _sha256(_read_limited(path)) != expected:
                raise CaptureArchiveCorrupt('ATTEMPT_HASH_MISMATCH')

    def _build_manifest_attempt_counts(self):
        counts = {}
        for record in self.attempts:
            if not isinstance(record, dict):
                continue
            tmdb_id = record.get('tmdb_id')
            if _is_positive_int(tmdb_id):
                key = str(tmdb_id)
                if key in {str(item['tmdb_id']) for item in self.targets}:
                    counts[key] = counts.get(key, 0) + 1
        self.manifest_attempt_counts = counts

    def _target_attempts(self, target):
        key = str(target['tmdb_id'])
        stored = self.target_attempts.get(key, 0)
        return max(int(stored), int(self.manifest_attempt_counts.get(key, 0)))

    def _target_last_status(self, target):
        for record in reversed(self.attempts):
            if isinstance(record, dict) and record.get('tmdb_id') == target['tmdb_id']:
                return record.get('status')
        return None

    def _safe_job_file(self, relative):
        if not isinstance(relative, str) or not relative or relative.startswith('/') or '\\' in relative or '..' in relative.split('/'):
            raise CaptureArchiveCorrupt('ARCHIVE_PATH_INVALID')
        candidate = self.job_dir / relative
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            raise CaptureArchiveCorrupt('ARCHIVE_FILE_MISSING') from None
        if os.path.normcase(str(resolved)) != os.path.normcase(str(candidate)):
            raise CaptureSecurityError('ARCHIVE_FILE_REPARSE')
        if not candidate.is_file() or _is_reparse(candidate):
            raise CaptureArchiveCorrupt('ARCHIVE_FILE_INVALID')
        return candidate

    def _now(self):
        raw = self.clock()
        if isinstance(raw, bool):
            raise CaptureInputError('CLOCK_INVALID')
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError):
            raise CaptureInputError('CLOCK_INVALID') from None
        if not math.isfinite(value) or value < 0 or value > 253402300799:
            raise CaptureInputError('CLOCK_INVALID')
        return value

    def _save_state(self, status=None):
        if status is not None:
            self.state['status'] = status
        self.state['updated_at'] = _utc_stamp(self._now())
        clean = dict(self.state)
        clean.pop('state_sha256', None)
        self.state['state_sha256'] = _sha256(_canonical(clean))
        _atomic_write(self.job_dir / 'checkpoint.json', _json_bytes(self.state))

    def _save_manifest(self):
        self.manifest['attempts'] = self.attempts
        self.manifest['next_allowed_at'] = self.state.get('next_allowed_at') if self.state else None
        self.manifest['not_before'] = self.state.get('not_before') if self.state else None
        self.manifest['updated_at'] = _utc_stamp(self._now())
        _atomic_write(self.job_dir / 'manifest.json', _json_bytes(self.manifest))

    # ---------- pacing / budget ----------

    def _remaining_budget(self):
        return self.max_requests - self.state['request_count']

    def _retry_deadline(self):
        value = self.state.get('not_before') if self.state else None
        if value is None:
            return None
        if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(float(value)):
            raise CaptureArchiveCorrupt('CHECKPOINT_NOT_BEFORE')
        return float(value)

    def _set_retry_deadline(self, deadline):
        deadline = float(deadline)
        if not math.isfinite(deadline) or deadline < 0 or deadline > 253402300799:
            raise CaptureInputError('CLOCK_INVALID')
        text = _utc_stamp_precise(deadline)
        self.state['not_before'] = round(deadline, 6)
        self.state['not_before_utc'] = text
        self.state['next_allowed_at'] = text
        self._save_state()

    def _clear_retry_deadline(self):
        if self.state.get('not_before') is None and self.state.get('next_allowed_at') is None:
            return
        self.state['not_before'] = None
        self.state['not_before_utc'] = None
        self.state['next_allowed_at'] = None
        self._save_state()

    def _enforce_retry_deadline(self, *, reason='RETRY_AFTER'):
        deadline = self._retry_deadline()
        if deadline is None:
            return True
        remaining = deadline - self._now()
        if remaining > 0:
            if not self._wait(remaining, reason=reason):
                return False
            if self._now() < deadline:
                self._pause('CLOCK_ROLLBACK', requested_wait=round(remaining, 3), wait_reason=reason)
                return False
        self._clear_retry_deadline()
        return True

    def _pause(self, reason, **detail):
        self.pause_reason = reason
        if self.state.get('not_before') is not None:
            detail.setdefault('not_before', self.state.get('not_before'))
            detail.setdefault('next_allowed_at', self.state.get('next_allowed_at'))
        self.state['pause'] = dict(reason=reason, **detail)
        self._save_state('PAUSED')
        return 'PAUSED'

    def _wait(self, seconds, *, reason):
        seconds = float(seconds)
        if seconds <= 0:
            return True
        if seconds > self.cfg['max_wait_seconds']:
            self._pause('WAIT_CAP_EXCEEDED', requested_wait=round(seconds, 3), wait_reason=reason)
            return False
        if self.state['waited_seconds'] + seconds > self.cfg['max_total_wait_seconds']:
            self._pause('TOTAL_WAIT_CAP_EXCEEDED', requested_wait=round(seconds, 3), wait_reason=reason)
            return False
        self.sleep(seconds)
        self.state['waited_seconds'] = round(self.state['waited_seconds'] + seconds, 6)
        self.waited_this_run = round(self.waited_this_run + seconds, 6)
        self._save_state()
        return True

    def _rate_wait(self):
        last = self.state.get('last_request_at')
        if last is None:
            return 0.0
        if type(last) not in (int, float) or isinstance(last, bool) or not math.isfinite(float(last)):
            raise CaptureArchiveCorrupt('CHECKPOINT_LAST_REQUEST')
        now = self._now()
        elapsed = now - float(last)
        if elapsed < 0:
            return float(self.cfg['min_interval']) + abs(elapsed)
        return max(0.0, float(self.cfg['min_interval']) - elapsed)

    def _reserve(self, target):
        if self._remaining_budget() <= 0:
            return None
        if self._target_attempts(target) >= self.cfg['max_retries'] + 1:
            return None
        if not self._enforce_retry_deadline():
            return None
        wait = self._rate_wait()
        if wait > 0 and not self._wait(wait, reason='MIN_INTERVAL'):
            return None
        seq = self.state['attempt_seq'] + 1
        key = str(target['tmdb_id'])
        self.target_attempts[key] = self._target_attempts(target) + 1
        self.state['target_attempts'] = dict(self.target_attempts)
        self.state['attempt_seq'] = seq
        self.state['request_count'] += 1
        self.state['last_request_at'] = self._now()
        self.requests_this_run += 1
        self._save_state()
        return seq

    # ---------- attempt / archive ----------

    def _record_attempt(self, *, target, seq, status, http_status, body, error=None,
                        retry_after=None, raw_path=None):
        digest = _sha256(body) if body is not None else None
        record = {
            'attempt': seq,
            'movie_id': target['movie_id'],
            'tmdb_id': target['tmdb_id'],
            'requested_at': self.state['updated_at'],
            'url': _request_url(target['tmdb_id'], self.cfg['language']),
            'http_status': http_status,
            'status': status,
            'body_bytes': len(body) if body is not None else 0,
            'body_sha256': digest,
            'raw_path': raw_path,
            'error': error,
        }
        if retry_after is not None:
            record['retry_after_seconds'] = round(float(retry_after), 3)
        self.attempts.append(record)
        self._save_manifest()
        return record

    def _archive_body(self, target, seq, body):
        relative = 'raw/%s/attempt-%s.json' % (target['tmdb_id'], seq)
        destination = self.job_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if _is_reparse(destination.parent):
            raise CaptureSecurityError('RAW_DIR_REPARSE')
        _write_new(destination, body)
        return relative

    def _fetch_attempt(self, target, seq):
        request = {
            'method': 'GET',
            'url': _request_url(target['tmdb_id'], self.cfg['language']),
            'headers': {
                'Accept': 'application/json',
                'Authorization': 'Bearer ' + self.token,
                'User-Agent': 'MovieInsight/2.0 TMDB supplement capture (bounded; offline tests)',
            },
            'timeout': float(self.cfg['timeout_seconds']),
            'max_bytes': int(self.cfg['max_body_bytes']),
        }
        try:
            response = self.fetch(request)
        except ResponseTooLarge:
            return None, 'BODY_TOO_LARGE'
        except CaptureSecurityError:
            raise
        except Exception as error:
            return None, 'TRANSPORT_' + re.sub(r'[^A-Za-z0-9_]', '', type(error).__name__)[:40]
        if not isinstance(response, dict):
            return None, 'RESPONSE_SHAPE_INVALID'
        status = response.get('status')
        body = response.get('body', b'')
        if type(status) is not int or not 100 <= status <= 599:
            return None, 'HTTP_STATUS_INVALID'
        if not isinstance(body, (bytes, bytearray)):
            return None, 'RESPONSE_BODY_INVALID'
        body = bytes(body)
        headers = response.get('headers') or {}
        if not isinstance(headers, dict):
            return None, 'RESPONSE_HEADERS_INVALID'
        if len(body) > self.cfg['max_body_bytes']:
            return {'status': status, 'headers': headers, 'body': b'', 'url': response.get('url')}, 'BODY_TOO_LARGE'
        final_url = response.get('url')
        if final_url is not None and not _same_url(final_url, request['url']):
            raise CaptureSecurityError('REDIRECT_OR_URL_MISMATCH')
        if 300 <= status < 400:
            raise CaptureSecurityError('REDIRECT_REJECTED')
        return {'status': status, 'headers': headers, 'body': body, 'url': final_url}, None

    def _append_issues(self, payload):
        issues = []
        credits = payload.get('credits')
        if 'credits' not in payload:
            issues.append('APPEND_MISSING_credits')
        elif not isinstance(credits, dict) or not isinstance(credits.get('cast', []), list) or not isinstance(credits.get('crew', []), list):
            issues.append('APPEND_INVALID_credits')
        keywords = payload.get('keywords')
        if 'keywords' not in payload:
            issues.append('APPEND_MISSING_keywords')
        elif not isinstance(keywords, dict) or not isinstance(keywords.get('keywords', []), list):
            issues.append('APPEND_INVALID_keywords')
        releases = payload.get('release_dates')
        if 'release_dates' not in payload:
            issues.append('APPEND_MISSING_release_dates')
        elif not isinstance(releases, dict) or not isinstance(releases.get('results', []), list):
            issues.append('APPEND_INVALID_release_dates')
        if isinstance(payload.get('errors'), list) and payload['errors']:
            issues.append('APPEND_UPSTREAM_ERRORS')
        return issues

    def _normalize_target(self, target, payload, seq, fetched_at, issues):
        if self.normalizer is None:
            issues.append('NORMALIZER_UNAVAILABLE')
            return None, None
        try:
            document = self.normalizer(
                payload=payload,
                movie_id=target['movie_id'],
                tmdb_id=target['tmdb_id'],
                fetched_at=fetched_at,
                job_id=self.job_id,
            )
        except Exception as error:
            issues.append('NORMALIZER_FAILED_' + re.sub(r'[^A-Za-z0-9_]', '', type(error).__name__)[:40])
            return None, None
        if not isinstance(document, dict) or document.get('movie_id') != target['movie_id'] or document.get('tmdb_id') != target['tmdb_id']:
            issues.append('NORMALIZER_IDENTITY_MISMATCH')
            return None, None
        field_status = document.get('field_status')
        if isinstance(field_status, dict):
            for field, value in sorted(field_status.items()):
                if value == 'FAILED':
                    issues.append('NORMALIZER_FIELD_FAILED_' + re.sub(r'[^A-Za-z0-9_]', '_', str(field))[:40])
        if document.get('issues'):
            issues.append('NORMALIZER_ISSUES')
        data = _json_bytes(document)
        relative = 'normalized/%s/attempt-%s.json' % (target['tmdb_id'], seq)
        destination = self.job_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if _is_reparse(destination.parent):
            raise CaptureSecurityError('NORMALIZED_DIR_REPARSE')
        _write_new(destination, data)
        return relative, _sha256(data)

    def _complete_target(self, target, seq, payload, fetched_at, append_issues, raw_relative, attempts):
        issues = list(append_issues)
        normalized_path, normalized_sha = self._normalize_target(target, payload, seq, fetched_at, issues)
        if 'NORMALIZER_IDENTITY_MISMATCH' in issues:
            result = {
                'movie_id': target['movie_id'],
                'tmdb_id': target['tmdb_id'],
                'status': 'FAILED',
                'attempts': attempts,
                'http_status': 200,
                'raw_path': raw_relative,
                'issues': issues,
            }
            self.results[target['tmdb_id']] = result
            return 'FAILED'
        status = 'COMPLETED' if not issues else 'PARTIAL'
        if normalized_path is None:
            status = 'PARTIAL'
        record = {
            'movie_id': target['movie_id'],
            'tmdb_id': target['tmdb_id'],
            'status': status,
            'attempts': attempts,
            'http_status': 200,
            'fetched_at': fetched_at,
            'raw_path': raw_relative,
            'raw_sha256': _sha256(_read_limited(self.job_dir / raw_relative)),
            'normalized_path': normalized_path,
            'normalized_sha256': normalized_sha,
            'issues': issues,
        }
        self.state['completed'][str(target['tmdb_id'])] = record
        self._save_state()
        self.results[target['tmdb_id']] = dict(record, status=status, attempts=attempts)
        return status

    def _attempt_limit_result(self, target, attempts):
        if self._retry_deadline() is not None and self._target_last_status(target) == 'HTTP_429':
            return self._pause('RETRY_LIMIT_REACHED', attempts=attempts, max_attempts=self.cfg['max_retries'] + 1)
        self.results[target['tmdb_id']] = {
            'movie_id': target['movie_id'],
            'tmdb_id': target['tmdb_id'],
            'status': 'FAILED',
            'attempts': attempts,
            'issues': ['RETRY_LIMIT_REACHED'],
        }
        return 'FAILED'

    def _capture_target(self, target):
        max_attempts = self.cfg['max_retries'] + 1
        attempts = self._target_attempts(target)
        if attempts >= max_attempts:
            return self._attempt_limit_result(target, attempts)
        while attempts < max_attempts:
            if self._remaining_budget() <= 0:
                return self._pause('BUDGET_EXHAUSTED')
            seq = self._reserve(target)
            if seq is None:
                if self.pause_reason is not None:
                    return 'PAUSED'
                return self._attempt_limit_result(target, self._target_attempts(target))
            attempts = self._target_attempts(target)
            response, fetch_error = self._fetch_attempt(target, seq)
            if fetch_error is not None:
                if fetch_error == 'BODY_TOO_LARGE':
                    self._record_attempt(target=target, seq=seq, status='FAILED', http_status=response['status'] if response else None, body=None, error=fetch_error)
                    self.results[target['tmdb_id']] = {'movie_id': target['movie_id'], 'tmdb_id': target['tmdb_id'], 'status': 'FAILED', 'attempts': attempts, 'issues': [fetch_error]}
                    return 'FAILED'
                self._record_attempt(target=target, seq=seq, status='TRANSPORT_ERROR', http_status=None, body=None, error=fetch_error)
                if attempts >= max_attempts:
                    self.results[target['tmdb_id']] = {'movie_id': target['movie_id'], 'tmdb_id': target['tmdb_id'], 'status': 'FAILED', 'attempts': attempts, 'issues': [fetch_error]}
                    return 'FAILED'
                if self._remaining_budget() <= 0:
                    return self._pause('BUDGET_EXHAUSTED')
                backoff = min(self.cfg['max_backoff_seconds'], self.cfg['backoff_seconds'] * (2 ** (attempts - 1)))
                if not self._wait(backoff, reason='TRANSPORT_BACKOFF'):
                    return 'PAUSED'
                continue
            status = response['status']
            body = response['body']
            headers = response['headers']
            retry_after = _parse_retry_after(_header(headers, 'Retry-After'), self._now()) if status == 429 else None
            retry_wait = None
            if status == 429:
                retry_wait = retry_after if retry_after is not None else float(self.cfg['default_retry_after'])
                self._set_retry_deadline(self._now() + retry_wait)
            raw_relative = None
            if body and status not in (401, 403):
                raw_relative = self._archive_body(target, seq, body)
            self._record_attempt(target=target, seq=seq, status='HTTP_%s' % status, http_status=status, body=body, retry_after=retry_after, raw_path=raw_relative)

            if status in (401, 403):
                self._save_state('AUTH_FAILED')
                self.fatal_reason = 'AUTH_FAILED'
                self.results[target['tmdb_id']] = {'movie_id': target['movie_id'], 'tmdb_id': target['tmdb_id'], 'status': 'FAILED', 'attempts': attempts, 'http_status': status, 'issues': ['AUTH_FAILED']}
                return 'AUTH_FAILED'
            if status == 429:
                if attempts >= max_attempts:
                    return self._pause('RATE_LIMITED', retry_after=round(retry_wait, 3), attempts=attempts)
                if self._remaining_budget() <= 0:
                    return self._pause('BUDGET_EXHAUSTED')
                if not self._enforce_retry_deadline():
                    return 'PAUSED'
                continue
            if 500 <= status <= 599:
                if attempts >= max_attempts:
                    self.results[target['tmdb_id']] = {'movie_id': target['movie_id'], 'tmdb_id': target['tmdb_id'], 'status': 'FAILED', 'attempts': attempts, 'http_status': status, 'issues': ['UPSTREAM_5XX']}
                    return 'FAILED'
                if self._remaining_budget() <= 0:
                    return self._pause('BUDGET_EXHAUSTED')
                backoff = min(self.cfg['max_backoff_seconds'], self.cfg['backoff_seconds'] * (2 ** (attempts - 1)))
                if not self._wait(backoff, reason='SERVER_BACKOFF'):
                    return 'PAUSED'
                continue
            if status != 200:
                code = 'NOT_FOUND' if status == 404 else 'HTTP_%s' % status
                self.results[target['tmdb_id']] = {'movie_id': target['movie_id'], 'tmdb_id': target['tmdb_id'], 'status': 'FAILED', 'attempts': attempts, 'http_status': status, 'raw_path': raw_relative, 'issues': [code]}
                return 'FAILED'

            try:
                payload = json.loads(body.decode('utf-8'))
            except (ValueError, UnicodeError):
                self.results[target['tmdb_id']] = {'movie_id': target['movie_id'], 'tmdb_id': target['tmdb_id'], 'status': 'FAILED', 'attempts': attempts, 'http_status': 200, 'raw_path': raw_relative, 'issues': ['INVALID_JSON']}
                return 'FAILED'
            if type(payload) is not dict or payload.get('success') is False or (
                type(payload.get('status_code')) is int and payload['status_code'] >= 400
            ) or type(payload.get('id')) is not int or payload.get('id') != target['tmdb_id']:
                self.results[target['tmdb_id']] = {'movie_id': target['movie_id'], 'tmdb_id': target['tmdb_id'], 'status': 'FAILED', 'attempts': attempts, 'http_status': 200, 'raw_path': raw_relative, 'issues': ['IDENTITY_OR_UPSTREAM_MISMATCH']}
                return 'FAILED'
            fetched_at = _utc_stamp(self._now())
            append_issues = self._append_issues(payload)
            status_out = self._complete_target(target, seq, payload, fetched_at, append_issues, raw_relative, attempts)
            return status_out
        return 'FAILED'

    # ---------- entry ----------

    def _target_result(self, target):
        key = target['tmdb_id']
        if key in self.results:
            return self.results[key]
        completed = self.state.get('completed', {}).get(str(key)) if self.state else None
        if completed:
            return dict(completed, status='SKIPPED', previous_status=completed.get('status'))
        return {'movie_id': target['movie_id'], 'tmdb_id': target['tmdb_id'], 'status': 'PLANNED', 'attempts': 0}

    def plan(self):
        if self.archive_dir is not None:
            _secure_directory(self.archive_dir, must_exist=False)
        pending = list(self.targets)
        used = 0
        if self.archive_dir is not None:
            root = Path(self.archive_dir)
            job_dir = root / self.job_id
            if job_dir.exists():
                if not job_dir.is_dir() or _is_reparse(job_dir):
                    raise CaptureSecurityError('JOB_DIR_INVALID')
                if (job_dir / 'checkpoint.json').exists():
                    self.job_dir = job_dir
                    self.root = root
                    self._ensure_job(read_only=True)
                    used = self.state['request_count']
                    pending = [item for item in self.targets if str(item['tmdb_id']) not in self.state['completed']]
                elif any(job_dir.iterdir()):
                    raise CaptureArchiveCorrupt('CHECKPOINT_MISSING')
        result = {
            'schema': CAPTURE_SCHEMA,
            'mode': 'dry-run',
            'status': 'PLANNED',
            'job_id': self.job_id,
            'input_hash': self.input_hash,
            'archive_dir': str(self.archive_dir) if self.archive_dir is not None else None,
            'attribution': dict(ATTRIBUTION),
            'source': 'tmdb-api-v3',
            'api_root': API_ROOT,
            'license_ref': ATTRIBUTION['license'],
            'targets': [self._target_result(item) for item in self.targets],
            'pending': pending,
            'request_budget': self.max_requests,
            'requests_used': used,
            'remaining_budget': max(0, self.max_requests - used),
            'next_allowed_at': self.state.get('next_allowed_at') if self.state else None,
            'not_before': self.state.get('not_before') if self.state else None,
            'minimum_requests_needed': len(pending),
            'duplicate_targets': 0,
            'retry_policy': {'max_retries': self.cfg['max_retries'], 'max_attempts': self.cfg['max_retries'] + 1},
            'effective_options': {'language': self.cfg['language'], 'append_to_response': list(APPEND_FIELDS), 'min_interval': self.cfg['min_interval']},
            'warnings': [],
        }
        if used + len(pending) > self.max_requests:
            result['warnings'].append('BUDGET_INSUFFICIENT_FOR_ALL_TARGETS')
        if self.normalizer is None:
            result['warnings'].append('NORMALIZER_UNAVAILABLE_RAW_ONLY')
        return result

    def run(self):
        _validate_job_id(self.job_id)
        self._now()
        self._ensure_job()
        if self.state['status'] == 'COMPLETED' and all(str(item['tmdb_id']) in self.state['completed'] for item in self.targets):
            return self._result('COMPLETED')
        self.state['status'] = 'RUNNING'
        self.state['pause'] = None
        self._save_state('RUNNING')
        for target in self.targets:
            key = str(target['tmdb_id'])
            if key in self.state['completed']:
                self.results[target['tmdb_id']] = dict(self.state['completed'][key], status='SKIPPED', previous_status=self.state['completed'][key].get('status'))
                continue
            if self._remaining_budget() <= 0:
                self._pause('BUDGET_EXHAUSTED')
                break
            outcome = self._capture_target(target)
            if outcome == 'AUTH_FAILED':
                break
            if outcome == 'PAUSED':
                break
        if self.fatal_reason == 'AUTH_FAILED':
            final = 'AUTH_FAILED'
        elif self.pause_reason is not None:
            final = 'PAUSED'
        else:
            statuses = [self._target_result(item) for item in self.targets]
            if all(
                item['status'] == 'COMPLETED'
                or (item['status'] == 'SKIPPED' and item.get('previous_status') == 'COMPLETED')
                for item in statuses
            ):
                final = 'COMPLETED'
            else:
                final = 'PARTIAL'
        self._save_state(final)
        return self._result(final)

    def _result(self, status):
        targets = [self._target_result(item) for item in self.targets]
        completed = sum(1 for item in targets if item['status'] == 'COMPLETED')
        partial = sum(1 for item in targets if item['status'] == 'PARTIAL')
        failed = sum(1 for item in targets if item['status'] == 'FAILED')
        skipped = sum(1 for item in targets if item['status'] == 'SKIPPED')
        remaining = sum(1 for item in targets if item['status'] == 'PLANNED')
        return {
            'schema': CAPTURE_SCHEMA,
            'mode': 'execute',
            'status': status,
            'job_id': self.job_id,
            'input_hash': self.input_hash,
            'archive_dir': str(self.job_dir) if self.job_dir is not None else None,
            'checkpoint': str(self.job_dir / 'checkpoint.json') if self.job_dir is not None else None,
            'manifest': str(self.job_dir / 'manifest.json') if self.job_dir is not None else None,
            'attribution': dict(ATTRIBUTION),
            'source': 'tmdb-api-v3',
            'api_root': API_ROOT,
            'license_ref': ATTRIBUTION['license'],
            'request_budget': self.max_requests,
            'requests_used': self.state['request_count'] if self.state else 0,
            'requests_this_run': self.requests_this_run,
            'waited_seconds': round(self.waited_this_run, 3),
            'waited_seconds_total': round(self.state['waited_seconds'], 3) if self.state else 0,
            'next_allowed_at': self.state.get('next_allowed_at') if self.state else None,
            'not_before': self.state.get('not_before') if self.state else None,
            'targets': targets,
            'coverage': {
                'completed': completed,
                'partial': partial,
                'failed': failed,
                'skipped': skipped,
                'remaining': remaining,
                'covered_total': completed + partial + skipped,
                'uncovered_total': failed + remaining,
                'total': len(targets),
            },
            'pause': self.state.get('pause') if self.state else None,
            'warnings': [] if self.normalizer is not None else ['NORMALIZER_UNAVAILABLE_RAW_ONLY'],
        }


def capture_movies(targets, *, fetch=None, archive_dir=None, job_id, max_requests, **options):
    """Plan or execute one bounded capture job.

    Parameters
    ----------
    targets:
        Mapping ``{movie_id: tmdb_id}``, a list of
        ``{'movie_id': int, 'tmdb_id': int}`` mappings, or ``(movie_id,
        tmdb_id)`` pairs.  Only positive integers are accepted; title lookup,
        catalogue expansion and duplicate/conflicting identities are rejected.
    fetch:
        Required for ``execute=True``; see the module fetch contract.  Ignored
        by dry-run.
    archive_dir:
        Existing absolute, non-C:, non-reparse directory for execution.  For a
        dry-run it may be ``None`` or a not-yet-created path.
    job_id:
        Stable ``[A-Za-z0-9][A-Za-z0-9._-]{0,63}`` identifier used for the
        archive subdirectory and checkpoint identity.
    max_requests:
        Cumulative job budget, 1..10000.  Every real attempt, including failed
        and retried attempts, is reserved before the request; a resumed job
        keeps the count from its checkpoint.
    options:
        ``execute=False`` (default), ``dry_run`` (inverse alias),
        ``language='zh-CN'``, ``token_provider``,
        ``normalize``, ``clock``, ``sleep``, ``min_interval`` (>=1 second),
        ``max_retries`` (0..2), ``max_wait_seconds``, ``max_total_wait_seconds``,
        ``timeout_seconds``, ``max_body_bytes``, ``backoff_seconds``,
        ``max_backoff_seconds``, ``default_retry_after``.
    """
    _validate_job_id(job_id)
    if type(max_requests) is not int or not 1 <= max_requests <= MAX_REQUESTS:
        raise CaptureInputError('MAX_REQUESTS_INVALID')
    prepared, duplicates = _prepare_targets(targets)
    cfg = _coerce_options(options)
    if cfg['execute'] and fetch is None:
        raise CaptureInputError('FETCH_REQUIRED')
    if cfg['execute'] and archive_dir is None:
        raise CaptureInputError('ARCHIVE_REQUIRED')
    runner = _Capture(prepared, fetch, archive_dir, job_id, max_requests, cfg)
    runner.duplicates = duplicates
    if not cfg['execute']:
        result = runner.plan()
        result['duplicate_targets'] = duplicates
        return result
    runner.token = _read_token(cfg['token_provider'])
    result = runner.run()
    result['duplicate_targets'] = duplicates
    return result