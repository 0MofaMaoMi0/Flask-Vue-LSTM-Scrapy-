"""Offline TMDB movie adapter. No requests, credentials or implicit publication."""
import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlsplit, parse_qs
from .data_pipeline import normalize, digest

LICENSE = 'https://developer.themoviedb.org/docs/faq'
MODE = 'LOCAL_AUTHORIZED_TMDB_NONCOMMERCIAL'


def adapt(raw_bytes, evidence):
    if evidence.get('license_ref') != LICENSE or evidence.get('http_status') != '200' or evidence.get('validated') is not True:
        raise ValueError('SOURCE_PERMISSION_OR_RESPONSE_UNVERIFIED')
    if hashlib.sha256(raw_bytes).hexdigest() != evidence.get('sha256'):
        raise ValueError('RAW_HASH_MISMATCH')
    observed = datetime.fromisoformat(evidence['acquired_utc'])
    if observed.tzinfo is None:
        raise ValueError('SOURCE_TIME_WITHOUT_TIMEZONE')
    raw = json.loads(raw_bytes)
    if not isinstance(raw, dict):
        raise ValueError('JSON_ROOT_NOT_OBJECT')
    mid = raw.get('id')
    if type(mid) is not int or mid <= 0 or type(raw.get('adult')) is not bool:
        raise ValueError('INVALID_MOVIE_ID_OR_TYPE')
    url = urlsplit(evidence['url'])
    # Official movie-details route supplies the movie-vs-TV type evidence.
    if url.scheme != 'https' or url.netloc != 'api.themoviedb.org' or url.path != f'/3/movie/{mid}' or url.fragment or parse_qs(url.query) != {'language': ['zh-CN']}:
        raise ValueError('NOT_APPROVED_MOVIE_DETAILS_ROUTE')
    if 'media_type' in raw and raw['media_type'] != 'movie':
        raise ValueError('NON_MOVIE')
    ids = {'tmdb': str(mid)}
    imdb = raw.get('imdb_id')
    if imdb:
        if not isinstance(imdb, str) or not re.fullmatch(r'tt\d{7,10}', imdb):
            raise ValueError('INVALID_IMDB_ID')
        ids['imdb'] = imdb  # TMDB assertion; not an IMDb API verification.
    ratings = []
    votes = raw.get('vote_count')
    average = raw.get('vote_average')
    if votes is not None and (type(votes) is not int or votes < 0):
        raise ValueError('INVALID_VOTES')
    if average is not None:
        score = Decimal(str(average))
        if type(average) is bool or not score.is_finite() or not 0 <= score <= 10:
            raise ValueError('INVALID_SCORE')
        ratings.append(dict(source='tmdb', value=str(score.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)) if votes else None,
                            scale=10, votes=votes, observed_at=evidence['acquired_utc']))
    genre_rows = raw.get('genres') or []
    if not isinstance(genre_rows, list) or any(type(g) is not dict or type(g.get('id')) is not int or g['id'] <= 0 or not isinstance(g.get('name'), str) for g in genre_rows):
        raise ValueError('INVALID_GENRES')
    original = raw.get('original_title') or None
    record = dict(source_record_id=str(mid), external_ids=ids, title=raw.get('title'), original_title=original,
                  aliases=[original] if original and original != raw.get('title') else [],
                  release_date=raw.get('release_date') or None, runtime_minutes=raw.get('runtime'),
                  language=raw.get('original_language') or None, genres=[g['name'] for g in genre_rows],
                  overview=raw.get('overview') or None, ratings=ratings, media_type='movie')
    row = normalize(record, {'source': 'tmdb', 'license_status': 'allowed'})
    row.update(is_adult=raw['adult'], tmdb_genres=genre_rows, raw_sha256=evidence['sha256'],
               source_url=evidence['url'], observed_at=evidence['acquired_utc'], license_ref=LICENSE,
               poster_path_present=bool(raw.get('poster_path')), raw=raw)
    # Identity signature deliberately excludes changing votes/overview; metadata
    # changes are still quarantined by raw hash, never automatically overwritten.
    row['identity_signature'] = digest({k: row[k] for k in ('title_normalized', 'release_year', 'original_title')})
    return row


def prepare(records):
    """Validate first, then quarantine every conflicting owner before writes."""
    rows, issues = [], []
    for raw, evidence in records:
        try:
            rows.append(adapt(raw, evidence))
        except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
            issues.append({'status': 'rejected', 'reason': str(exc), 'raw_sha256': hashlib.sha256(raw).hexdigest()})
    owners = {}
    source_payloads = {}
    for row in rows:
        source_payloads.setdefault(row['source_record_id'], set()).add(digest(row['raw']))
        for key in row['external_ids'].items():
            # Names/year are not reliable identity evidence. Distinct source
            # records claiming the same external key are all disputed owners.
            owners.setdefault(key, set()).add((row['source'], row['source_record_id']))
    conflicts = {key for key, source_records in owners.items() if len(source_records) > 1}
    clean, seen = [], set()
    for row in sorted(rows, key=lambda r: r['raw_sha256']):
        if len(source_payloads[row['source_record_id']]) > 1 or any(key in conflicts for key in row['external_ids'].items()):
            disputed = [{'source': source, 'external_id': eid, 'claiming_source_records': [{'source': s, 'source_record_id': rid} for s, rid in sorted(owners[(source, eid)])]}
                        for source, eid in sorted(row['external_ids'].items()) if (source, eid) in conflicts]
            issues.append({'status': 'review', 'reason': 'BATCH_EXTERNAL_ID_CONFLICT', 'source': row['source'], 'source_record_id': row['source_record_id'], 'raw_sha256': row['raw_sha256'],
                           'record_payload_conflict': len(source_payloads[row['source_record_id']]) > 1, 'disputed_keys': disputed})
        elif digest(row['raw']) in seen:
            issues.append({'status': 'duplicate', 'reason': 'EQUIVALENT_RAW_REPEAT', 'raw_sha256': row['raw_sha256']})
        else:
            clean.append(row)
            seen.add(digest(row['raw']))
    return clean, issues
