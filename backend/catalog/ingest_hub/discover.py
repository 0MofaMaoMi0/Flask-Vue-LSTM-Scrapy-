"""Bounded TMDB new-movie discovery (popular / top_rated / discover -> job-plan.json)."""
from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from catalog.tmdb_supplement import capture as _capture

from .contracts import (
    HUB_CONTRACT,
    RULE_VERSION,
    HubInputError,
    HubSecurityError,
    atomic_write_json,
    is_positive_int,
    validate_archive_dir,
    validate_env_name,
    validate_job_id,
    validate_safe_file,
)
from .selector import MAX_CATALOG_IDS, MAX_CATALOG_REQUESTS, select_targets

DISCOVER_PRESETS = {
    'popular': '/3/movie/popular',
    'top_rated': '/3/movie/top_rated',
    'discover': '/3/discover/movie',
}
MAX_DISCOVER_PAGES = 500
MAX_DISCOVER_REQUESTS = 500


def _epoch_clock_fn(clock):
    if clock is None:
        return time.monotonic

    def _now():
        val = clock()
        return val.timestamp() if isinstance(val, datetime) else float(val)

    return _now


def _load_existing_ids_from_arg(existing_ids):
    known = set()
    if existing_ids is None:
        return known
    if isinstance(existing_ids, (str, Path)):
        path = validate_safe_file(existing_ids, allow_c_read=True, must_exist=True)
        try:
            loaded = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as exc:
            raise HubInputError('EXISTING_IDS_INVALID', 'cannot parse existing_ids JSON file') from exc
        if isinstance(loaded, dict):
            for key in ('tmdb_ids', 'existing_tmdb_ids', 'ids'):
                if isinstance(loaded.get(key), list):
                    loaded = loaded[key]
                    break
        if not isinstance(loaded, list):
            raise HubInputError('EXISTING_IDS_INVALID', 'existing_ids JSON must be a list of TMDB IDs')
        items = loaded
    elif isinstance(existing_ids, (list, tuple, set, frozenset)):
        items = existing_ids
    else:
        raise HubInputError('EXISTING_IDS_INVALID', 'existing_ids must be a sequence or JSON file path')

    for raw in items:
        if type(raw) is bool:
            raise HubInputError('EXISTING_IDS_INVALID', 'existing_ids entries must be integers')
        if isinstance(raw, int) and raw > 0:
            known.add(raw)
        elif isinstance(raw, str) and raw.strip().isdigit() and int(raw.strip()) > 0:
            known.add(int(raw.strip()))
        else:
            raise HubInputError('EXISTING_IDS_INVALID', 'existing_ids entries must be positive integers')
    return known


def _load_existing_ids_from_connection(connection):
    known = set()
    if connection is None:
        return known
    sql = "SELECT external_id FROM movie_external_ids WHERE source = 'tmdb'"
    if hasattr(connection, 'connect') and not hasattr(connection, 'execute'):
        with connection.connect() as conn:
            res = conn.execute(text(sql))
            rows = res.fetchall()
    elif hasattr(connection, 'execute'):
        try:
            res = connection.execute(text(sql))
            rows = res.fetchall()
        except TypeError:
            res = connection.execute(sql)
            rows = res.fetchall() if hasattr(res, 'fetchall') else list(res)
    else:
        raise HubInputError('CONNECTION_INVALID', 'connection must support execute()')
    for row in rows:
        val = row[0] if isinstance(row, (tuple, list)) else getattr(row, 'external_id', row[0])
        if isinstance(val, int) and val > 0:
            known.add(val)
        elif isinstance(val, str) and val.strip().isdigit() and int(val.strip()) > 0:
            known.add(int(val.strip()))
    return known


def build_discover_request(
    *,
    preset,
    page,
    language,
    token,
    sort_by='popularity.desc',
    min_vote_count=None,
    min_vote_average=None,
):
    if preset not in DISCOVER_PRESETS:
        raise HubInputError('PRESET_INVALID', f'unsupported discover preset: {preset}')
    if type(page) is not int or not 1 <= page <= MAX_DISCOVER_PAGES:
        raise HubInputError('PAGE_INVALID', f'page must be an integer in 1..{MAX_DISCOVER_PAGES}')
    path = DISCOVER_PRESETS[preset]
    params = [('language', language), ('page', str(page))]
    if preset == 'discover':
        params.append(('include_adult', 'false'))
        if sort_by is not None:
            if sort_by not in _capture.DISCOVER_SORT_BY:
                raise HubInputError('SORT_BY_INVALID', f'unsupported sort_by: {sort_by}')
            params.append(('sort_by', sort_by))
        if min_vote_count is not None:
            params.append(('vote_count.gte', str(min_vote_count)))
        if min_vote_average is not None:
            params.append(('vote_average.gte', str(min_vote_average)))
    query = urllib.parse.urlencode(params)
    return {
        'kind': 'discover',
        'method': 'GET',
        'url': f'https://api.themoviedb.org{path}?{query}',
        'headers': {
            'Accept': 'application/json',
            'Authorization': 'Bearer ' + token,
            'User-Agent': 'movieinsight-m6-discover/1.0',
        },
        'timeout': _capture.DEFAULT_TIMEOUT,
        'max_bytes': _capture.MAX_BODY_BYTES,
    }


def discover_catalog_targets(
    *,
    job_id,
    preset='popular',
    limit=20,
    language='zh-CN',
    start_page=1,
    max_pages=20,
    max_requests=None,
    catalog_max_requests=None,
    sort_by='popularity.desc',
    min_vote_count=None,
    min_vote_average=None,
    include_adult=False,
    existing_ids=None,
    connection=None,
    execute=False,
    token_env='TMDB_READ_TOKEN',
    fetch=None,
    proxy=None,
    reuse_connection=False,
    clock=None,
    sleep=None,
    output_plan_path=None,
):
    """Discover deduplicated out-of-catalog TMDB IDs and emit a tmdb_catalog job-plan.json."""
    validate_job_id(job_id)
    if type(execute) is not bool:
        raise HubInputError('EXECUTE_FLAG_INVALID', 'execute must be a boolean')
    if preset not in DISCOVER_PRESETS:
        raise HubInputError('PRESET_INVALID', f'unsupported discover preset: {preset}')
    if include_adult is not False:
        raise HubSecurityError('ADULT_FILTER_REQUIRED', 'include_adult must remain False')
    if type(limit) is not int or not 1 <= limit <= MAX_CATALOG_IDS:
        raise HubInputError('LIMIT_INVALID', f'limit must be an integer in 1..{MAX_CATALOG_IDS}')
    if type(start_page) is not int or not 1 <= start_page <= MAX_DISCOVER_PAGES:
        raise HubInputError('START_PAGE_INVALID', f'start_page must be an integer in 1..{MAX_DISCOVER_PAGES}')
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_DISCOVER_PAGES:
        raise HubInputError('MAX_PAGES_INVALID', f'max_pages must be an integer in 1..{MAX_DISCOVER_PAGES}')
    if sort_by is not None and sort_by not in _capture.DISCOVER_SORT_BY:
        raise HubInputError('SORT_BY_INVALID', f'unsupported sort_by: {sort_by}')
    if min_vote_count is not None:
        if type(min_vote_count) not in (int, float) or isinstance(min_vote_count, bool) or not math.isfinite(float(min_vote_count)) or min_vote_count < 0:
            raise HubInputError('MIN_VOTE_COUNT_INVALID', 'min_vote_count must be >= 0')
    if min_vote_average is not None:
        if type(min_vote_average) not in (int, float) or isinstance(min_vote_average, bool) or not math.isfinite(float(min_vote_average)) or not 0 <= float(min_vote_average) <= 10:
            raise HubInputError('MIN_VOTE_AVERAGE_INVALID', 'min_vote_average must be in 0..10')
    if catalog_max_requests is not None:
        if type(catalog_max_requests) is not int or not 1 <= catalog_max_requests <= MAX_CATALOG_REQUESTS:
            raise HubInputError('MAX_REQUESTS_INVALID', f'catalog_max_requests must be in 1..{MAX_CATALOG_REQUESTS}')
    try:
        _capture._validate_language(language)
    except _capture.CaptureError as exc:
        raise HubInputError(exc.code, str(exc)) from None
    validate_env_name(token_env, 'token_env')

    budget = max_pages if max_requests is None else max_requests
    if type(budget) is not int or not 1 <= budget <= MAX_DISCOVER_REQUESTS:
        raise HubInputError('MAX_REQUESTS_INVALID', f'max_requests must be an integer in 1..{MAX_DISCOVER_REQUESTS}')

    known_ids = _load_existing_ids_from_arg(existing_ids) | _load_existing_ids_from_connection(connection)
    end_page = min(MAX_DISCOVER_PAGES, start_page + max_pages - 1)
    planned_pages = max(0, end_page - start_page + 1)

    if not execute:
        return {
            'contract': HUB_CONTRACT,
            'rule_version': RULE_VERSION,
            'command': 'discover',
            'job_id': job_id,
            'mode': 'dry-run',
            'status': 'DRY_RUN',
            'preset': preset,
            'language': language,
            'limit': limit,
            'start_page': start_page,
            'max_pages': max_pages,
            'requests_planned': min(planned_pages, budget),
            'request_budget': budget,
            'existing_known': len(known_ids),
            'output_plan_path': str(output_plan_path) if output_plan_path else None,
        }

    actual_fetch = fetch or (
        _capture.make_keepalive_fetch(proxy) if reuse_connection else _capture.make_urllib_fetch(proxy)
    )
    try:
        token = _capture._read_token(lambda: os.environ.get(token_env, ''))
    except _capture.CaptureError as exc:
        raise HubInputError(exc.code, str(exc)) from None

    now_fn = _epoch_clock_fn(clock)
    pause_fn = sleep or time.sleep
    min_interval = _capture.DEFAULT_MIN_INTERVAL
    max_attempts = _capture.MAX_RETRIES + 1
    max_retry_after = 60.0

    selected_ids = []
    seen_new = set()
    requests_used = 0
    pages_scanned = 0
    skipped_existing = 0
    skipped_adult = 0
    skipped_duplicate = 0
    skipped_filter = 0
    last_request_at = None
    auth_failed = False
    last_error = None

    for page in range(start_page, end_page + 1):
        if len(selected_ids) >= limit or requests_used >= budget:
            break

        page_payload = None
        for attempt in range(1, max_attempts + 1):
            if requests_used >= budget:
                last_error = 'BUDGET_EXHAUSTED'
                break
            if last_request_at is not None:
                elapsed = now_fn() - last_request_at
                if elapsed < min_interval:
                    pause_fn(min_interval - elapsed)
            last_request_at = now_fn()
            requests_used += 1
            req = build_discover_request(
                preset=preset,
                page=page,
                language=language,
                token=token,
                sort_by=sort_by,
                min_vote_count=min_vote_count,
                min_vote_average=min_vote_average,
            )
            try:
                resp = actual_fetch(req)
            except _capture.CaptureSecurityError as exc:
                raise HubSecurityError(exc.code, str(exc)) from None
            except _capture.CaptureError as exc:
                last_error = exc.code
                if attempt < max_attempts and requests_used < budget:
                    pause_fn(min(_capture.MAX_BACKOFF, _capture.DEFAULT_BACKOFF * attempt))
                    continue
                break
            except Exception:
                last_error = 'TRANSPORT_ERROR'
                if attempt < max_attempts and requests_used < budget:
                    pause_fn(min(_capture.MAX_BACKOFF, _capture.DEFAULT_BACKOFF * attempt))
                    continue
                break

            http_status = resp.get('status')
            if http_status in (401, 403):
                auth_failed = True
                last_error = 'AUTH_FAILED'
                break
            if http_status == 429:
                ra = _capture._parse_retry_after(_capture._header(resp.get('headers'), 'Retry-After'), now_fn())
                wait_sec = min(max_retry_after, max(min_interval, float(ra))) if ra is not None else min(max_retry_after, 2.0 * attempt)
                last_error = 'HTTP_429'
                if attempt < max_attempts and requests_used < budget:
                    pause_fn(wait_sec)
                    continue
                break
            if isinstance(http_status, int) and 500 <= http_status < 600:
                last_error = f'HTTP_{http_status}'
                if attempt < max_attempts and requests_used < budget:
                    pause_fn(min(_capture.MAX_BACKOFF, _capture.DEFAULT_BACKOFF * attempt))
                    continue
                break
            if http_status != 200:
                last_error = f'HTTP_{http_status}'
                break

            body = resp.get('body') or b''
            try:
                parsed = json.loads(body.decode('utf-8'))
            except (UnicodeDecodeError, ValueError):
                last_error = 'INVALID_JSON'
                break
            if not isinstance(parsed, dict) or not isinstance(parsed.get('results'), list):
                last_error = 'RESPONSE_SHAPE_INVALID'
                break
            page_payload = parsed
            last_error = None
            break

        if auth_failed or page_payload is None:
            break

        pages_scanned += 1
        for item in page_payload['results']:
            if not isinstance(item, dict):
                continue
            tid = item.get('id')
            if not is_positive_int(tid) or tid > 9999999999:
                continue
            if item.get('adult') is True:
                skipped_adult += 1
                continue
            if min_vote_count is not None and item.get('vote_count') is not None and float(item['vote_count']) < float(min_vote_count):
                skipped_filter += 1
                continue
            if min_vote_average is not None and item.get('vote_average') is not None and float(item['vote_average']) < float(min_vote_average):
                skipped_filter += 1
                continue
            if tid in known_ids:
                skipped_existing += 1
                continue
            if tid in seen_new:
                skipped_duplicate += 1
                continue
            seen_new.add(tid)
            selected_ids.append(tid)
            if len(selected_ids) >= limit:
                break

        total_pages = page_payload.get('total_pages')
        if is_positive_int(total_pages) and page >= total_pages:
            break

    plan = None
    plan_digest = None
    if selected_ids:
        cat_budget = catalog_max_requests if catalog_max_requests is not None else min(
            MAX_CATALOG_REQUESTS,
            max(len(selected_ids), len(selected_ids) * 2),
        )
        plan = select_targets(
            source='tmdb_catalog',
            scope='catalog',
            job_id=job_id,
            tmdb_ids=selected_ids,
            max_requests=cat_budget,
            language=language,
            output_plan_path=output_plan_path,
        )
        plan_digest = plan['plan_digest']
        status = 'COMPLETED' if len(selected_ids) == limit else 'PARTIAL'
    elif auth_failed:
        status = 'AUTH_FAILED'
    elif last_error and pages_scanned == 0:
        status = 'FAILED'
    else:
        status = 'EMPTY'

    out = {
        'contract': HUB_CONTRACT,
        'rule_version': RULE_VERSION,
        'command': 'discover',
        'job_id': job_id,
        'mode': 'execute',
        'status': status,
        'preset': preset,
        'language': language,
        'requests_used': requests_used,
        'request_budget': budget,
        'pages_scanned': pages_scanned,
        'counts': {
            'limit': limit,
            'selected': len(selected_ids),
            'existing_known': len(known_ids),
            'skipped_existing': skipped_existing,
            'skipped_adult': skipped_adult,
            'skipped_duplicate': skipped_duplicate,
            'skipped_filter': skipped_filter,
        },
        'output_plan_path': str(output_plan_path) if (output_plan_path and plan is not None) else None,
        'plan_digest': plan_digest,
        'plan': plan,
    }
    if last_error:
        out['error_code'] = last_error
    return out


def export_existing_tmdb_ids(connection, output_path=None):
    """Export sorted, deduplicated integer TMDB IDs from movie_external_ids."""
    if connection is None:
        raise HubInputError('CONNECTION_REQUIRED', 'database connection is required')
    dest = None
    if output_path is not None:
        dest = validate_safe_file(output_path, allow_c_read=False, must_exist=False)
        validate_archive_dir(dest.parent, must_exist=False, create=False)

    ids = sorted(_load_existing_ids_from_connection(connection))
    if dest is not None:
        atomic_write_json(dest, ids)

    result = {
        'contract': HUB_CONTRACT,
        'rule_version': RULE_VERSION,
        'command': 'export-existing-ids',
        'status': 'EXPORTED' if dest is not None else 'OK',
        'count': len(ids),
        'min_id': ids[0] if ids else None,
        'max_id': ids[-1] if ids else None,
        'output_path': str(dest) if dest is not None else None,
    }
    if dest is None:
        result['tmdb_ids'] = ids
    return result


__all__ = [
    'DISCOVER_PRESETS',
    'build_discover_request',
    'discover_catalog_targets',
    'export_existing_tmdb_ids',
]
