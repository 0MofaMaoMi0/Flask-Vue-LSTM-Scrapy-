"""Douban Scrapy/raw-export adapter to standard local_envelope and archive bundles.

Pure offline stdlib implementation (no Flask, SQLAlchemy, network or C: writes).
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from .contracts import (
    HUB_CONTRACT,
    RULE_VERSION,
    HubInputError,
    HubSecurityError,
    atomic_write_bytes,
    atomic_write_json,
    canonical_json,
    is_reparse,
    sha256_bytes,
    sha256_json,
    validate_archive_dir,
    validate_job_id,
    validate_safe_file,
)

DOUBAN_ADAPTER_VERSION = 'douban-archive-v1'
LOCAL_ENVELOPE_MANIFEST_SCHEMA = 'local-envelope-archive-v1'
IMDB_RE = re.compile(r'^tt\d+$')
DATE_CANDIDATE_RE = re.compile(r'(\d{4})-(\d{1,2})-(\d{1,2})')
YEAR_RE = re.compile(r'(?<!\d)(\d{4})(?!\d)')
INT_RE = re.compile(r'\d+')


def _utc_now_iso(now=None):
    if isinstance(now, datetime):
        dt = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    if isinstance(now, str) and now.strip():
        text = now.strip()
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _clean_text(value, max_len=None):
    if value is None:
        return ''
    if not isinstance(value, str):
        value = str(value)
    normalized = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value)).strip()
    if max_len is not None and len(normalized) > max_len:
        normalized = normalized[:max_len].strip()
    return normalized


def _split_list(value, *, max_len=255):
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.split('/')
    elif isinstance(value, (list, tuple)):
        parts = []
        for entry in value:
            if isinstance(entry, str):
                parts.extend(entry.split('/'))
            elif entry is not None:
                parts.append(str(entry))
    else:
        return []
    seen = set()
    cleaned = []
    for part in parts:
        text = _clean_text(part, max_len=max_len)
        if text and text not in seen:
            seen.add(text)
            cleaned.append(text)
    return cleaned


def _parse_year(raw_year):
    if raw_year is None or isinstance(raw_year, bool):
        return None
    if isinstance(raw_year, int):
        return raw_year if 1800 <= raw_year <= 2199 else None
    text = _clean_text(raw_year)
    if not text or text == '0':
        return None
    match = YEAR_RE.search(text)
    if not match:
        return None
    year_val = int(match.group(1))
    return year_val if 1800 <= year_val <= 2199 else None


def _parse_release_date(raw_date, release_year):
    if raw_date is None:
        return None
    if isinstance(raw_date, datetime):
        iso = raw_date.date().isoformat()
        candidates = [iso]
    elif isinstance(raw_date, date):
        candidates = [raw_date.isoformat()]
    else:
        text = _clean_text(raw_date)
        if not text:
            return None
        candidates = []
        for y_s, m_s, d_s in DATE_CANDIDATE_RE.findall(text):
            try:
                parsed = date(int(y_s), int(m_s), int(d_s))
            except ValueError:
                continue
            if 1800 <= parsed.year <= 2199:
                candidates.append(parsed.isoformat())
    if not candidates:
        return None
    if release_year is not None:
        for iso in candidates:
            if int(iso[:4]) == release_year:
                return iso
        return None
    return candidates[0]


def _parse_runtime(raw_runtime):
    if raw_runtime is None or isinstance(raw_runtime, bool):
        return None
    if isinstance(raw_runtime, int):
        return raw_runtime if 1 <= raw_runtime <= 1500 else None
    text = _clean_text(raw_runtime)
    if not text:
        return None
    match = INT_RE.search(text)
    if not match:
        return None
    val = int(match.group(0))
    return val if 1 <= val <= 1500 else None


def _parse_language(raw_lang):
    if isinstance(raw_lang, (list, tuple)):
        candidates = [_clean_text(item) for item in raw_lang]
    else:
        text = _clean_text(raw_lang)
        candidates = re.split(r'[/,、]', text) if text else []
    for cand in candidates:
        cleaned = _clean_text(cand, max_len=16)
        if cleaned:
            return cleaned
    return None


def _parse_people_group(raw_names, raw_ids, role):
    names = _split_list(raw_names, max_len=255)
    if not names:
        return []

    if raw_ids is None:
        raw_id_entries = []
    elif isinstance(raw_ids, str):
        raw_id_entries = [part.strip() for part in raw_ids.split('|') if part.strip()]
    elif isinstance(raw_ids, (list, tuple)):
        raw_id_entries = [str(part).strip() for part in raw_ids if str(part).strip()]
    else:
        raw_id_entries = []

    by_name = {}
    positional = []
    for entry in raw_id_entries:
        cleaned_entry = _clean_text(entry, max_len=300)
        if ':' in cleaned_entry:
            person_name, ext_id = cleaned_entry.rsplit(':', 1)
            person_name = _clean_text(person_name, max_len=255)
            ext_id = _clean_text(ext_id, max_len=128)
            if person_name and ext_id:
                by_name.setdefault(person_name, ext_id)
                positional.append(ext_id)
                continue
        ext_id = _clean_text(cleaned_entry, max_len=128)
        if ext_id:
            positional.append(ext_id)

    people = []
    seen = set()
    for idx, name in enumerate(names):
        ext_id = by_name.get(name)
        if not ext_id and idx < len(positional):
            ext_id = positional[idx]
        if not ext_id:
            digest = hashlib.sha256(name.encode('utf-8')).hexdigest()[:16]
            ext_id = f'name:{digest}'
        ext_id = _clean_text(ext_id, max_len=128)
        if not ext_id or not name:
            continue
        key = (role, ext_id, name)
        if key in seen:
            continue
        seen.add(key)
        people.append({
            'source': 'douban',
            'external_id': ext_id,
            'name': name,
            'role': role,
        })
    return people


def _parse_ratings(raw_score, raw_votes, observed_at):
    votes = None
    if raw_votes not in (None, '') and not isinstance(raw_votes, bool):
        if isinstance(raw_votes, int):
            if 0 <= raw_votes <= 9223372036854775807:
                votes = raw_votes
        else:
            vote_text = _clean_text(raw_votes)
            if vote_text.isdecimal():
                val = int(vote_text)
                if 0 <= val <= 9223372036854775807:
                    votes = val

    score = None
    if raw_score not in (None, '') and not isinstance(raw_score, bool):
        score_text = _clean_text(raw_score)
        if score_text:
            try:
                dec = Decimal(score_text)
                if dec.is_finite() and Decimal('0') <= dec <= Decimal('10'):
                    if dec.as_tuple().exponent < -2:
                        dec = dec.quantize(Decimal('0.01'))
                    score = float(dec)
            except InvalidOperation:
                score = None

    if votes is None and score is None:
        return []
    if votes == 0:
        # data_pipeline enforces: zero votes requires null score
        score = None
    if votes is None and score == 0.0:
        return []

    return [{
        'source': 'douban',
        'value': score,
        'scale': 10,
        'votes': votes,
        'observed_at': observed_at,
    }]


def _as_mapping(item):
    if isinstance(item, dict):
        return dict(item)
    return {k: item[k] for k in item}


def classify_douban_item(item):
    """Classify a Scrapy Item or raw dict as movie | person | comment | subject | book."""
    raw = _as_mapping(item)
    explicit = _clean_text(raw.get('__item_type__') or raw.get('item_type') or '').lower()
    if explicit in ('movie', 'moviemeta'):
        return 'movie'
    if explicit in ('person', 'personmeta'):
        return 'person'
    if explicit in ('comment',):
        return 'comment'
    if explicit in ('subject',):
        return 'subject'
    if explicit in ('book', 'bookmeta'):
        return 'book'

    cls_name = type(item).__name__
    if cls_name == 'MovieMeta':
        return 'movie'
    if cls_name == 'PersonMeta':
        return 'person'
    if cls_name == 'Comment':
        return 'comment'
    if cls_name == 'Subject':
        return 'subject'
    if cls_name == 'BookMeta':
        return 'book'

    if 'douban_comment_id' in raw or ('content' in raw and ('comment_time' in raw or 'douban_user_nickname' in raw)):
        return 'comment'
    if 'person_id' in raw and 'douban_id' not in raw:
        return 'person'
    if 'isbn' in raw or 'authors' in raw or 'publisher' in raw:
        return 'book'
    if 'douban_id' in raw and not any(k in raw for k in ('name', 'title', 'directors', 'actors', 'genres', 'storyline', 'overview')):
        return 'subject'
    return 'movie'


def douban_item_to_envelope_record(item, *, observed_at=None):
    """Convert one Douban MovieMeta (or raw dict) into a data_pipeline record."""
    raw = _as_mapping(item)
    obs = _utc_now_iso(observed_at)
    raw_did = _clean_text(raw.get('douban_id'), max_len=128)
    title = _clean_text(raw.get('name') or raw.get('title'), max_len=255)

    external_ids = {}
    if raw_did:
        external_ids['douban'] = raw_did

    imdb_raw = _clean_text(raw.get('imdb_id'), max_len=64)
    if imdb_raw and IMDB_RE.fullmatch(imdb_raw):
        external_ids['imdb'] = imdb_raw

    raw_type = _clean_text(raw.get('media_type') or raw.get('type') or '').lower()
    if not raw_type:
        media_type = 'book' if classify_douban_item(item) == 'book' else 'movie'
    else:
        media_type = raw_type

    release_year = _parse_year(raw.get('year') if raw.get('year') not in (None, '') else raw.get('release_year'))
    release_date = _parse_release_date(raw.get('release_date'), release_year)
    if release_year is None and release_date:
        release_year = int(release_date[:4])

    runtime_minutes = _parse_runtime(
        raw.get('mins') if raw.get('mins') not in (None, '') else raw.get('runtime_minutes')
    )
    language = _parse_language(
        raw.get('languages') if raw.get('languages') not in (None, '') else raw.get('language')
    )
    aliases = _split_list(raw.get('alias') if raw.get('alias') not in (None, '') else raw.get('aliases'), max_len=255)
    genres = _split_list(raw.get('genres'), max_len=64)
    overview = _clean_text(
        raw.get('storyline') if raw.get('storyline') not in (None, '') else raw.get('overview'),
        max_len=10000,
    )

    people = []
    people.extend(_parse_people_group(raw.get('directors'), raw.get('director_ids'), 'crew'))
    people.extend(_parse_people_group(raw.get('actors'), raw.get('actor_ids'), 'cast'))
    if isinstance(raw.get('people'), list) and not people:
        people = list(raw['people'])

    ratings = _parse_ratings(raw.get('douban_score'), raw.get('douban_votes'), obs)

    record = {
        'source_record_id': f'douban:{raw_did}' if raw_did else '',
        'external_ids': external_ids,
        'title': title,
        'aliases': aliases,
        'genres': genres,
        'people': people,
        'ratings': ratings,
        'media_type': media_type,
    }
    original_title = _clean_text(raw.get('original_title'), max_len=255)
    if original_title:
        record['original_title'] = original_title
    if release_date:
        record['release_date'] = release_date
    if release_year is not None:
        record['release_year'] = release_year
    if runtime_minutes is not None:
        record['runtime_minutes'] = runtime_minutes
    if language:
        record['language'] = language
    if overview:
        record['overview'] = overview

    legacy_table = _clean_text(raw.get('legacy_table'))
    legacy_pk = raw.get('legacy_pk')
    if legacy_table and legacy_pk not in (None, ''):
        record['legacy_table'] = legacy_table
        record['legacy_pk'] = legacy_pk
    return record


def _normalize_person(item):
    raw = _as_mapping(item)
    return {
        'person_id': _clean_text(raw.get('person_id'), max_len=64),
        'name': _clean_text(raw.get('name'), max_len=255),
        'sex': _clean_text(raw.get('sex'), max_len=32),
        'birth': _clean_text(raw.get('birth'), max_len=64),
        'birthplace': _clean_text(raw.get('birthplace'), max_len=255),
        'biography': _clean_text(raw.get('biography'), max_len=10000),
        'profession': _clean_text(raw.get('profession'), max_len=255),
        'constellatory': _clean_text(raw.get('constellatory'), max_len=64),
        'name_zh': _clean_text(raw.get('name_zh'), max_len=255),
        'name_en': _clean_text(raw.get('name_en'), max_len=255),
    }


def _normalize_comment(item):
    raw = _as_mapping(item)
    votes = raw.get('votes')
    if isinstance(votes, str) and votes.strip().isdecimal():
        votes = int(votes.strip())
    elif not isinstance(votes, int) or isinstance(votes, bool):
        votes = 0
    return {
        'douban_id': _clean_text(raw.get('douban_id'), max_len=64),
        'douban_comment_id': _clean_text(raw.get('douban_comment_id'), max_len=64),
        'douban_user_nickname': _clean_text(raw.get('douban_user_nickname'), max_len=255),
        'douban_user_avatar': _clean_text(raw.get('douban_user_avatar'), max_len=512),
        'douban_user_url': _clean_text(raw.get('douban_user_url'), max_len=512),
        'content': _clean_text(raw.get('content'), max_len=10000),
        'votes': votes,
        'rating': _clean_text(raw.get('rating'), max_len=32),
        'comment_time': _clean_text(raw.get('comment_time'), max_len=64),
    }


def load_douban_raw(input_path):
    """Load raw Douban items from a .json or .jsonl/.ndjson file safely."""
    safe_path = validate_safe_file(input_path, allow_c_read=True, must_exist=True)
    suffix = safe_path.suffix.lower()
    text = safe_path.read_text(encoding='utf-8-sig')
    if suffix in ('.jsonl', '.ndjson'):
        items = []
        for idx, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except ValueError as exc:
                raise HubInputError('INVALID_DOUBAN_INPUT', f'invalid JSONL on line {idx}') from exc
            if not isinstance(obj, dict):
                raise HubInputError('INVALID_DOUBAN_INPUT', f'JSONL line {idx} must be an object')
            items.append(obj)
        return items
    if suffix == '.json':
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise HubInputError('INVALID_DOUBAN_INPUT', 'invalid JSON input') from exc
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ('items', 'records'):
                if isinstance(payload.get(key), list):
                    return payload[key]
            grouped = []
            found_group = False
            for key, tag in (('movies', 'movie'), ('persons', 'person'), ('comments', 'comment')):
                if isinstance(payload.get(key), list):
                    found_group = True
                    for entry in payload[key]:
                        if isinstance(entry, dict):
                            item = dict(entry)
                            item.setdefault('__item_type__', tag)
                            grouped.append(item)
            if found_group:
                return grouped
            return [payload]
        raise HubInputError('INVALID_DOUBAN_INPUT', 'JSON input must be a list or object')
    raise HubInputError('INPUT_FORMAT_INVALID', 'Douban raw input must be .json, .jsonl, or .ndjson')


def build_douban_archive(
    raw_items,
    archive_dir,
    *,
    job_id,
    license_status='allowed',
    license_ref='douban-academic-local-archive',
    imported_at=None,
    source_input_path=None,
):
    """Convert raw Douban items into a standard local_envelope archive ready for verify/commit."""
    validate_job_id(job_id)
    if license_status not in ('allowed', 'unknown', 'restricted'):
        raise HubInputError('LICENSE_STATUS_INVALID', 'license_status must be allowed, unknown, or restricted')
    ref_clean = _clean_text(license_ref, max_len=255)
    if not ref_clean:
        raise HubInputError('LICENSE_REF_REQUIRED', 'license_ref is required')

    archive_root = validate_archive_dir(archive_dir, must_exist=False, create=True)
    job_dir = archive_root / job_id
    if job_dir.exists():
        if is_reparse(job_dir) or not job_dir.is_dir():
            raise HubSecurityError('ARCHIVE_REPARSE_POINT', 'job directory is invalid or a symlink/reparse point')
        resolved = job_dir.resolve(strict=True)
        if resolved != job_dir and str(resolved).lower() != str(job_dir).lower():
            raise HubSecurityError('ARCHIVE_REPARSE_POINT', 'job directory resolves through a link')
    else:
        job_dir.mkdir(parents=True, exist_ok=True)

    imported_iso = _utc_now_iso(imported_at)
    movie_records = []
    persons = []
    comments = []
    skipped = 0

    for item in raw_items:
        kind = classify_douban_item(item)
        if kind in ('movie', 'book'):
            movie_records.append(douban_item_to_envelope_record(item, observed_at=imported_iso))
        elif kind == 'person':
            persons.append(_normalize_person(item))
        elif kind == 'comment':
            comments.append(_normalize_comment(item))
        else:
            skipped += 1

    envelope = {
        'metadata': {
            'source': 'legacy_movies',
            'mode': 'AUTHORIZED_LOCAL',
            'license_status': license_status,
            'license_ref': ref_clean,
            'imported_at': imported_iso,
        },
        'records': movie_records,
    }
    env_bytes = (json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    env_path = atomic_write_bytes(job_dir / 'envelope.json', env_bytes)
    env_sha = sha256_bytes(env_bytes)

    supplementary_info = {}
    file_hashes = {'envelope.json': env_sha}

    if persons:
        sup_dir = job_dir / 'supplementary'
        sup_dir.mkdir(parents=True, exist_ok=True)
        lines = (canonical_json(row) for row in persons)
        person_bytes = ('\n'.join(lines) + '\n').encode('utf-8')
        atomic_write_bytes(sup_dir / 'persons.jsonl', person_bytes)
        person_sha = sha256_bytes(person_bytes)
        supplementary_info['persons'] = {
            'path': 'supplementary/persons.jsonl',
            'count': len(persons),
            'sha256': person_sha,
        }
        file_hashes['supplementary/persons.jsonl'] = person_sha

    if comments:
        sup_dir = job_dir / 'supplementary'
        sup_dir.mkdir(parents=True, exist_ok=True)
        lines = (canonical_json(row) for row in comments)
        comment_bytes = ('\n'.join(lines) + '\n').encode('utf-8')
        atomic_write_bytes(sup_dir / 'comments.jsonl', comment_bytes)
        comment_sha = sha256_bytes(comment_bytes)
        supplementary_info['comments'] = {
            'path': 'supplementary/comments.jsonl',
            'count': len(comments),
            'sha256': comment_sha,
        }
        file_hashes['supplementary/comments.jsonl'] = comment_sha

    plan = {
        'contract': HUB_CONTRACT,
        'rule_version': RULE_VERSION,
        'job_id': job_id,
        'source': 'local_envelope',
        'scope': 'envelope',
        'language': 'zh-CN',
        'request_budget': 0,
        'estimated_requests': 0,
        'targets': [{'input_path': str(source_input_path or env_path), 'format': 'douban_archive'}],
        'selection': {'selected_count': 1, 'adapter': DOUBAN_ADAPTER_VERSION},
        'warnings': [],
    }
    plan['plan_digest'] = sha256_json(plan)
    atomic_write_json(job_dir / 'hub-plan.json', plan)

    manifest = {
        'schema': LOCAL_ENVELOPE_MANIFEST_SCHEMA,
        'adapter': DOUBAN_ADAPTER_VERSION,
        'job_id': job_id,
        'created_at': imported_iso,
        'input_path': str(source_input_path) if source_input_path else None,
        'record_count': len(movie_records),
        'envelope_sha256': env_sha,
        'counts': {
            'movies': len(movie_records),
            'persons': len(persons),
            'comments': len(comments),
            'skipped': skipped,
        },
        'supplementary': supplementary_info,
    }
    atomic_write_json(job_dir / 'manifest.json', manifest)
    file_hashes['manifest.json'] = sha256_bytes((job_dir / 'manifest.json').read_bytes())

    return {
        'contract': HUB_CONTRACT,
        'rule_version': RULE_VERSION,
        'adapter': DOUBAN_ADAPTER_VERSION,
        'source': 'local_envelope',
        'job_id': job_id,
        'status': 'ARCHIVED',
        'archive_dir': str(job_dir),
        'envelope_path': str(env_path),
        'manifest_path': str(job_dir / 'manifest.json'),
        'counts': manifest['counts'],
        'supplementary': supplementary_info,
        'file_hashes': file_hashes,
    }


__all__ = [
    'DOUBAN_ADAPTER_VERSION',
    'build_douban_archive',
    'classify_douban_item',
    'douban_item_to_envelope_record',
    'load_douban_raw',
]
