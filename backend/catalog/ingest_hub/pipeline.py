"""Unified capture -> verify -> commit pipeline and token-saving summarizer."""
from __future__ import annotations

import json
import os
import secrets
import time
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from catalog import admin_importer as _admin_importer
from catalog import data_pipeline as _data_pipeline
from catalog.tmdb_supplement import board_ingest as _board_ingest
from catalog.tmdb_supplement import capture as _capture
from catalog.tmdb_supplement import ingest as _ingest
from catalog.tmdb_supplement import trending as _trending
from catalog.tmdb_supplement import writer as _writer

from .contracts import (
    HUB_CONTRACT,
    RULE_VERSION,
    SUPPORTED_SOURCES,
    HubError,
    HubInputError,
    HubRejected,
    HubSecurityError,
    atomic_write_bytes,
    atomic_write_json,
    canonical_json,
    is_positive_int,
    is_reparse,
    sha256_bytes,
    sha256_json,
    validate_archive_dir,
    validate_env_name,
    validate_job_id,
)
from .selector import load_plan, select_targets

MAX_ANOMALY_SAMPLES = 5
CATALOG_MANIFEST_SCHEMA = 'tmdb-catalog-capture-v1'
LOCAL_ENVELOPE_MANIFEST_SCHEMA = 'local-envelope-archive-v1'
IGNORED_SEAL_FILES = frozenset((
    'verification-receipt.json',
    'commit-receipt.json',
    'before-image.json',
    'dataset.json',
))


def _utc_now_iso(now=None):
    if isinstance(now, datetime):
        dt = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _resolve_job_dir(archive_dir, job_id=None, *, must_exist=True):
    root = validate_archive_dir(archive_dir, must_exist=must_exist)
    if job_id is not None:
        validate_job_id(job_id)
        candidate = root / job_id
        if must_exist:
            return validate_archive_dir(candidate, must_exist=True)
        return candidate
    return root


def _read_json_safe(path, code='ARCHIVE_JSON_INVALID'):
    candidate = Path(path)
    if is_reparse(candidate):
        raise HubSecurityError('ARCHIVE_REPARSE_POINT', 'symlink/reparse file rejected')
    if not candidate.is_file():
        raise HubInputError('ARCHIVE_FILE_MISSING', f'missing file: {candidate.name}')
    try:
        return json.loads(candidate.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise HubInputError(code, f'invalid JSON in {candidate.name}') from exc


def _detect_source(job_dir, explicit_source=None, plan=None):
    if explicit_source is not None:
        if explicit_source not in SUPPORTED_SOURCES:
            raise HubInputError('UNSUPPORTED_SOURCE', f'unsupported source: {explicit_source}')
        return explicit_source
    if isinstance(plan, dict) and plan.get('source') in SUPPORTED_SOURCES:
        return plan['source']
    hub_plan_path = job_dir / 'hub-plan.json'
    if hub_plan_path.is_file():
        hub_plan = _read_json_safe(hub_plan_path, 'PLAN_INVALID')
        src = hub_plan.get('source')
        if src in SUPPORTED_SOURCES:
            return src
    manifest_path = job_dir / 'manifest.json'
    if manifest_path.is_file():
        manifest = _read_json_safe(manifest_path, 'INVALID_MANIFEST')
        schema = manifest.get('schema')
        if schema == _ingest.MANIFEST_SCHEMA:
            return 'tmdb_supplement'
        if schema == _trending.TRENDING_SCHEMA:
            return 'tmdb_trending'
        if schema == CATALOG_MANIFEST_SCHEMA:
            return 'tmdb_catalog'
        if schema == LOCAL_ENVELOPE_MANIFEST_SCHEMA:
            return 'local_envelope'
    if (job_dir / 'envelope.json').is_file():
        return 'local_envelope'
    raise HubInputError('SOURCE_UNDETECTABLE', 'cannot determine archive source; pass source explicitly')


def _archive_file_hashes(job_dir):
    files = []
    for path in sorted(job_dir.rglob('*')):
        if is_reparse(path):
            raise HubSecurityError('ARCHIVE_REPARSE_POINT', 'symlink/reparse item inside archive')
        if not path.is_file():
            continue
        rel = path.relative_to(job_dir).as_posix()
        name = path.name
        if name in IGNORED_SEAL_FILES or name.startswith('.') or name.endswith('.tmp'):
            continue
        files.append([rel, sha256_bytes(path.read_bytes())])
    files.sort(key=lambda item: item[0])
    return files


def _compute_commit_seal(job_dir, *, source, job_id, status, counts):
    material = {
        'contract': HUB_CONTRACT,
        'rule_version': RULE_VERSION,
        'source': source,
        'job_id': job_id,
        'status': status,
        'counts': counts,
        'files': _archive_file_hashes(job_dir),
    }
    return sha256_json(material)


def summary_envelope(receipt):
    """Project a verification receipt or commit output into a <30-line compact summary."""
    keys = (
        'contract',
        'rule_version',
        'source',
        'job_id',
        'status',
        'counts',
        'error_breakdown',
        'anomaly_samples',
        'commit_seal',
    )
    compact = {key: receipt.get(key) for key in keys if key in receipt}
    if 'anomaly_samples' in compact and isinstance(compact['anomaly_samples'], list):
        compact['anomaly_samples'] = compact['anomaly_samples'][:MAX_ANOMALY_SAMPLES]
    return compact


# ------------------------------------------------------------------ capture


def _capture_catalog_movie_request(tmdb_id, language, token):
    query = urllib.parse.urlencode({
        'language': language,
        'append_to_response': ','.join(_capture.APPEND_FIELDS),
    })
    return {
        'kind': 'catalog',
        'method': 'GET',
        'url': f'{_capture.API_ROOT}/movie/{tmdb_id}?{query}',
        'headers': {
            'Accept': 'application/json',
            'Authorization': 'Bearer ' + token,
            'User-Agent': 'movieinsight-m6-catalog/1.0',
        },
        'timeout': _capture.DEFAULT_TIMEOUT,
        'max_bytes': _capture.MAX_BODY_BYTES,
    }


def _epoch_clock_fn(clock):
    if clock is None:
        return time.monotonic
    def _now():
        val = clock()
        return val.timestamp() if isinstance(val, datetime) else float(val)
    return _now


def _load_resumable_catalog_items(job_dir, *, job_id, language):
    manifest_path = job_dir / 'manifest.json'
    if not manifest_path.is_file():
        return {}
    manifest = _read_json_safe(manifest_path, 'INVALID_MANIFEST')
    if manifest.get('schema') != CATALOG_MANIFEST_SCHEMA or manifest.get('job_id') != job_id:
        raise HubInputError('INVALID_MANIFEST', 'existing tmdb_catalog manifest mismatch')
    if manifest.get('language') != language:
        raise HubInputError('INPUT_MISMATCH', 'existing tmdb_catalog manifest has different language')
    resumable = {}
    for entry in manifest.get('items') or []:
        if not isinstance(entry, dict) or entry.get('status') != 'HTTP_200':
            continue
        tid = entry.get('tmdb_id')
        if not is_positive_int(tid):
            continue
        raw_path = job_dir / 'raw' / f'{tid}.json'
        norm_path = job_dir / 'normalized' / f'{tid}.json'
        if is_reparse(raw_path) or is_reparse(norm_path):
            raise HubSecurityError('ARCHIVE_REPARSE_POINT', 'symlink/reparse file rejected')
        if not raw_path.is_file() or not norm_path.is_file():
            raise HubInputError('ARCHIVE_CORRUPT', f'missing resumed artifact for tmdb_id={tid}')
        raw_bytes = raw_path.read_bytes()
        norm_bytes = norm_path.read_bytes()
        if sha256_bytes(raw_bytes) != entry.get('raw_sha256') or sha256_bytes(norm_bytes) != entry.get('normalized_sha256'):
            raise HubInputError('ARCHIVE_CORRUPT', f'resumed artifact hash mismatch for tmdb_id={tid}')
        try:
            payload = json.loads(norm_bytes.decode('utf-8'))
            _admin_importer.validate(payload)
        except Exception:
            raise HubInputError('ARCHIVE_CORRUPT', f'invalid resumed payload for tmdb_id={tid}') from None
        resumable[tid] = dict(entry, resumed=True)
    return resumable


def _capture_tmdb_catalog(
    plan,
    archive_path,
    *,
    execute,
    token_env,
    fetch,
    proxy,
    reuse_connection,
    clock=None,
    sleep=None,
):
    job_id = plan['job_id']
    tmdb_ids = [int(item['tmdb_id']) for item in plan['targets']]
    language = plan.get('language', 'zh-CN')
    max_requests = int(plan.get('request_budget') or len(tmdb_ids))
    job_dir = archive_path / job_id

    if not execute:
        return {
            'contract': HUB_CONTRACT,
            'rule_version': RULE_VERSION,
            'source': 'tmdb_catalog',
            'job_id': job_id,
            'mode': 'dry-run',
            'status': 'DRY_RUN',
            'archive_dir': str(job_dir),
            'requests_planned': len(tmdb_ids),
            'request_budget': max_requests,
            'plan_digest': plan.get('plan_digest'),
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

    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / 'raw').mkdir(parents=True, exist_ok=True)
    (job_dir / 'normalized').mkdir(parents=True, exist_ok=True)
    resumable = _load_resumable_catalog_items(job_dir, job_id=job_id, language=language)

    items = []
    requests_used = 0
    succeeded = 0
    resumed = 0
    failed = 0
    last_request_at = None

    def _save_progress():
        atomic_write_json(job_dir / 'manifest.json', {
            'schema': CATALOG_MANIFEST_SCHEMA,
            'job_id': job_id,
            'language': language,
            'requests_used': requests_used,
            'max_requests': max_requests,
            'items': items,
        })

    atomic_write_json(job_dir / 'hub-plan.json', plan)

    auth_failed = False
    for tid in tmdb_ids:
        if tid in resumable:
            items.append(dict(resumable[tid]))
            succeeded += 1
            resumed += 1
            continue

        if auth_failed:
            items.append({'tmdb_id': tid, 'status': 'FAILED', 'error_code': 'AUTH_FAILED'})
            failed += 1
            continue

        final_item = None
        for attempt in range(1, max_attempts + 1):
            if requests_used >= max_requests:
                final_item = {'tmdb_id': tid, 'status': 'FAILED', 'error_code': 'BUDGET_EXHAUSTED'}
                break
            if last_request_at is not None:
                elapsed = now_fn() - last_request_at
                if elapsed < min_interval:
                    pause_fn(min_interval - elapsed)
            last_request_at = now_fn()
            requests_used += 1
            req = _capture_catalog_movie_request(tid, language, token)

            try:
                resp = actual_fetch(req)
            except _capture.CaptureSecurityError as exc:
                raise HubSecurityError(exc.code, str(exc)) from None
            except _capture.CaptureError as exc:
                err_code = exc.code
                if attempt < max_attempts and requests_used < max_requests:
                    pause_fn(min(_capture.MAX_BACKOFF, _capture.DEFAULT_BACKOFF * attempt))
                    continue
                final_item = {'tmdb_id': tid, 'status': 'FAILED', 'error_code': err_code}
                break
            except Exception:
                if attempt < max_attempts and requests_used < max_requests:
                    pause_fn(min(_capture.MAX_BACKOFF, _capture.DEFAULT_BACKOFF * attempt))
                    continue
                final_item = {'tmdb_id': tid, 'status': 'FAILED', 'error_code': 'TRANSPORT_ERROR'}
                break

            http_status = resp.get('status')
            if http_status in (401, 403):
                final_item = {'tmdb_id': tid, 'status': 'FAILED', 'http_status': http_status, 'error_code': 'AUTH_FAILED'}
                auth_failed = True
                break
            if http_status == 429:
                ra = _capture._parse_retry_after(_capture._header(resp.get('headers'), 'Retry-After'), now_fn())
                wait_sec = min(max_retry_after, max(min_interval, float(ra))) if ra is not None else min(max_retry_after, 2.0 * attempt)
                if attempt < max_attempts and requests_used < max_requests:
                    pause_fn(wait_sec)
                    continue
                final_item = {'tmdb_id': tid, 'status': 'FAILED', 'http_status': 429, 'error_code': 'HTTP_429'}
                break
            if isinstance(http_status, int) and 500 <= http_status < 600:
                if attempt < max_attempts and requests_used < max_requests:
                    pause_fn(min(_capture.MAX_BACKOFF, _capture.DEFAULT_BACKOFF * attempt))
                    continue
                final_item = {'tmdb_id': tid, 'status': 'FAILED', 'http_status': http_status, 'error_code': f'HTTP_{http_status}'}
                break
            if http_status != 200:
                final_item = {'tmdb_id': tid, 'status': 'FAILED', 'http_status': http_status, 'error_code': f'HTTP_{http_status}'}
                break

            body = resp.get('body') or b''
            try:
                raw = json.loads(body.decode('utf-8'))
            except (UnicodeDecodeError, ValueError):
                final_item = {'tmdb_id': tid, 'status': 'FAILED', 'http_status': 200, 'error_code': 'INVALID_JSON'}
                break
            if not isinstance(raw, dict) or raw.get('id') != tid:
                final_item = {'tmdb_id': tid, 'status': 'FAILED', 'http_status': 200, 'error_code': 'UPSTREAM_IDENTITY_MISMATCH'}
                break
            try:
                payload = _admin_importer.capture(raw)
            except _admin_importer.ImportRejected as exc:
                final_item = {'tmdb_id': tid, 'status': 'FAILED', 'http_status': 200, 'error_code': str(exc)}
                break

            raw_bytes = (json.dumps(raw, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
            norm_bytes = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
            atomic_write_bytes(job_dir / 'raw' / f'{tid}.json', raw_bytes)
            atomic_write_bytes(job_dir / 'normalized' / f'{tid}.json', norm_bytes)
            final_item = {
                'tmdb_id': tid,
                'status': 'HTTP_200',
                'http_status': 200,
                'raw_sha256': sha256_bytes(raw_bytes),
                'normalized_sha256': sha256_bytes(norm_bytes),
            }
            break

        if final_item is None:
            final_item = {'tmdb_id': tid, 'status': 'FAILED', 'error_code': 'BUDGET_EXHAUSTED'}
        items.append(final_item)
        if final_item.get('status') == 'HTTP_200':
            succeeded += 1
        else:
            failed += 1
        _save_progress()
        if auth_failed:
            break

    _save_progress()
    overall = 'COMPLETED' if failed == 0 else ('AUTH_FAILED' if auth_failed else ('PARTIAL' if succeeded > 0 else 'FAILED'))
    return {
        'contract': HUB_CONTRACT,
        'rule_version': RULE_VERSION,
        'source': 'tmdb_catalog',
        'job_id': job_id,
        'mode': 'execute',
        'status': overall,
        'archive_dir': str(job_dir),
        'requests_used': requests_used,
        'request_budget': max_requests,
        'succeeded': succeeded,
        'resumed': resumed,
        'failed': failed,
        'plan_digest': plan.get('plan_digest'),
    }


def _capture_local_envelope(plan, archive_path, *, execute):
    job_id = plan['job_id']
    target = plan['targets'][0]
    input_path = target['input_path']
    metadata = target.get('metadata')
    job_dir = archive_path / job_id

    if not execute:
        return {
            'contract': HUB_CONTRACT,
            'rule_version': RULE_VERSION,
            'source': 'local_envelope',
            'job_id': job_id,
            'mode': 'dry-run',
            'status': 'DRY_RUN',
            'archive_dir': str(job_dir),
            'input_path': input_path,
            'requests_planned': 0,
            'request_budget': 0,
            'plan_digest': plan.get('plan_digest'),
        }

    envelope = _data_pipeline.read_input(input_path, metadata)
    if not isinstance(envelope, dict) or not isinstance(envelope.get('records'), list):
        raise HubInputError('INVALID_ENVELOPE', 'local_envelope input must contain metadata and records')
    job_dir.mkdir(parents=True, exist_ok=True)
    env_bytes = (json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    (job_dir / 'envelope.json').write_bytes(env_bytes)
    manifest = {
        'schema': LOCAL_ENVELOPE_MANIFEST_SCHEMA,
        'job_id': job_id,
        'input_path': str(input_path),
        'record_count': len(envelope['records']),
        'envelope_sha256': sha256_bytes(env_bytes),
    }
    atomic_write_json(job_dir / 'manifest.json', manifest)
    atomic_write_json(job_dir / 'hub-plan.json', plan)
    return {
        'contract': HUB_CONTRACT,
        'rule_version': RULE_VERSION,
        'source': 'local_envelope',
        'job_id': job_id,
        'mode': 'execute',
        'status': 'COMPLETED',
        'archive_dir': str(job_dir),
        'record_count': len(envelope['records']),
        'requests_used': 0,
        'request_budget': 0,
        'plan_digest': plan.get('plan_digest'),
    }


def _capture_douban_archive(plan, archive_path, *, execute):
    job_id = plan['job_id']
    target = plan['targets'][0]
    input_path = target['input_path']
    job_dir = archive_path / job_id

    if not execute:
        return {
            'contract': HUB_CONTRACT,
            'rule_version': RULE_VERSION,
            'source': 'douban_archive',
            'job_id': job_id,
            'mode': 'dry-run',
            'status': 'DRY_RUN',
            'archive_dir': str(job_dir),
            'input_path': input_path,
            'requests_planned': 0,
            'request_budget': 0,
            'plan_digest': plan.get('plan_digest'),
        }

    from .douban_adapter import build_douban_archive, load_douban_raw

    raw_items = load_douban_raw(input_path)
    built = build_douban_archive(
        raw_items,
        archive_path,
        job_id=job_id,
        source_input_path=input_path,
    )
    atomic_write_json(job_dir / 'hub-plan.json', plan)
    return {
        'contract': HUB_CONTRACT,
        'rule_version': RULE_VERSION,
        'source': 'douban_archive',
        'job_id': job_id,
        'mode': 'execute',
        'status': 'COMPLETED',
        'archive_dir': built['archive_dir'],
        'counts': built['counts'],
        'supplementary': built['supplementary'],
        'requests_used': 0,
        'request_budget': 0,
        'plan_digest': plan.get('plan_digest'),
    }


def run_capture(
    plan=None,
    *,
    archive_dir,
    execute=False,
    token_env='TMDB_READ_TOKEN',
    fetch=None,
    proxy=None,
    reuse_connection=False,
    clock=None,
    sleep=None,
    **select_kwargs,
):
    """Execute or dry-run bounded capture according to a validated job-plan."""
    if type(execute) is not bool:
        raise HubInputError('EXECUTE_FLAG_INVALID', 'execute must be a boolean')
    validate_env_name(token_env, 'token_env')
    archive_path = validate_archive_dir(archive_dir, must_exist=False, create=execute)

    if plan is None:
        plan_obj = select_targets(**select_kwargs)
    else:
        plan_obj = load_plan(plan)

    source = plan_obj['source']
    job_id = plan_obj['job_id']
    language = plan_obj.get('language', 'zh-CN')
    budget = int(plan_obj.get('request_budget') or 0)

    if source == 'tmdb_supplement':
        pairs = [{'movie_id': item['movie_id'], 'tmdb_id': item['tmdb_id']} for item in plan_obj['targets']]
        if not pairs:
            return {
                'contract': HUB_CONTRACT,
                'rule_version': RULE_VERSION,
                'source': source,
                'job_id': job_id,
                'mode': 'execute' if execute else 'dry-run',
                'status': 'EMPTY_PLAN' if execute else 'DRY_RUN',
                'archive_dir': str(archive_path / job_id),
                'requests_used': 0,
                'request_budget': budget,
                'plan_digest': plan_obj.get('plan_digest'),
            }
        actual_fetch = None
        provider = None
        extra_opts = {}
        if clock is not None:
            def _epoch_clock():
                val = clock()
                return val.timestamp() if isinstance(val, datetime) else float(val)
            extra_opts['clock'] = _epoch_clock
        if sleep is not None:
            extra_opts['sleep'] = sleep
        if execute:
            actual_fetch = fetch or (
                _capture.make_keepalive_fetch(proxy) if reuse_connection else _capture.make_urllib_fetch(proxy)
            )
            provider = lambda: os.environ.get(token_env, '')
        try:
            raw_res = _capture.capture_movies(
                pairs,
                fetch=actual_fetch,
                archive_dir=str(archive_path),
                job_id=job_id,
                max_requests=budget,
                execute=execute,
                language=language,
                token_provider=provider,
                **extra_opts,
            )
        except _capture.CaptureSecurityError as exc:
            raise HubSecurityError(exc.code, str(exc)) from None
        except _capture.CaptureError as exc:
            raise HubInputError(exc.code, str(exc)) from None
        if execute and (archive_path / job_id).is_dir():
            atomic_write_json(archive_path / job_id / 'hub-plan.json', plan_obj)
        return {
            'contract': HUB_CONTRACT,
            'rule_version': RULE_VERSION,
            'source': source,
            'job_id': job_id,
            'mode': raw_res.get('mode'),
            'status': 'DRY_RUN' if not execute else raw_res.get('status'),
            'archive_dir': raw_res.get('archive_dir') or str(archive_path / job_id),
            'requests_used': raw_res.get('requests_used', 0),
            'request_budget': budget,
            'coverage': raw_res.get('coverage'),
            'plan_digest': plan_obj.get('plan_digest'),
        }

    if source == 'tmdb_trending':
        actual_fetch = None
        token_val = None
        now_fn = None
        if clock is not None:
            def now_fn():
                val = clock()
                return datetime.fromtimestamp(float(val), tz=timezone.utc) if isinstance(val, (int, float)) else val
        if execute:
            actual_fetch = fetch or _capture.make_urllib_fetch(proxy)
            try:
                token_val = _capture._read_token(lambda: os.environ.get(token_env, ''))
            except _capture.CaptureError as exc:
                raise HubInputError(exc.code, str(exc)) from None
        try:
            raw_res = _trending.capture_trending(
                str(archive_path),
                job_id=job_id,
                fetch=actual_fetch,
                execute=execute,
                pages=plan_obj.get('pages', 2),
                max_requests=budget,
                language=language,
                token=token_val,
                now=now_fn,
                sleep=sleep,
            )
        except _capture.CaptureSecurityError as exc:
            raise HubSecurityError(exc.code, str(exc)) from None
        except _capture.CaptureError as exc:
            raise HubInputError(exc.code, str(exc)) from None
        if execute and (archive_path / job_id).is_dir():
            atomic_write_json(archive_path / job_id / 'hub-plan.json', plan_obj)
        return {
            'contract': HUB_CONTRACT,
            'rule_version': RULE_VERSION,
            'source': source,
            'job_id': job_id,
            'mode': 'execute' if execute else 'dry-run',
            'status': raw_res.get('status'),
            'archive_dir': raw_res.get('archive_dir') or str(archive_path / job_id),
            'requests_used': raw_res.get('requests_used', 0),
            'request_budget': budget,
            'channels': raw_res.get('channels'),
            'plan_digest': plan_obj.get('plan_digest'),
        }

    if source == 'tmdb_catalog':
        return _capture_tmdb_catalog(
            plan_obj,
            archive_path,
            execute=execute,
            token_env=token_env,
            fetch=fetch,
            proxy=proxy,
            reuse_connection=reuse_connection,
            clock=clock,
            sleep=sleep,
        )

    if source == 'douban_archive':
        return _capture_douban_archive(plan_obj, archive_path, execute=execute)

    return _capture_local_envelope(plan_obj, archive_path, execute=execute)


# ------------------------------------------------------------------ verify


def _verify_tmdb_supplement(job_dir, *, now=None):
    manifest = _read_json_safe(job_dir / 'manifest.json', 'INVALID_MANIFEST')
    errors = Counter()
    anomalies = []
    seen_tids = set()
    succeeded_tids = set()
    failed_attempts = {}
    for attempt in manifest.get('attempts') or []:
        tid = attempt.get('tmdb_id')
        if is_positive_int(tid):
            seen_tids.add(tid)
        if _ingest._attempt_succeeded(attempt):
            succeeded_tids.add(tid)
        elif is_positive_int(tid):
            code = attempt.get('error_code') or attempt.get('status') or 'ATTEMPT_FAILED'
            failed_attempts[tid] = {'movie_id': attempt.get('movie_id'), 'tmdb_id': tid, 'reason': str(code)}

    job = _ingest.load_job(str(job_dir))
    dry = _ingest.dry_run(str(job_dir), now=now)

    for tid in sorted(failed_attempts):
        if tid in succeeded_tids:
            continue
        info = failed_attempts[tid]
        errors[info['reason']] += 1
        if len(anomalies) < MAX_ANOMALY_SAMPLES:
            anomalies.append({
                'kind': 'attempt_failed',
                'movie_id': info['movie_id'],
                'tmdb_id': tid,
                'reason': info['reason'],
            })

    for doc in job['documents']:
        for topic, st in doc['field_status'].items():
            if st == 'FAILED':
                code = f'FIELD_FAILED:{topic}'
                errors[code] += 1
                if len(anomalies) < MAX_ANOMALY_SAMPLES:
                    anomalies.append({
                        'kind': 'field_failed',
                        'movie_id': doc['movie_id'],
                        'tmdb_id': doc['tmdb_id'],
                        'topic': topic,
                        'reason': code,
                    })

    valid_count = len(job['documents'])
    rejected_count = len([tid for tid in failed_attempts if tid not in succeeded_tids])
    total_input = len(seen_tids) if seen_tids else valid_count
    status = 'READY_TO_COMMIT' if valid_count > 0 else 'REJECTED'
    return {
        'job_id': job['job_id'],
        'status': status,
        'counts': {
            'total_input': total_input,
            'valid': valid_count,
            'rejected': rejected_count,
            'quarantined': 0,
            'needs_review': 0,
            'duplicates': max(0, (manifest.get('attempts') and len(manifest['attempts']) or valid_count) - total_input),
        },
        'error_breakdown': dict(sorted(errors.items())),
        'anomaly_samples': anomalies[:MAX_ANOMALY_SAMPLES],
        'preview': {
            'operations': dry.get('operations', 0),
            'per_topic': dry.get('per_topic', {}),
        },
    }


def _verify_tmdb_trending(job_dir, *, now=None):
    manifest, entries = _trending.load_archive(str(job_dir))
    dry = _board_ingest.dry_run(str(job_dir), now=now)
    errors = Counter()
    anomalies = []
    total_input = 0
    unique_tids = set()
    duplicates = 0
    for channel in _trending.CHANNELS:
        ch_info = manifest['channels'][channel]
        if ch_info.get('status') != 'succeeded':
            for page_info in ch_info.get('pages') or []:
                if page_info.get('status') != 'HTTP_200':
                    code = page_info.get('error_code') or 'PAGE_FAILED'
                    errors[code] += 1
                    if len(anomalies) < MAX_ANOMALY_SAMPLES:
                        anomalies.append({
                            'kind': 'channel_issue',
                            'channel': channel,
                            'page': page_info.get('page'),
                            'reason': code,
                        })
        for entry in entries.get(channel) or []:
            total_input += 1
            tid = entry.get('tmdb_id')
            if tid in unique_tids:
                duplicates += 1
            elif tid is not None:
                unique_tids.add(tid)

    valid_count = sum(info['entries'] for info in dry['channels'].values())
    status = 'READY_TO_COMMIT' if valid_count > 0 else 'REJECTED'
    return {
        'job_id': dry['job_id'],
        'status': status,
        'counts': {
            'total_input': total_input,
            'valid': valid_count,
            'rejected': 0,
            'quarantined': 0,
            'needs_review': 0,
            'duplicates': duplicates,
        },
        'error_breakdown': dict(sorted(errors.items())),
        'anomaly_samples': anomalies[:MAX_ANOMALY_SAMPLES],
        'preview': {
            'operations': dry.get('operations', 0),
            'channels': dry.get('channels', {}),
        },
    }


def _verify_local_envelope(job_dir, *, plan=None):
    manifest_path = job_dir / 'manifest.json'
    envelope_path = job_dir / 'envelope.json'
    manifest = None
    if manifest_path.is_file():
        manifest = _read_json_safe(manifest_path, 'INVALID_MANIFEST')
        if manifest.get('schema') != LOCAL_ENVELOPE_MANIFEST_SCHEMA:
            raise HubInputError('INVALID_MANIFEST_SCHEMA', 'unexpected local_envelope manifest schema')
        job_id = manifest.get('job_id') or job_dir.name
    else:
        job_id = (plan or {}).get('job_id') or job_dir.name
    envelope = _read_json_safe(envelope_path, 'INVALID_ENVELOPE')
    try:
        built = _data_pipeline.build(envelope)
    except ValueError as exc:
        raise HubInputError('INVALID_ENVELOPE', str(exc)) from None

    quality = built['quality']
    errors = Counter()
    anomalies = []
    for issue in built['issues']:
        reason = issue.get('reason') or issue.get('status') or 'issue'
        errors[reason] += 1
        if len(anomalies) < MAX_ANOMALY_SAMPLES:
            anomalies.append({
                'kind': issue.get('status', 'issue'),
                'id_ref': str(issue.get('payload_hash', ''))[:12],
                'reason': reason,
            })
    for rev in built['reviews']:
        reason = rev.get('reason') or 'review'
        errors[reason] += 1
        if len(anomalies) < MAX_ANOMALY_SAMPLES:
            sample = {
                'kind': 'review',
                'id_ref': str(rev.get('payload_hash', ''))[:12],
                'reason': reason,
            }
            if rev.get('candidate_movie_id') is not None:
                sample['candidate_movie_id'] = rev['candidate_movie_id']
            anomalies.append(sample)

    valid_count = int(quality['accepted_movies'])
    rejected_count = int(quality['rejected'])
    quarantined_count = int(quality['quarantined'])
    needs_review_count = int(quality['review'])
    duplicates_count = int(quality['duplicates'])

    if valid_count == 0 and (rejected_count > 0 or quarantined_count > 0 or needs_review_count > 0):
        status = 'REJECTED'
    elif valid_count == 0:
        status = 'REJECTED'
    elif quarantined_count > 0 or needs_review_count > 0:
        status = 'NEEDS_REVIEW'
    else:
        status = 'READY_TO_COMMIT'

    preview = {
        'batch_id': built['batch_id'],
        'content_hash': built['content_hash'],
        'missing_year': int(quality['missing_year']),
    }
    if isinstance(manifest, dict):
        if manifest.get('adapter'):
            preview['adapter'] = manifest['adapter']
        if isinstance(manifest.get('supplementary'), dict):
            preview['supplementary'] = manifest['supplementary']
        if isinstance(manifest.get('counts'), dict):
            preview['archive_counts'] = manifest['counts']

    return {
        'job_id': str(job_id),
        'status': status,
        'counts': {
            'total_input': int(quality['input']),
            'valid': valid_count,
            'rejected': rejected_count,
            'quarantined': quarantined_count,
            'needs_review': needs_review_count,
            'duplicates': duplicates_count,
        },
        'error_breakdown': dict(sorted(errors.items())),
        'anomaly_samples': anomalies[:MAX_ANOMALY_SAMPLES],
        'preview': preview,
    }


def _verify_tmdb_catalog(job_dir, *, connection=None, importer=None, catalog_config=None):
    manifest = _read_json_safe(job_dir / 'manifest.json', 'INVALID_MANIFEST')
    if manifest.get('schema') != CATALOG_MANIFEST_SCHEMA:
        raise HubInputError('INVALID_MANIFEST_SCHEMA', 'unexpected tmdb_catalog manifest schema')
    job_id = manifest.get('job_id') or job_dir.name
    items = manifest.get('items') or []
    errors = Counter()
    anomalies = []
    valid_payloads = []
    rejected = 0
    quarantined = 0
    needs_review = 0
    plans = []
    seen_tids = set()
    duplicates = 0

    active_importer = importer
    if active_importer is None and connection is not None and catalog_config is not None:
        # Build CatalogImporter if an Engine was provided via connection or attribute.
        engine = getattr(connection, 'engine', connection)
        active_importer = _admin_importer.CatalogImporter(engine, catalog_config)

    for entry in items:
        tid = entry.get('tmdb_id')
        if tid in seen_tids:
            duplicates += 1
            continue
        if tid is not None:
            seen_tids.add(tid)
        if entry.get('status') != 'HTTP_200':
            code = entry.get('error_code') or 'CAPTURE_FAILED'
            errors[code] += 1
            rejected += 1
            if len(anomalies) < MAX_ANOMALY_SAMPLES:
                anomalies.append({'kind': 'rejected', 'tmdb_id': tid, 'reason': code})
            continue
        norm_path = job_dir / 'normalized' / f'{tid}.json'
        norm_bytes = norm_path.read_bytes() if norm_path.is_file() else b''
        if not norm_path.is_file() or sha256_bytes(norm_bytes) != entry.get('normalized_sha256'):
            raise HubInputError('ARCHIVE_CORRUPT', f'normalized artifact hash mismatch for tmdb_id={tid}')
        payload = json.loads(norm_bytes.decode('utf-8'))
        try:
            _admin_importer.validate(payload)
        except (_admin_importer.ImportRejected, ValueError, TypeError) as exc:
            code = str(exc) or 'INVALID_PAYLOAD'
            errors[code] += 1
            rejected += 1
            if len(anomalies) < MAX_ANOMALY_SAMPLES:
                anomalies.append({'kind': 'rejected', 'tmdb_id': tid, 'reason': code})
            continue

        if active_importer is not None:
            try:
                if hasattr(active_importer, 'plan') and connection is not None and not hasattr(connection, 'connect'):
                    plan_item = active_importer.plan(connection, payload)
                else:
                    plan_item = active_importer.preview([payload])[0]
                plans.append(plan_item)
            except _admin_importer.ImportRejected as exc:
                code = str(exc)
                errors[code] += 1
                if code == 'IDENTITY_CONFLICT':
                    quarantined += 1
                    kind = 'quarantined'
                else:
                    needs_review += 1
                    kind = 'review'
                if len(anomalies) < MAX_ANOMALY_SAMPLES:
                    anomalies.append({'kind': kind, 'tmdb_id': tid, 'reason': code})
                continue

        valid_payloads.append(payload)

    valid_count = len(valid_payloads)
    if quarantined > 0 or needs_review > 0:
        status = 'NEEDS_REVIEW'
    elif valid_count == 0:
        status = 'REJECTED'
    elif rejected > 0:
        status = 'NEEDS_REVIEW'
    else:
        status = 'READY_TO_COMMIT'

    return {
        'job_id': str(job_id),
        'status': status,
        'counts': {
            'total_input': len(items),
            'valid': valid_count,
            'rejected': rejected,
            'quarantined': quarantined,
            'needs_review': needs_review,
            'duplicates': duplicates,
        },
        'error_breakdown': dict(sorted(errors.items())),
        'anomaly_samples': anomalies[:MAX_ANOMALY_SAMPLES],
        'preview': {
            'actions': plans,
            'payload_count': valid_count,
        },
        '_payloads': valid_payloads,
    }


def verify_archive(
    archive_dir,
    *,
    job_id=None,
    source=None,
    plan=None,
    connection=None,
    importer=None,
    catalog_config=None,
    now=None,
    write_receipt=True,
):
    """Pure offline/read-only verification of an archive -> Summary Envelope + commit_seal."""
    job_dir = _resolve_job_dir(archive_dir, job_id=job_id, must_exist=True)
    loaded_plan = load_plan(plan) if plan is not None else None
    if loaded_plan is None and (job_dir / 'hub-plan.json').is_file():
        loaded_plan = load_plan(job_dir / 'hub-plan.json')

    resolved_source = _detect_source(job_dir, explicit_source=source, plan=loaded_plan)
    try:
        if resolved_source == 'tmdb_supplement':
            details = _verify_tmdb_supplement(job_dir, now=now)
        elif resolved_source == 'tmdb_trending':
            details = _verify_tmdb_trending(job_dir, now=now)
        elif resolved_source in ('local_envelope', 'douban_archive'):
            details = _verify_local_envelope(job_dir, plan=loaded_plan)
        else:
            details = _verify_tmdb_catalog(
                job_dir,
                connection=connection,
                importer=importer,
                catalog_config=catalog_config,
            )
    except (_ingest.IngestError, _trending.TrendingArchiveError) as exc:
        raise HubInputError(exc.code, str(exc)) from None

    details.pop('_payloads', None)
    seal = _compute_commit_seal(
        job_dir,
        source=resolved_source,
        job_id=details['job_id'],
        status=details['status'],
        counts=details['counts'],
    )
    receipt = {
        'contract': HUB_CONTRACT,
        'rule_version': RULE_VERSION,
        'source': resolved_source,
        'job_id': details['job_id'],
        'archive_dir': str(job_dir),
        'status': details['status'],
        'counts': details['counts'],
        'error_breakdown': details['error_breakdown'],
        'anomaly_samples': details['anomaly_samples'][:MAX_ANOMALY_SAMPLES],
        'plan_digest': loaded_plan.get('plan_digest') if loaded_plan else None,
        'preview': details['preview'],
        'verified_at': _utc_now_iso(now),
        'commit_seal': seal,
    }
    if write_receipt:
        atomic_write_json(job_dir / 'verification-receipt.json', receipt)
    return receipt


# ------------------------------------------------------------------ commit


def commit_archive(
    archive_dir,
    *,
    job_id=None,
    source=None,
    execute=False,
    seal=None,
    connection=None,
    importer=None,
    catalog_config=None,
    job_ref=None,
    now=None,
):
    """Idempotent single-transaction commit gated by --execute and commit_seal verification."""
    if execute is not True:
        raise HubInputError('EXECUTE_FLAG_REQUIRED', 'database/archive commits require execute=True')

    job_dir = _resolve_job_dir(archive_dir, job_id=job_id, must_exist=True)
    receipt_path = job_dir / 'verification-receipt.json'
    if not receipt_path.is_file():
        raise HubRejected('VERIFICATION_REQUIRED', 'run verify before commit')
    stored_receipt = _read_json_safe(receipt_path, 'VERIFICATION_RECEIPT_INVALID')

    resolved_source = _detect_source(job_dir, explicit_source=source or stored_receipt.get('source'))
    fresh = verify_archive(
        job_dir,
        source=resolved_source,
        connection=connection,
        importer=importer,
        catalog_config=catalog_config,
        now=now,
        write_receipt=False,
    )

    expected_seal = fresh['commit_seal']
    stored_seal = str(stored_receipt.get('commit_seal') or '')
    provided_seal = str(seal if seal is not None else stored_seal)
    if not expected_seal or not secrets.compare_digest(stored_seal, expected_seal) or not secrets.compare_digest(provided_seal, expected_seal):
        raise HubRejected('SEAL_MISMATCH', 'archive contents or seal changed since verification')

    if fresh['status'] == 'REJECTED':
        raise HubRejected('ARCHIVE_REJECTED', 'archive verification status is REJECTED')
    if fresh['status'] == 'NEEDS_REVIEW':
        raise HubRejected('REVIEW_REQUIRED', 'blocking conflicts require review before commit')

    if resolved_source == 'tmdb_supplement':
        if connection is None:
            raise HubInputError('CONNECTION_REQUIRED', 'tmdb_supplement commit requires a database connection')
        try:
            writer_result = _ingest.run(connection, str(job_dir), job_ref=job_ref, mode='write', now=now)
        except _ingest.IngestRejected as exc:
            raise HubRejected(exc.code, str(exc)) from None
        except _ingest.IngestError as exc:
            raise HubInputError(exc.code, str(exc)) from None
        receipt = {
            'contract': HUB_CONTRACT,
            'rule_version': RULE_VERSION,
            'source': resolved_source,
            'job_id': fresh['job_id'],
            'status': 'COMMITTED',
            'commit_seal': expected_seal,
            'batch_id': writer_result.get('batch_id'),
            'idempotent': writer_result.get('idempotent', False),
            'counts': fresh['counts'],
            'writer_summary': writer_result,
        }
        atomic_write_json(job_dir / 'commit-receipt.json', receipt)
        return receipt

    if resolved_source == 'tmdb_trending':
        if connection is None:
            raise HubInputError('CONNECTION_REQUIRED', 'tmdb_trending commit requires a database connection')
        try:
            writer_result = _board_ingest.run(connection, str(job_dir), job_ref=job_ref, mode='write', now=now)
        except _ingest.IngestRejected as exc:
            raise HubRejected(exc.code, str(exc)) from None
        except _ingest.IngestError as exc:
            raise HubInputError(exc.code, str(exc)) from None
        receipt = {
            'contract': HUB_CONTRACT,
            'rule_version': RULE_VERSION,
            'source': resolved_source,
            'job_id': fresh['job_id'],
            'status': 'COMMITTED',
            'commit_seal': expected_seal,
            'batch_id': writer_result.get('batch_id'),
            'idempotent': writer_result.get('idempotent', False),
            'counts': fresh['counts'],
            'writer_summary': writer_result,
        }
        atomic_write_json(job_dir / 'commit-receipt.json', receipt)
        return receipt

    if resolved_source == 'tmdb_catalog':
        active_importer = importer
        if active_importer is None:
            if connection is None or catalog_config is None:
                raise HubInputError('IMPORTER_REQUIRED', 'tmdb_catalog commit requires importer or engine+catalog_config')
            engine = getattr(connection, 'engine', connection)
            active_importer = _admin_importer.CatalogImporter(engine, catalog_config)
        manifest = _read_json_safe(job_dir / 'manifest.json', 'INVALID_MANIFEST')
        payloads = []
        for entry in manifest.get('items') or []:
            if entry.get('status') == 'HTTP_200':
                tid = entry['tmdb_id']
                payloads.append(_read_json_safe(job_dir / 'normalized' / f'{tid}.json', 'ARCHIVE_CORRUPT'))
        if not payloads:
            raise HubRejected('ARCHIVE_REJECTED', 'no valid payloads to commit')
        expected_plans = fresh['preview'].get('actions') or None
        def _write_backup(before):
            atomic_write_json(job_dir / 'before-image.json', {'job_id': fresh['job_id'], 'before': before})
        try:
            writer_result = active_importer.apply(
                job_ref or fresh['job_id'],
                payloads,
                _write_backup,
                expected_plans=expected_plans,
            )
        except _admin_importer.ImportRejected as exc:
            raise HubRejected(str(exc), str(exc)) from None
        receipt = {
            'contract': HUB_CONTRACT,
            'rule_version': RULE_VERSION,
            'source': resolved_source,
            'job_id': fresh['job_id'],
            'status': 'COMMITTED',
            'commit_seal': expected_seal,
            'batch_id': writer_result.get('batch_id'),
            'idempotent': bool(writer_result.get('replayed')),
            'counts': fresh['counts'],
            'writer_summary': writer_result,
        }
        atomic_write_json(job_dir / 'commit-receipt.json', receipt)
        return receipt

    # local_envelope
    envelope = _read_json_safe(job_dir / 'envelope.json', 'INVALID_ENVELOPE')
    built = _data_pipeline.build(envelope)
    encoded = (_data_pipeline.canonical(built) + '\n').encode('utf-8')
    dataset_path = job_dir / 'dataset.json'
    if dataset_path.exists():
        if dataset_path.read_bytes() != encoded:
            raise HubRejected('DATASET_CONFLICT', 'existing dataset.json differs from verified envelope')
        dataset_replayed = True
    else:
        from .contracts import atomic_write_bytes
        atomic_write_bytes(dataset_path, encoded)
        dataset_replayed = False

    batch_id = None
    if connection is not None:
        stamp = datetime.now(timezone.utc).replace(tzinfo=None) if now is None else (
            now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now
        )
        snapshot_ref = f'local-envelope:{job_ref or fresh["job_id"]}'
        existing = _ingest._one(
            connection,
            'SELECT batch_id FROM ingest_batches WHERE source_snapshot_ref=%s AND status=%s',
            (snapshot_ref, 'succeeded'),
        )
        if existing is not None:
            batch_id = int(existing['batch_id'])
            dataset_replayed = True
        else:
            cursor = connection.execute(
                'INSERT INTO ingest_batches (source,mode,status,started_at,finished_at,'
                'source_snapshot_ref,counts_json,error_summary) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                (
                    built['metadata']['source'],
                    'write',
                    'succeeded',
                    stamp,
                    stamp,
                    snapshot_ref,
                    canonical_json(fresh['counts']),
                    None,
                ),
            )
            batch_id = _ingest._lastrowid(connection, cursor)
            connection.commit()

    receipt = {
        'contract': HUB_CONTRACT,
        'rule_version': RULE_VERSION,
        'source': resolved_source,
        'job_id': fresh['job_id'],
        'status': 'COMMITTED',
        'commit_seal': expected_seal,
        'batch_id': batch_id,
        'idempotent': dataset_replayed,
        'dataset_path': str(dataset_path),
        'counts': fresh['counts'],
    }
    atomic_write_json(job_dir / 'commit-receipt.json', receipt)
    return receipt


__all__ = [
    'CATALOG_MANIFEST_SCHEMA',
    'LOCAL_ENVELOPE_MANIFEST_SCHEMA',
    'MAX_ANOMALY_SAMPLES',
    'commit_archive',
    'run_capture',
    'summary_envelope',
    'verify_archive',
]
