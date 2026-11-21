"""Declarative target selector (Step 1: select -> job-plan.json) for ingest_hub."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from catalog.tmdb_supplement import capture as _capture
from catalog.tmdb_supplement import repository as _repository
from catalog.tmdb_supplement import trending as _trending
from catalog.tmdb_supplement import writer as _writer

from .contracts import (
    HUB_CONTRACT,
    RULE_VERSION,
    SUPPORTED_SOURCES,
    HubInputError,
    atomic_write_json,
    is_positive_int,
    sha256_json,
    validate_archive_dir,
    validate_job_id,
    validate_safe_file,
)

MAX_CATALOG_IDS = 500
MAX_CATALOG_REQUESTS = 1500
MAX_SUPPLEMENT_TARGETS = _capture.MAX_TARGETS
MAX_SUPPLEMENT_REQUESTS = _capture.MAX_REQUESTS
MISSING_STATUSES = frozenset(_repository.MISSING_STATUSES)  # FAILED, MISSING, NOT_FETCHED


def _parse_topics(topics):
    if topics is None:
        return list(_writer.TOPICS)
    if isinstance(topics, str):
        parts = [item.strip() for item in topics.split(',') if item.strip()]
    elif isinstance(topics, (list, tuple, set)):
        parts = [str(item).strip() for item in topics if str(item).strip()]
    else:
        raise HubInputError('INVALID_TOPIC', 'topics must be a comma-separated string or sequence')
    if not parts:
        raise HubInputError('INVALID_TOPIC', 'topics must not be empty')
    ordered = []
    seen = set()
    for topic in parts:
        if topic not in _writer.TOPICS:
            raise HubInputError('INVALID_TOPIC', f'unsupported topic: {topic}')
        if topic not in seen:
            seen.add(topic)
            ordered.append(topic)
    return ordered


def _parse_limit(limit, *, default=None, maximum=MAX_SUPPLEMENT_TARGETS):
    if limit is None:
        return default
    if type(limit) is not int or limit < 1 or limit > maximum:
        raise HubInputError('LIMIT_INVALID', f'limit must be an integer in 1..{maximum}')
    return limit


def _parse_budget(max_requests, *, default, maximum):
    if max_requests is None:
        return default
    if type(max_requests) is not int or max_requests < 1 or max_requests > maximum:
        raise HubInputError('MAX_REQUESTS_INVALID', f'max_requests must be an integer in 1..{maximum}')
    return max_requests


def _parse_utc(value, label='before'):
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith('Z'):
            raw = raw[:-1] + '+00:00'
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            raise HubInputError('INVALID_TIMESTAMP', f'{label} must be ISO-8601') from None
    else:
        raise HubInputError('INVALID_TIMESTAMP', f'{label} must be a datetime or ISO-8601 string')
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _iso_utc(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        return _parse_utc(dt, 'timestamp').replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')


def _parse_field_status(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _load_explicit_targets(targets):
    if isinstance(targets, (str, Path)):
        path = validate_safe_file(targets, allow_c_read=True, must_exist=True)
        try:
            targets = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as exc:
            raise HubInputError('TARGET_FILE_INVALID', 'cannot parse targets JSON file') from exc
    if isinstance(targets, (list, tuple)):
        normalized = []
        for item in targets:
            if isinstance(item, str) and ':' in item:
                left, right = item.split(':', 1)
                if not left.strip().isdigit() or not right.strip().isdigit():
                    raise HubInputError('TARGET_ID_INVALID', 'movie_id:tmdb_id must be positive integers')
                normalized.append({'movie_id': int(left.strip()), 'tmdb_id': int(right.strip())})
            else:
                normalized.append(item)
        targets = normalized
    try:
        prepared, duplicates = _capture._prepare_targets(targets)
    except _capture.CaptureError as exc:
        raise HubInputError(exc.code, str(exc)) from None
    return prepared, duplicates


def _load_catalog_tmdb_ids(tmdb_ids):
    """Parse explicit tmdb_ids from a JSON file path, comma string, or int sequence."""
    if isinstance(tmdb_ids, (list, tuple)) and len(tmdb_ids) == 1 and isinstance(tmdb_ids[0], (str, Path)):
        single = str(tmdb_ids[0]).strip()
        if single.lower().endswith('.json') or ',' in single:
            tmdb_ids = single

    if isinstance(tmdb_ids, Path) or (isinstance(tmdb_ids, str) and tmdb_ids.strip().lower().endswith('.json')):
        path = validate_safe_file(tmdb_ids, allow_c_read=True, must_exist=True)
        try:
            loaded = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as exc:
            raise HubInputError('TMDB_IDS_INVALID', 'cannot parse tmdb_ids JSON file') from exc
        if isinstance(loaded, dict):
            if 'tmdb_ids' in loaded and isinstance(loaded['tmdb_ids'], list):
                loaded = loaded['tmdb_ids']
            elif 'targets' in loaded and isinstance(loaded['targets'], list):
                loaded = [
                    item.get('tmdb_id') if isinstance(item, dict) else item
                    for item in loaded['targets']
                ]
            else:
                raise HubInputError('TMDB_IDS_INVALID', 'tmdb_ids JSON object must contain tmdb_ids or targets list')
        if not isinstance(loaded, list):
            raise HubInputError('TMDB_IDS_INVALID', 'tmdb_ids JSON must be a list of positive integers')
        return loaded

    if isinstance(tmdb_ids, str):
        raw_parts = [p.strip() for p in tmdb_ids.split(',') if p.strip()]
        if not raw_parts or any(not p.isdigit() for p in raw_parts):
            raise HubInputError('TMDB_IDS_INVALID', 'tmdb_ids must contain positive integers')
        return [int(p) for p in raw_parts]

    if isinstance(tmdb_ids, (list, tuple)):
        return list(tmdb_ids)

    raise HubInputError('TMDB_IDS_INVALID', f'tmdb_ids must be a sequence of 1..{MAX_CATALOG_IDS} positive integers')


def _rows_from_connection(connection):
    """Read only narrow identity + field_status/fetched_at columns for active movies."""
    sql = (
        "SELECT m.movie_id AS movie_id, m.tmdb_id AS tmdb_id, "
        "m.field_status_json AS field_status_json, m.fetched_at AS fetched_at "
        "FROM tmdb_movie_metadata AS m "
        "JOIN movies AS mv ON mv.movie_id = m.movie_id "
        "WHERE mv.catalog_status = 'active' "
        "ORDER BY m.movie_id"
    )
    if hasattr(connection, 'execute'):
        try:
            res = connection.execute(text(sql))
            if hasattr(res, 'mappings'):
                return [dict(r) for r in res.mappings().all()]
            rows = res.fetchall()
            cols = [d[0] for d in (getattr(res, 'description', None) or [])]
            if cols:
                return [dict(zip(cols, r)) for r in rows]
        except SQLAlchemyError:
            pass
    return None


def _select_supplement_missing(*, connection, documents, missing_rows, wanted_topics, cap):
    topic_set = set(wanted_topics)
    candidates = {}  # movie_id -> {'tmdb_id': int, 'missing_topics': set, 'empty_topics': set}
    excluded_empty_movies = 0
    excluded_present_movies = 0

    if missing_rows is None and documents is None and connection is not None:
        if hasattr(_repository, 'load_missing'):
            missing_rows = _repository.load_missing(connection)
        else:
            db_rows = _rows_from_connection(connection)
            if db_rows is not None:
                documents = [
                    {
                        'movie_id': row['movie_id'],
                        'tmdb_id': row['tmdb_id'],
                        'field_status': _parse_field_status(row.get('field_status_json')),
                        'fetched_at': row.get('fetched_at'),
                        'catalog_status': 'active',
                    }
                    for row in db_rows
                ]
            else:
                # Fallback to repository.load_missing_queue paging (MySQL JSON_TABLE path)
                collected = []
                offset = 0
                while True:
                    batch = _repository.load_missing_queue(connection, limit=500, offset=offset)
                    if not batch:
                        break
                    collected.extend(batch)
                    if len(batch) < 500 or len(collected) >= 5000:
                        break
                    offset += len(batch)
                missing_rows = collected

    if documents is not None:
        by_movie = {}
        by_tmdb = {}
        for doc in documents:
            if not isinstance(doc, dict):
                continue
            if doc.get('catalog_status') is not None and doc.get('catalog_status') != 'active':
                continue
            mid = doc.get('movie_id')
            tid = doc.get('tmdb_id')
            if not is_positive_int(mid) or not is_positive_int(tid):
                continue
            if mid in by_movie and by_movie[mid] != tid:
                raise HubInputError('MOVIE_ID_MULTIPLE_TMDB', f'conflicting tmdb_id for movie_id={mid}')
            if tid in by_tmdb and by_tmdb[tid] != mid:
                raise HubInputError('TMDB_ID_MULTIPLE_MOVIE', f'conflicting movie_id for tmdb_id={tid}')
            by_movie[mid] = tid
            by_tmdb[tid] = mid
            status_map = _parse_field_status(doc.get('field_status') or doc.get('field_status_json'))
            missing = []
            empty_hits = []
            for topic in wanted_topics:
                st = str(status_map.get(topic) or 'NOT_FETCHED').strip().upper()
                if st in MISSING_STATUSES:
                    missing.append(topic)
                elif st == 'EMPTY':
                    empty_hits.append(topic)
            if missing:
                candidates[mid] = {'movie_id': mid, 'tmdb_id': tid, 'missing_topics': missing}
            elif empty_hits:
                excluded_empty_movies += 1
            else:
                excluded_present_movies += 1
    elif missing_rows is not None:
        grouped = {}
        empty_only = set()
        for row in missing_rows:
            if not isinstance(row, dict):
                continue
            topic = row.get('topic')
            if topic not in topic_set:
                continue
            mid = row.get('movie_id')
            tid = row.get('tmdb_id')
            if not is_positive_int(mid) or not is_positive_int(tid):
                continue
            st = str(row.get('status') or 'NOT_FETCHED').strip().upper()
            if st == 'EMPTY':
                empty_only.add(mid)
                continue
            if st not in MISSING_STATUSES:
                continue
            entry = grouped.setdefault(mid, {'movie_id': mid, 'tmdb_id': tid, 'missing_topics': []})
            if entry['tmdb_id'] != tid:
                raise HubInputError('MOVIE_ID_MULTIPLE_TMDB', f'conflicting tmdb_id for movie_id={mid}')
            if topic not in entry['missing_topics']:
                entry['missing_topics'].append(topic)
        for mid in empty_only:
            if mid not in grouped:
                excluded_empty_movies += 1
        candidates = grouped
    else:
        raise HubInputError('SOURCE_DATA_REQUIRED', 'tmdb_supplement missing scope requires connection, documents or missing_rows')

    ordered = [candidates[mid] for mid in sorted(candidates)]
    total_candidates = len(ordered)
    selected = ordered[:cap]
    return {
        'targets': selected,
        'candidate_count': total_candidates,
        'selected_count': len(selected),
        'truncated': total_candidates > len(selected),
        'excluded_empty_count': excluded_empty_movies,
        'excluded_present_count': excluded_present_movies,
    }


def _select_supplement_stale(*, connection, documents, before, cap):
    cutoff = _parse_utc(before, 'before')
    rows = []
    if documents is not None:
        for doc in documents:
            if not isinstance(doc, dict):
                continue
            if doc.get('catalog_status') is not None and doc.get('catalog_status') != 'active':
                continue
            mid = doc.get('movie_id')
            tid = doc.get('tmdb_id')
            if not is_positive_int(mid) or not is_positive_int(tid):
                continue
            raw_at = doc.get('fetched_at')
            fetched_dt = _parse_utc(raw_at, 'fetched_at') if raw_at is not None else None
            if fetched_dt is None or fetched_dt < cutoff:
                rows.append((fetched_dt or datetime.min, mid, tid))
    elif connection is not None:
        db_rows = _rows_from_connection(connection)
        if db_rows is None:
            raise HubInputError('SOURCE_DATA_REQUIRED', 'cannot read stale movies from connection')
        for row in db_rows:
            mid = row.get('movie_id')
            tid = row.get('tmdb_id')
            if not is_positive_int(mid) or not is_positive_int(tid):
                continue
            raw_at = row.get('fetched_at')
            fetched_dt = _parse_utc(raw_at, 'fetched_at') if raw_at is not None else None
            if fetched_dt is None or fetched_dt < cutoff:
                rows.append((fetched_dt or datetime.min, mid, tid))
    else:
        raise HubInputError('SOURCE_DATA_REQUIRED', 'tmdb_supplement stale scope requires connection or documents')

    rows.sort(key=lambda item: (item[0], item[1]))
    seen_mid = set()
    ordered = []
    for fetched_dt, mid, tid in rows:
        if mid in seen_mid:
            continue
        seen_mid.add(mid)
        ordered.append({
            'movie_id': mid,
            'tmdb_id': tid,
            'fetched_at': None if fetched_dt == datetime.min else _iso_utc(fetched_dt),
        })
    selected = ordered[:cap]
    return {
        'targets': selected,
        'candidate_count': len(ordered),
        'selected_count': len(selected),
        'truncated': len(ordered) > len(selected),
        'before': _iso_utc(cutoff),
    }


def select_targets(
    *,
    source,
    job_id,
    scope=None,
    connection=None,
    documents=None,
    missing_rows=None,
    targets=None,
    tmdb_ids=None,
    topics=None,
    limit=None,
    max_requests=None,
    before=None,
    pages=2,
    language='zh-CN',
    input_path=None,
    metadata=None,
    output_plan_path=None,
):
    """Select bounded targets for one of the 4 supported ingest sources and build job-plan."""
    if source not in SUPPORTED_SOURCES:
        raise HubInputError('UNSUPPORTED_SOURCE', f'unsupported source: {source}')
    validate_job_id(job_id)

    try:
        _capture._validate_language(language)
    except _capture.CaptureError as exc:
        raise HubInputError(exc.code, str(exc)) from None

    warnings = []
    plan = {
        'contract': HUB_CONTRACT,
        'rule_version': RULE_VERSION,
        'job_id': job_id,
        'source': source,
        'language': language,
    }

    if source == 'tmdb_supplement':
        if scope is None:
            if targets is not None:
                scope = 'explicit'
            elif before is not None:
                scope = 'stale'
            else:
                scope = 'missing'
        if scope not in ('missing', 'explicit', 'stale'):
            raise HubInputError('INVALID_SCOPE', f'invalid scope for tmdb_supplement: {scope}')
        wanted_topics = _parse_topics(topics)
        parsed_limit = _parse_limit(limit, default=None, maximum=MAX_SUPPLEMENT_TARGETS)
        budget = _parse_budget(
            max_requests,
            default=min(parsed_limit or 500, MAX_SUPPLEMENT_REQUESTS),
            maximum=MAX_SUPPLEMENT_REQUESTS,
        )
        cap = min(parsed_limit, budget) if parsed_limit is not None else budget
        plan['scope'] = scope
        plan['topics'] = wanted_topics
        plan['request_budget'] = budget

        if scope == 'missing':
            meta = _select_supplement_missing(
                connection=connection,
                documents=documents,
                missing_rows=missing_rows,
                wanted_topics=wanted_topics,
                cap=cap,
            )
            plan['targets'] = meta['targets']
            plan['estimated_requests'] = len(meta['targets'])
            plan['selection'] = {k: v for k, v in meta.items() if k != 'targets'}
        elif scope == 'stale':
            if before is None:
                raise HubInputError('BEFORE_REQUIRED', 'stale scope requires --before timestamp')
            meta = _select_supplement_stale(
                connection=connection,
                documents=documents,
                before=before,
                cap=cap,
            )
            plan['targets'] = meta['targets']
            plan['estimated_requests'] = len(meta['targets'])
            plan['selection'] = {k: v for k, v in meta.items() if k != 'targets'}
        else:  # explicit
            if targets is None:
                raise HubInputError('TARGETS_REQUIRED', 'explicit scope requires targets')
            prepared, duplicates = _load_explicit_targets(targets)
            if parsed_limit is not None:
                prepared = prepared[:parsed_limit]
            if len(prepared) > budget:
                warnings.append('BUDGET_INSUFFICIENT_FOR_ALL_TARGETS')
            plan['targets'] = prepared
            plan['estimated_requests'] = min(len(prepared), budget)
            plan['selection'] = {
                'candidate_count': len(prepared),
                'selected_count': len(prepared),
                'duplicate_targets': duplicates,
                'truncated': False,
            }

    elif source == 'tmdb_trending':
        scope = scope or 'boards'
        if scope != 'boards':
            raise HubInputError('INVALID_SCOPE', f'invalid scope for tmdb_trending: {scope}')
        if type(pages) is not int or pages not in (1, 2):
            raise HubInputError('PAGES_INVALID', 'pages must be 1 or 2')
        budget = _parse_budget(max_requests, default=_trending.MAX_REQUESTS, maximum=_trending.MAX_REQUESTS)
        board_targets = [
            {'channel': channel, 'page': page}
            for channel in _trending.CHANNELS
            for page in range(1, pages + 1)
        ]
        if len(board_targets) > budget:
            warnings.append('BUDGET_INSUFFICIENT_FOR_ALL_TARGETS')
        plan['scope'] = scope
        plan['pages'] = pages
        plan['request_budget'] = budget
        plan['targets'] = board_targets
        plan['estimated_requests'] = min(len(board_targets), budget)
        plan['selection'] = {
            'channels': list(_trending.CHANNELS),
            'pages': pages,
            'selected_count': len(board_targets),
        }

    elif source == 'tmdb_catalog':
        scope = scope or 'catalog'
        if scope != 'catalog':
            raise HubInputError('INVALID_SCOPE', f'invalid scope for tmdb_catalog: {scope}')
        ids = _load_catalog_tmdb_ids(tmdb_ids)
        if not ids:
            raise HubInputError('TMDB_IDS_INVALID', f'tmdb_ids count must be 1..{MAX_CATALOG_IDS}')
        if any(not is_positive_int(tid) or tid > 9999999999 for tid in ids) or len(set(ids)) != len(ids):
            raise HubInputError('TMDB_IDS_INVALID', 'tmdb_ids must be unique integers in 1..9999999999')
        candidate_count = len(ids)
        parsed_limit = _parse_limit(limit, default=None, maximum=MAX_CATALOG_IDS)
        if parsed_limit is not None:
            ids = ids[:parsed_limit]
        if not 1 <= len(ids) <= MAX_CATALOG_IDS:
            raise HubInputError('TMDB_IDS_INVALID', f'tmdb_ids count must be 1..{MAX_CATALOG_IDS}')
        budget = _parse_budget(max_requests, default=len(ids), maximum=MAX_CATALOG_REQUESTS)
        if len(ids) > budget:
            warnings.append('BUDGET_INSUFFICIENT_FOR_ALL_TARGETS')
        plan['scope'] = scope
        plan['request_budget'] = budget
        plan['targets'] = [{'tmdb_id': tid} for tid in ids]
        plan['estimated_requests'] = min(len(ids), budget)
        plan['selection'] = {
            'candidate_count': candidate_count,
            'selected_count': len(ids),
            'truncated': candidate_count > len(ids),
        }

    elif source == 'douban_archive':
        scope = scope or 'douban'
        if scope not in ('douban', 'envelope'):
            raise HubInputError('INVALID_SCOPE', f'invalid scope for douban_archive: {scope}')
        if not input_path:
            raise HubInputError('INPUT_PATH_REQUIRED', 'douban_archive requires input_path')
        safe_input = validate_safe_file(input_path, allow_c_read=True, must_exist=True)
        suffix = safe_input.suffix.lower()
        if suffix not in ('.json', '.jsonl', '.ndjson'):
            raise HubInputError('INPUT_FORMAT_INVALID', 'douban_archive supports .json, .jsonl, or .ndjson only')
        plan['scope'] = scope
        plan['request_budget'] = 0
        plan['estimated_requests'] = 0
        plan['targets'] = [{'input_path': str(safe_input), 'format': suffix.lstrip('.')}]
        plan['selection'] = {'selected_count': 1}

    else:  # local_envelope
        scope = scope or 'envelope'
        if scope != 'envelope':
            raise HubInputError('INVALID_SCOPE', f'invalid scope for local_envelope: {scope}')
        if not input_path:
            raise HubInputError('INPUT_PATH_REQUIRED', 'local_envelope requires input_path')
        safe_input = validate_safe_file(input_path, allow_c_read=True, must_exist=True)
        suffix = safe_input.suffix.lower()
        if suffix not in ('.json', '.csv'):
            raise HubInputError('INPUT_FORMAT_INVALID', 'local_envelope supports .json or .csv only')
        meta_obj = None
        if metadata is not None:
            if isinstance(metadata, dict):
                meta_obj = metadata
            elif isinstance(metadata, (str, Path)):
                meta_file = validate_safe_file(metadata, allow_c_read=True, must_exist=True)
                try:
                    meta_obj = json.loads(meta_file.read_text(encoding='utf-8-sig'))
                except (OSError, ValueError) as exc:
                    raise HubInputError('METADATA_INVALID', 'invalid metadata JSON') from exc
            else:
                raise HubInputError('METADATA_INVALID', 'metadata must be dict or JSON path')
        if suffix == '.csv' and meta_obj is None:
            raise HubInputError('METADATA_REQUIRED', 'CSV local_envelope requires metadata')
        plan['scope'] = scope
        plan['request_budget'] = 0
        plan['estimated_requests'] = 0
        target_entry = {'input_path': str(safe_input), 'format': suffix.lstrip('.')}
        if meta_obj is not None:
            target_entry['metadata'] = meta_obj
        plan['targets'] = [target_entry]
        plan['selection'] = {'selected_count': 1}

    plan['warnings'] = warnings
    plan['plan_digest'] = sha256_json(plan)

    if output_plan_path is not None:
        out_path = Path(output_plan_path)
        validate_archive_dir(out_path.parent, must_exist=False, create=True)
        atomic_write_json(out_path, plan)
    return plan


def load_plan(plan_or_path):
    """Load and verify integrity of a job-plan.json file or dict."""
    if isinstance(plan_or_path, dict):
        plan = dict(plan_or_path)
    elif isinstance(plan_or_path, (str, Path)):
        path = validate_safe_file(plan_or_path, allow_c_read=True, must_exist=True)
        try:
            plan = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise HubInputError('PLAN_INVALID', 'cannot parse job-plan JSON') from exc
    else:
        raise HubInputError('PLAN_INVALID', 'plan must be a dict or path')
    if not isinstance(plan, dict) or plan.get('contract') != HUB_CONTRACT:
        raise HubInputError('PLAN_CONTRACT_INVALID', 'unsupported job-plan contract')
    expected = plan.get('plan_digest')
    if not isinstance(expected, str) or len(expected) != 64:
        raise HubInputError('PLAN_DIGEST_INVALID', 'missing or malformed plan_digest')
    body = {k: v for k, v in plan.items() if k != 'plan_digest'}
    if sha256_json(body) != expected:
        raise HubInputError('PLAN_DIGEST_MISMATCH', 'job-plan digest mismatch')
    return plan


__all__ = ['MAX_CATALOG_IDS', 'MAX_CATALOG_REQUESTS', 'select_targets', 'load_plan']
