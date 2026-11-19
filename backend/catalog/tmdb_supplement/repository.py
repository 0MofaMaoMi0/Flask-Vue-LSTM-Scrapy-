"""Read-only loading layer for the TMDB supplement tables.

This module never writes, never refreshes upstream data and never opens its own
connection: the caller passes an existing SQLAlchemy ``Connection``.  Every
statement is bounded (``LIMIT``/``OFFSET`` with a hard ceiling) and ordered by
``movie_id`` so the administrator API can neither scan the whole table nor
materialise a raw payload it does not need.

The documents produced by :func:`load_documents` follow the
``tmdb-supplement-v1`` contract consumed by
``catalog.tmdb_supplement.analytics``.  Only the topics whose
``field_status_json`` says ``PRESENT``/``EMPTY`` receive facts; ``FAILED``,
``MISSING`` and ``NOT_FETCHED`` stay out of ``facts`` so a failed attempt can
never look like an empty success.

Credits are the one topic whose detail is not stored in a supplement table: the
schema manifest reuses the existing ``movie_credits`` (+ ``people``) rows and
only records ``credits_at`` in ``tmdb_movie_metadata``.  We therefore read that
topic from the reused table and never invent credit facts.

Base facts are read from the *base* source record (``movie_source_records``
joined through ``source_record_pk``) and reduced to a whitelist of scalar
fields; the rest of the upstream payload is dropped before it can reach a
response.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import bindparam, text

__all__ = [
    'RepositoryError',
    'load_documents',
    'load_coverage',
    'load_missing_queue',
    'load_watermark',
    'load_catalog_counts',
    'load_unmapped_movies',
    'load_batches',
    'SCHEMA_VERSION',
    'TOPICS',
    'STATUSES',
    'SUCCESS_STATUSES',
    'MAX_DOCUMENTS',
    'DEFAULT_DOCUMENTS',
    'MAX_MISSING_ROWS',
    'DEFAULT_MISSING_ROWS',
    'BATCH_STATUSES',
    'DEFAULT_BATCH_ROWS',
    'MAX_BATCH_ROWS',
    'MAX_BATCH_OFFSET',
]

SCHEMA_VERSION = 'tmdb-supplement-v1'
TOPICS = (
    'base', 'countries', 'companies', 'spoken_languages',
    'keywords', 'releases', 'credits', 'metrics',
)
STATUSES = ('NOT_FETCHED', 'MISSING', 'EMPTY', 'PRESENT', 'FAILED')
SUCCESS_STATUSES = frozenset(('PRESENT', 'EMPTY'))
MISSING_STATUSES = ('MISSING', 'NOT_FETCHED', 'FAILED')

DEFAULT_DOCUMENTS = 2000
MAX_DOCUMENTS = 5000
MAX_OFFSET = 1000000
DEFAULT_MISSING_ROWS = 200
MAX_MISSING_ROWS = 500
DEFAULT_BATCH_ROWS = 20
MAX_BATCH_ROWS = 100
MAX_BATCH_OFFSET = 100000
BATCH_STATUSES = ('pending', 'running', 'succeeded', 'partial', 'failed')
MAX_UNMAPPED_ROWS = 5000

# Base facts are copied field by field: the stored payload is never forwarded.
BASE_FIELDS = (
    'title', 'original_title', 'release_date', 'release_year',
    'original_language', 'runtime', 'runtime_minutes', 'overview',
    'poster_path', 'backdrop_path', 'budget', 'revenue',
)


class RepositoryError(Exception):
    """Raised when the read layer is asked for something unsafe or invalid."""


# ------------------------------------------------------------------ helpers

def _clamp(value, default, maximum, minimum=1):
    if value is None:
        return default
    if type(value) is not int or type(value) is bool:
        raise RepositoryError('limit must be an integer')
    return max(minimum, min(int(value), maximum))


def _offset(value):
    if type(value) is not int or type(value) is bool or value < 0:
        raise RepositoryError('offset must be a non-negative integer')
    return min(int(value), MAX_OFFSET)


def _movie_ids(values):
    if values is None:
        return None
    try:
        ids = [int(item) for item in values]
    except (TypeError, ValueError):
        raise RepositoryError('movie_ids must contain integers') from None
    if not ids:
        return []
    if len(ids) > MAX_DOCUMENTS:
        raise RepositoryError('too many movie_ids')
    return sorted(set(ids))


def _query(connection, sql, movie_ids=None, params=None):
    """Run one bounded statement; ``movie_id IN (...)`` uses a bound parameter."""
    statement = text(sql)
    values = dict(params or {})
    if movie_ids is not None:
        statement = statement.bindparams(bindparam('movie_ids', expanding=True))
        values['movie_ids'] = movie_ids
    return connection.execute(statement, values).mappings().all()


def _in(table_alias):
    return ' WHERE %s.movie_id IN :movie_ids' % table_alias


def _json_mapping(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            raise RepositoryError('invalid JSON column') from None
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _status(value):
    if not isinstance(value, str):
        return 'NOT_FETCHED'
    candidate = value.strip().upper()
    return candidate if candidate in STATUSES else 'NOT_FETCHED'


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            moment = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            return raw
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    return None


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _non_negative_int(value):
    """A real 0 stays 0 (``vote_count=0`` is a valid observation)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _base_facts(payload):
    if not isinstance(payload, dict):
        return {}
    facts = {}
    for field in BASE_FIELDS:
        if field in payload:
            facts[field] = payload[field]
    genres = []
    for item in payload.get('genres') or []:
        if isinstance(item, dict) and isinstance(item.get('id'), int):
            genres.append({'id': item['id'], 'name': item.get('name')})
        elif isinstance(item, int):
            genres.append({'id': item, 'name': None})
    if 'genres' in payload:
        facts['genres'] = genres
    return facts


# ------------------------------------------------------------------ queries

DOCUMENTS_SQL = """
SELECT m.movie_id AS movie_id,
       m.tmdb_id AS tmdb_id,
       m.payload_sha256 AS payload_sha256,
       m.fetched_at AS fetched_at,
       m.field_status_json AS field_status_json,
       sr.metadata_json AS metadata_json
FROM tmdb_movie_metadata AS m
JOIN movies AS mv ON mv.movie_id = m.movie_id
LEFT JOIN movie_source_records AS sr ON sr.id = m.source_record_pk
{where}
ORDER BY m.movie_id
LIMIT :limit OFFSET :offset
"""

COUNTRIES_SQL = """
SELECT movie_id, country_iso2, country_name
FROM tmdb_movie_countries{c_in}
ORDER BY movie_id, country_iso2
"""

COMPANIES_SQL = """
SELECT c.movie_id AS movie_id,
       co.tmdb_company_id AS tmdb_company_id,
       co.name AS name,
       co.origin_country AS origin_country,
       co.logo_path AS logo_path
FROM tmdb_movie_companies AS c
JOIN tmdb_companies AS co ON co.company_id = c.company_id{c_in}
ORDER BY c.movie_id, co.tmdb_company_id
"""

LANGUAGES_SQL = """
SELECT movie_id, language_code, english_name, name
FROM tmdb_movie_languages{c_in}
ORDER BY movie_id, language_code
"""

KEYWORDS_SQL = """
SELECT k.movie_id AS movie_id,
       kw.tmdb_keyword_id AS tmdb_keyword_id,
       kw.name AS name
FROM tmdb_movie_keywords AS k
JOIN tmdb_keywords AS kw ON kw.keyword_id = k.keyword_id{c_in}
ORDER BY k.movie_id, kw.tmdb_keyword_id
"""

RELEASES_SQL = """
SELECT movie_id, country_iso2, release_type, release_date,
       certification, note, certification_language
FROM tmdb_release_records{c_in}
ORDER BY movie_id, country_iso2, release_date
"""

CREDITS_SQL = """
SELECT c.movie_id AS movie_id,
       c.person_id AS person_id,
       c.role_type AS role_type,
       c.job AS job,
       c.character_name AS character_name,
       c.sort_order AS sort_order,
       p.name AS person_name
FROM movie_credits AS c
LEFT JOIN people AS p ON p.person_id = c.person_id{c_in}
ORDER BY c.movie_id, c.role_type, c.sort_order
"""

METRICS_SQL = """
SELECT o.movie_id AS movie_id,
       o.observed_at AS observed_at,
       o.vote_average AS vote_average,
       o.vote_count AS vote_count,
       o.popularity AS popularity
FROM tmdb_metric_observations AS o
JOIN (SELECT movie_id, MAX(observed_at) AS observed_at
      FROM tmdb_metric_observations{c_in}
      GROUP BY movie_id) AS latest
  ON latest.movie_id = o.movie_id AND latest.observed_at = o.observed_at
ORDER BY o.movie_id
"""

STATUS_EXPR = ("COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT("
               "m.field_status_json, CONCAT('$.\"', j.topic, '\"'))), 'null'),"
               " 'NOT_FETCHED')")

COVERAGE_SQL = """
SELECT j.topic AS topic,
       {status} AS status,
       COUNT(*) AS count,
       (SELECT MAX(m2.fetched_at)
        FROM tmdb_movie_metadata AS m2
        JOIN movies AS mv2 ON mv2.movie_id = m2.movie_id
        WHERE mv2.catalog_status = 'active') AS observed_at
FROM tmdb_movie_metadata AS m
JOIN movies AS mv ON mv.movie_id = m.movie_id
JOIN JSON_TABLE(JSON_KEYS(m.field_status_json),
                '$[*]' COLUMNS (topic VARCHAR(64) PATH '$')) AS j
WHERE mv.catalog_status = 'active'
GROUP BY j.topic, status
""".format(status=STATUS_EXPR)

MISSING_SQL = """
SELECT m.movie_id AS movie_id,
       m.tmdb_id AS tmdb_id,
       j.topic AS topic,
       {status} AS status,
       JSON_UNQUOTE(JSON_EXTRACT(
           m.last_attempt_json, CONCAT('$.\"', j.topic, '\".attempt_at'))) AS attempt_at_json,
       m.fetched_at AS fetched_at,
       NULLIF(JSON_UNQUOTE(JSON_EXTRACT(
           m.last_attempt_json, CONCAT('$.\"', j.topic, '\".error_code'))),
           'null') AS error_code
FROM tmdb_movie_metadata AS m
JOIN movies AS mv ON mv.movie_id = m.movie_id
JOIN JSON_TABLE(JSON_KEYS(m.field_status_json),
                '$[*]' COLUMNS (topic VARCHAR(64) PATH '$')) AS j
WHERE mv.catalog_status = 'active' AND {status} IN ({missing})
ORDER BY m.movie_id, j.topic
LIMIT :limit OFFSET :offset
""".format(status=STATUS_EXPR,
           missing=', '.join("'%s'" % status for status in MISSING_STATUSES))

# One cheap statement: the analytics document cache reloads only when this
# watermark changes (row count + the two high-water timestamps).
WATERMARK_SQL = """
SELECT COUNT(*) AS document_count,
       MAX(m.fetched_at) AS max_fetched_at,
       MAX(m.updated_at) AS max_updated_at
FROM tmdb_movie_metadata AS m
JOIN movies AS mv ON mv.movie_id = m.movie_id
WHERE mv.catalog_status = 'active'
"""

CATALOG_COUNTS_SQL = """
SELECT
  (SELECT COUNT(*) FROM movies WHERE catalog_status = 'active') AS active_movies,
  (SELECT COUNT(*) FROM tmdb_movie_metadata AS m
     JOIN movies AS mv ON mv.movie_id = m.movie_id
     WHERE mv.catalog_status = 'active') AS tmdb_mapped
"""

UNMAPPED_SQL = """
SELECT mv.movie_id AS movie_id,
       mv.title AS title,
       mv.release_year AS release_year
FROM movies AS mv
LEFT JOIN tmdb_movie_metadata AS m ON m.movie_id = mv.movie_id
WHERE mv.catalog_status = 'active' AND m.movie_id IS NULL
ORDER BY mv.movie_id
LIMIT :limit
"""

BATCHES_SQL = """
SELECT batch_id, source, mode, status, started_at, finished_at,
       source_snapshot_ref, counts_json, error_summary
FROM ingest_batches
WHERE source = :source{extra}
ORDER BY started_at DESC, batch_id DESC
LIMIT :limit OFFSET :offset
"""

BATCHES_COUNT_SQL = """
SELECT COUNT(*) AS total
FROM ingest_batches
WHERE source = :source{extra}
"""

BATCH_ITEM_STATUS_SQL = """
SELECT batch_id, status, COUNT(*) AS count
FROM ingest_items
WHERE batch_id IN :batch_ids
GROUP BY batch_id, status
"""

BATCH_ERRORS_SQL = """
SELECT batch_id, error_code, COUNT(*) AS count
FROM ingest_items
WHERE batch_id IN :batch_ids AND error_code IS NOT NULL
GROUP BY batch_id, error_code
ORDER BY batch_id, count DESC, error_code
"""


# ------------------------------------------------------------------ loaders

def load_documents(connection, *, movie_ids=None, limit=DEFAULT_DOCUMENTS,
                   offset=0):
    """Materialise ``tmdb-supplement-v1`` documents for at most ``limit`` movies.

    ``limit`` is clamped to :data:`MAX_DOCUMENTS`; pagination is stable because
    the statement orders by ``movie_id``.  Facts exist only for topics whose
    status is ``PRESENT``/``EMPTY``; every other topic keeps an empty ``facts``
    entry so the analytics kernel can never mistake a failure for success.
    """
    limit = _clamp(limit, DEFAULT_DOCUMENTS, MAX_DOCUMENTS)
    offset = _offset(offset)
    ids = _movie_ids(movie_ids)
    if ids == []:
        return []

    where = ("WHERE mv.catalog_status = 'active' AND m.movie_id IN :movie_ids"
             if ids is not None else "WHERE mv.catalog_status = 'active'")
    rows = _query(connection, DOCUMENTS_SQL.format(where=where), ids,
                  {'limit': limit, 'offset': offset})
    if not rows:
        return []
    loaded_ids = [int(row['movie_id']) for row in rows]

    countries = _group(_query(connection, COUNTRIES_SQL.format(
        c_in=_in('tmdb_movie_countries')), loaded_ids), 'movie_id')
    companies = _group(_query(connection, COMPANIES_SQL.format(
        c_in=_in('c')), loaded_ids), 'movie_id')
    languages = _group(_query(connection, LANGUAGES_SQL.format(
        c_in=_in('tmdb_movie_languages')), loaded_ids), 'movie_id')
    keywords = _group(_query(connection, KEYWORDS_SQL.format(
        c_in=_in('k')), loaded_ids), 'movie_id')
    releases = _group(_query(connection, RELEASES_SQL.format(
        c_in=_in('tmdb_release_records')), loaded_ids), 'movie_id')
    credits = _group(_query(connection, CREDITS_SQL.format(
        c_in=_in('c')), loaded_ids), 'movie_id')
    metrics = _group(_query(connection, METRICS_SQL.format(
        c_in=_in('tmdb_metric_observations')), loaded_ids), 'movie_id')

    documents = []
    for row in rows:
        movie_id = int(row['movie_id'])
        documents.append(_document(
            row,
            countries.get(movie_id, []),
            companies.get(movie_id, []),
            languages.get(movie_id, []),
            keywords.get(movie_id, []),
            releases.get(movie_id, []),
            credits.get(movie_id, []),
            metrics.get(movie_id, []),
        ))
    return documents


def _group(rows, key):
    grouped = {}
    for row in rows:
        grouped.setdefault(int(row[key]), []).append(row)
    return grouped


def _document(row, countries, companies, languages, keywords, releases,
              credits, metrics):
    """Build one v1 document; ``EMPTY`` is an empty success, never leftover rows.

    A topic marked ``EMPTY`` means the upstream answered successfully with an
    empty set, so stray relation rows (a rolled-back batch, a manual fix) must
    not resurrect as facts.  ``FAILED``/``MISSING``/``NOT_FETCHED`` simply keep
    the topic out of ``facts``.
    """
    status_map = _json_mapping(row.get('field_status_json'))
    statuses = {topic: _status(status_map.get(topic)) for topic in TOPICS}

    def present(topic):
        return statuses[topic] == 'PRESENT'

    def empty(topic):
        return statuses[topic] == 'EMPTY'

    facts = {}
    if present('base'):
        facts['base'] = _base_facts(_json_mapping(row.get('metadata_json')))
    elif empty('base'):
        facts['base'] = {}
    if present('countries'):
        facts['countries'] = [
            {'iso_3166_1': item['country_iso2'],
             'name': item.get('country_name')}
            for item in countries
        ]
    elif empty('countries'):
        facts['countries'] = []
    if present('companies'):
        facts['companies'] = [
            {'company_id': _integer(item.get('tmdb_company_id')),
             'name': item.get('name'),
             'origin_country': item.get('origin_country'),
             'logo_path': item.get('logo_path')}
            for item in companies
            if _integer(item.get('tmdb_company_id')) is not None
        ]
    elif empty('companies'):
        facts['companies'] = []
    if present('spoken_languages'):
        facts['spoken_languages'] = [
            {'iso_639_1': item.get('language_code'),
             'english_name': item.get('english_name'),
             'name': item.get('name')}
            for item in languages
            if item.get('language_code')
        ]
    elif empty('spoken_languages'):
        facts['spoken_languages'] = []
    if present('keywords'):
        facts['keywords'] = [
            {'keyword_id': _integer(item.get('tmdb_keyword_id')),
             'name': item.get('name')}
            for item in keywords
            if _integer(item.get('tmdb_keyword_id')) is not None
        ]
    elif empty('keywords'):
        facts['keywords'] = []
    if present('releases'):
        facts['releases'] = [
            {'iso_3166_1': item['country_iso2'],
             'release_date': _iso(item.get('release_date')),
             'type': item.get('release_type'),
             'certification': item.get('certification'),
             'note': item.get('note'),
             'iso_639_1': item.get('certification_language')}
            for item in releases
            if item.get('release_date') is not None
        ]
    elif empty('releases'):
        facts['releases'] = []
    if present('credits'):
        facts['credits'] = [
            {'role_type': item.get('role_type'),
             'person_id': _integer(item.get('person_id')),
             'person_name': item.get('person_name'),
             'character': item.get('character_name'),
             'job': item.get('job'),
             'sort_order': item.get('sort_order')}
            for item in credits
            if _integer(item.get('person_id')) is not None
        ]
    elif empty('credits'):
        facts['credits'] = []
    if present('metrics'):
        latest = metrics[-1] if metrics else None
        facts['metrics'] = {} if latest is None else {
            'vote_average': _number(latest.get('vote_average')),
            'vote_count': _non_negative_int(latest.get('vote_count')),
            'popularity': _number(latest.get('popularity')),
        }
    elif empty('metrics'):
        facts['metrics'] = {}
    return {
        'schema_version': SCHEMA_VERSION,
        'movie_id': int(row['movie_id']),
        'tmdb_id': _integer(row.get('tmdb_id')),
        'fetched_at': _iso(row.get('fetched_at')),
        'payload_sha256': row.get('payload_sha256'),
        'field_status': statuses,
        'facts': facts,
    }


def load_coverage(connection):
    """Per-topic status counts plus the document total, from one grouped query.

    A movie that never received a status key contributes nothing; missing keys
    are reported as ``NOT_FETCHED`` by the same expression the analytics kernel
    uses, so the two never disagree.
    """
    rows = connection.execute(text(COVERAGE_SQL), {}).mappings().all()
    counts = {topic: {status: 0 for status in STATUSES} for topic in TOPICS}
    observed_at = None
    for row in rows:
        topic = row.get('topic')
        status = _status(row.get('status'))
        if topic not in counts:
            continue
        try:
            count = int(row.get('count') or 0)
        except (TypeError, ValueError):
            continue
        counts[topic][status] += count
        moment = _iso(row.get('observed_at'))
        if moment is not None and (observed_at is None or moment > observed_at):
            observed_at = moment
    total = sum(counts['base'].values())
    if not total:
        total = max((sum(per_status.values()) for per_status in counts.values()),
                    default=0)
    return {
        'documents': total,
        'topics': counts,
        'statuses': list(STATUSES),
        'observed_at': observed_at,
    }


def load_missing_queue(connection, *, limit=DEFAULT_MISSING_ROWS, offset=0):
    """Bounded queue of topics that still need a successful fetch.

    Only fields an operator needs to act on are returned: identity, topic,
    status, last attempt time and the stable error code.  No payload, source
    record body or token is ever selected.
    """
    limit = _clamp(limit, DEFAULT_MISSING_ROWS, MAX_MISSING_ROWS)
    offset = _offset(offset)
    rows = connection.execute(text(MISSING_SQL),
                              {'limit': limit, 'offset': offset}).mappings().all()
    items = []
    for row in rows:
        attempt = _iso(row.get('attempt_at_json'))
        if attempt is None:
            attempt = _iso(row.get('fetched_at'))
        items.append({
            'movie_id': int(row['movie_id']),
            'tmdb_id': _integer(row.get('tmdb_id')),
            'topic': row.get('topic'),
            'status': _status(row.get('status')),
            'attempt_at': attempt,
            'error_code': row.get('error_code') or None,
        })
    return items


# ------------------------------------------------- cache watermark / catalog

def load_watermark(connection):
    """Cheap change detector for the document cache (one aggregate row).

    The watermark is ``(document_count, max(fetched_at), max(updated_at))``:
    inserting, deleting or re-fetching one movie changes it, while reading the
    catalog does not.  It is deliberately not the data itself.
    """
    rows = connection.execute(text(WATERMARK_SQL), {}).mappings().all()
    row = rows[0] if rows else {}
    count = _non_negative_int(row.get('document_count'))
    if count is None:
        raise RepositoryError('watermark unavailable')
    return {
        'document_count': count,
        'max_fetched_at': _iso(row.get('max_fetched_at')),
        'max_updated_at': _iso(row.get('max_updated_at')),
    }


def load_catalog_counts(connection):
    """Active / TMDB-mapped / unmapped counts from the sealed catalog tables.

    Read-only and aggregated: no movie row leaves the database.  ``unmapped``
    is derived as ``active - mapped`` because a mapped movie outside the active
    scope is not part of the dashboard denominator.
    """
    rows = connection.execute(text(CATALOG_COUNTS_SQL), {}).mappings().all()
    row = rows[0] if rows else {}
    active = _non_negative_int(row.get('active_movies'))
    mapped = _non_negative_int(row.get('tmdb_mapped'))
    if active is None or mapped is None:
        raise RepositoryError('catalog counts unavailable')
    mapped = min(mapped, active)
    return {
        'active_movies': active,
        'tmdb_mapped': mapped,
        'unmapped': active - mapped,
        'source': 'movies+tmdb_movie_metadata',
        'as_of': _iso(datetime.now(timezone.utc)),
        'note': '在架/已关联计数为目录表只读聚合；无独立数据版本号，as_of 为查询时刻。',
    }


def load_unmapped_movies(connection, *, limit=MAX_DOCUMENTS):
    """Bounded list of active movies without a TMDB supplement row.

    Only identity + display fields are selected, so the quality problem list can
    name the 114 unmapped movies without reading any payload.
    """
    limit = _clamp(limit, MAX_UNMAPPED_ROWS, MAX_UNMAPPED_ROWS)
    rows = connection.execute(text(UNMAPPED_SQL),
                              {'limit': limit}).mappings().all()
    return [{
        'movie_id': int(row['movie_id']),
        'title': row.get('title'),
        'release_year': _non_negative_int(row.get('release_year')),
    } for row in rows]


# --------------------------------------------------------- ingest batches

def _safe_json(value):
    """Parse a JSON column without turning one bad row into a 503 for the page."""
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _sql_instant(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RepositoryError('instant must be an ISO-8601 string')
    raw = value.strip()
    try:
        moment = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        raise RepositoryError('invalid instant') from None
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def _query_in(connection, sql, name, values, params=None):
    statement = text(sql).bindparams(bindparam(name, expanding=True))
    payload = dict(params or {})
    payload[name] = list(values)
    return connection.execute(statement, payload).mappings().all()


def load_batches(connection, *, limit=DEFAULT_BATCH_ROWS, offset=0, status=None,
                 source='tmdb', started_from=None, started_to=None):
    """Read-only page over ``ingest_batches`` plus item/error distributions.

    ``ingest_batches`` has no per-batch target/success/failure columns; those
    numbers exist only inside ``counts_json`` for the supplement writer, so a
    missing key is returned as ``null`` with the field named in
    ``missing_fields`` instead of being inferred.  Item status and error codes
    come from ``ingest_items`` and are therefore always a real count when rows
    exist.
    """
    limit = _clamp(limit, DEFAULT_BATCH_ROWS, MAX_BATCH_ROWS)
    if type(offset) is not int or isinstance(offset, bool) \
            or not 0 <= offset <= MAX_BATCH_OFFSET:
        raise RepositoryError('invalid batch offset')
    if status is not None and status not in BATCH_STATUSES:
        raise RepositoryError('invalid batch status')
    if not isinstance(source, str) or not source.strip() or len(source) > 32:
        raise RepositoryError('invalid batch source')

    conditions, params = [], {'source': source.strip()}
    if status is not None:
        conditions.append('status = :status')
        params['status'] = status
    start = _sql_instant(started_from)
    if start is not None:
        conditions.append('started_at >= :started_from')
        params['started_from'] = start
    end = _sql_instant(started_to)
    if end is not None:
        conditions.append('started_at <= :started_to')
        params['started_to'] = end
    extra = ''.join(' AND ' + item for item in conditions)

    count_rows = connection.execute(
        text(BATCHES_COUNT_SQL.format(extra=extra)), params).mappings().all()
    total = _non_negative_int((count_rows[0] if count_rows else {}).get('total'))
    if total is None:
        raise RepositoryError('batch count unavailable')

    rows = connection.execute(
        text(BATCHES_SQL.format(extra=extra)),
        {**params, 'limit': limit, 'offset': offset}).mappings().all()
    if not rows:
        return {'total': total, 'items': [], 'statuses': list(BATCH_STATUSES),
                'source': params['source']}

    batch_ids = [int(row['batch_id']) for row in rows]
    item_rows = _query_in(connection, BATCH_ITEM_STATUS_SQL, 'batch_ids', batch_ids)
    error_rows = _query_in(connection, BATCH_ERRORS_SQL, 'batch_ids', batch_ids)

    items_by_batch = {}
    for row in item_rows:
        batch_id = int(row['batch_id'])
        status_name = row.get('status')
        count = _non_negative_int(row.get('count')) or 0
        items_by_batch.setdefault(batch_id, {'counts': {}, 'total': 0})
        items_by_batch[batch_id]['counts'][status_name] = count
        items_by_batch[batch_id]['total'] += count

    errors_by_batch = {}
    for row in error_rows:
        batch_id = int(row['batch_id'])
        code = row.get('error_code')
        count = _non_negative_int(row.get('count')) or 0
        errors_by_batch.setdefault(batch_id, []).append(
            {'error_code': code, 'count': count})

    items = []
    for row in rows:
        batch_id = int(row['batch_id'])
        counts = _safe_json(row.get('counts_json')) or {}
        missing_fields = []
        reported = {}
        for output_name, source_key in (
                ('target_documents', 'documents'),
                ('success_count', 'success'),
                ('failed_count', 'failed'),
                ('skipped_count', 'skipped')):
            value = _non_negative_int(counts.get(source_key))
            reported[output_name] = value
            if value is None:
                missing_fields.append(output_name)
        reported['executed_operations'] = _non_negative_int(
            counts.get('executed_operations'))
        if reported['executed_operations'] is None:
            missing_fields.append('executed_operations')

        distribution = items_by_batch.get(batch_id)
        item_status = None
        if distribution is not None:
            statuses = {name: distribution['counts'].get(name, 0)
                        for name in ('inserted', 'updated', 'skipped', 'review', 'error')}
            statuses['total'] = distribution['total']
            item_status = statuses
        else:
            missing_fields.append('item_status')

        error_codes = (errors_by_batch.get(batch_id, [])
                       if distribution is not None else None)

        items.append({
            'batch_id': batch_id,
            'source': row.get('source'),
            'mode': row.get('mode'),
            'status': row.get('status'),
            'started_at': _iso(row.get('started_at')),
            'finished_at': _iso(row.get('finished_at')),
            'source_snapshot_ref': row.get('source_snapshot_ref'),
            'reported_counts': reported,
            'counts_source': 'ingest_batches.counts_json',
            'item_status': item_status,
            'error_codes': error_codes,
            'error_summary': row.get('error_summary') or None,
            'missing_fields': missing_fields,
            'note': 'reported_counts 只取自 counts_json，缺键即 null；item_status/error_codes '
                    '来自 ingest_items 实际行，无明细行时为 null，不补 0。',
        })
    return {'total': total, 'items': items, 'statuses': list(BATCH_STATUSES),
            'source': params['source']}