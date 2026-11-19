"""Read-only administrator API for TMDB supplement statistics.

Five GET endpoints, all of them read-only and bounded:

* ``/api/admin/tmdb-supplement/coverage``  per-topic status counts.
* ``/api/admin/tmdb-supplement/analytics`` bounded aggregate over the loaded
  ``tmdb-supplement-v1`` documents (never echoes a document back).
* ``/api/admin/tmdb-supplement/missing``   the bounded retry/failure queue.
* ``/api/admin/tmdb-supplement/quality``   five-state topic quality, missing
  severity per country/genre/decade and a paginated problem list.
* ``/api/admin/tmdb-supplement/batches``   read-only page over ``ingest_batches``
  plus item/error distributions.

The analytics/quality documents are cached in-process: a cheap watermark
(``tmdb_movie_metadata`` row count + max ``fetched_at``/``updated_at``) plus a
15 second TTL decide when to reload.  A failed load never replaces the previous
snapshot, and concurrent misses share one load.

Authorisation mirrors ``admin_api.py`` exactly: the session row must exist and
the user must be listed in ``ADMIN_USERS`` (``MI2_ADMIN_USERS``), otherwise
401 ``AUTHENTICATION_REQUIRED`` / 403 ``FORBIDDEN``.  No endpoint writes, and
nothing here can trigger an upstream refresh.
"""
from collections import OrderedDict
import threading
import time
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from catalog.tmdb_supplement import repository
from catalog.tmdb_supplement.analytics import (
    AnalyticsInputError,
    DEFAULT_MIN_VOTES,
    DETAIL_MISSING_TOPICS,
    DETAIL_SORT_KEYS,
    IdentityConflictError,
    MAX_COMPARE_COUNTRIES,
    MAX_COUNTRY_LIMIT,
    MAX_COUNTRY_OFFSET,
    MAX_QUALITY_PROBLEM_LIMIT,
    MAX_QUALITY_PROBLEM_OFFSET,
    MAX_QUALITY_TOP,
    MAX_TOP,
    QUALITY_CATEGORIES,
    QUALITY_DENOMINATORS,
    QUALITY_PROBLEM_TOPICS,
    aggregate_movies,
    aggregate_quality,
    prepare_documents,
)


def _session_row():
    """Same session lookup as ``admin_api``; imported lazily for offline tests."""
    from app.auth import session_row
    return session_row


def _user_model():
    """The authenticated ``User`` model used by ``admin_api``'s guard."""
    from app.models import User
    return User

MAX_VOTES = 1000000
MAX_MISSING_OFFSET = 100000
DEFAULT_DETAIL_LIMIT = 20
MAX_DETAIL_LIMIT = 100
MAX_DETAIL_OFFSET = 10000
MIN_YEAR, MAX_YEAR = 1800, 2199
MAX_TEXT_FILTER = 64
MAX_LIST_ITEMS = 16
MAX_INSTANT_LENGTH = 40
DEFAULT_CACHE_TTL = 15.0
DEFAULT_RESULT_LRU_MAXSIZE = 32


def _iso(moment):
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _watermark_key(watermark):
    return (watermark.get('document_count'),
            watermark.get('max_fetched_at'),
            watermark.get('max_updated_at'))


class DocumentCache:
    """Process-local two-tier cache for supplement documents and aggregates.

    Tier 1 caches raw documents + lazy normalized :class:`PreparedDocuments`
    for the current watermark generation; Tier 2 maintains a bounded LRU
    (``maxsize=32``) of aggregate payloads keyed by ``(generation, params)``.
    """

    def __init__(self, ttl=DEFAULT_CACHE_TTL, clock=time.monotonic,
                 lru_maxsize=DEFAULT_RESULT_LRU_MAXSIZE):
        self.ttl = float(ttl)
        self._clock = clock
        self._lock = threading.Lock()
        self._derived_lock = threading.Lock()
        self._documents = None
        self._watermark = None
        self._watermark_report = None
        self._loaded_at = None
        self._loaded_monotonic = None
        self.load_count = 0
        self._prepared = None
        self._prepared_generation = None
        self._lru = OrderedDict()
        self._lru_generation = None
        self._lru_maxsize = max(1, int(lru_maxsize))

    def snapshot(self):
        with self._lock:
            return self._documents, self._report()

    def fetch(self, watermark, loader):
        key = _watermark_key(watermark)
        now = self._clock()
        with self._lock:
            fresh = (self._documents is not None and self._watermark == key
                     and now - self._loaded_monotonic <= self.ttl)
            if fresh:
                return self._documents, self._report()
            documents = loader()
            loaded_now = self._clock()
            self._documents = documents
            self._watermark = key
            self._watermark_report = dict(watermark)
            self._loaded_at = datetime.now(timezone.utc)
            self._loaded_monotonic = loaded_now
            self.load_count += 1
            with self._derived_lock:
                self._prepared = None
                self._prepared_generation = None
                self._lru.clear()
                self._lru_generation = self.load_count
            return documents, self._report(hit=False)

    def prepared_for(self, generation, documents):
        with self._derived_lock:
            if self._prepared is not None and self._prepared_generation == generation:
                return self._prepared
            prepared = prepare_documents(documents)
            if self._lru_generation is None or generation >= self._lru_generation:
                self._prepared = prepared
                self._prepared_generation = generation
            return prepared

    def get_result(self, generation, key):
        with self._derived_lock:
            if self._lru_generation != generation:
                self._lru.clear()
                self._lru_generation = generation
                return None
            if key not in self._lru:
                return None
            self._lru.move_to_end(key)
            return self._lru[key]

    def put_result(self, generation, key, value):
        with self._derived_lock:
            if self._lru_generation is not None and generation < self._lru_generation:
                return
            if self._lru_generation != generation:
                self._lru.clear()
                self._lru_generation = generation
            if key in self._lru:
                self._lru.move_to_end(key)
            self._lru[key] = value
            while len(self._lru) > self._lru_maxsize:
                self._lru.popitem(last=False)

    def _report(self, hit=None):
        if hit is None:
            hit = self._documents is not None
        now = self._clock()
        age = None
        if self._loaded_monotonic is not None:
            age = round(max(0.0, now - self._loaded_monotonic), 3)
        return {
            'hit': bool(hit),
            'status': 'HIT' if hit else 'MISS',
            'loaded_at': _iso(self._loaded_at),
            'watermark': dict(self._watermark_report or {}),
            'ttl_seconds': self.ttl,
            'age_seconds': age,
            'load_count': self.load_count,
            'note': '进程内文档缓存：水位（document_count/max_fetched_at/max_updated_at）'
                    '变化或超过 TTL 才重载；加载失败不覆盖上次快照。',
        }


_DOCUMENT_CACHE = DocumentCache()


def _documents(connection):
    """Load documents through the shared cache; returns ``(documents, report)``."""
    watermark = repository.load_watermark(connection)
    return _DOCUMENT_CACHE.fetch(
        watermark,
        lambda: repository.load_documents(connection, limit=repository.MAX_DOCUMENTS))


def _scoped_coverage(documents, limit, offset):
    """Coverage restricted to one page of documents (``?limit/offset``)."""
    counts = {topic: {status: 0 for status in repository.STATUSES}
              for topic in repository.TOPICS}
    observed_at = None
    for document in documents:
        statuses = document.get('field_status') or {}
        for topic in repository.TOPICS:
            counts[topic][statuses.get(topic, 'NOT_FETCHED')] += 1
        moment = document.get('fetched_at')
        if moment is not None and (observed_at is None or moment > observed_at):
            observed_at = moment
    return {
        'documents': len(documents),
        'topics': counts,
        'statuses': list(repository.STATUSES),
        'observed_at': observed_at,
        'scope': {'limit': limit, 'offset': offset, 'scoped': True},
    }


def install(app):
    bp = Blueprint('admin_tmdb_supplement', __name__,
                   url_prefix='/api/admin/tmdb-supplement')
    admins = {x.strip().lower() for x in app.config.get('ADMIN_USERS', []) if x.strip()}

    @bp.before_request
    def guard():
        session_lookup = _session_row()
        user_model = _user_model()
        with app.extensions['auth_db'].begin() as db:
            row = session_lookup(db)
            if not row or row.user_id is None:
                return jsonify(code='AUTHENTICATION_REQUIRED', data=None), 401
            user = db.get(user_model, row.user_id)
            if not user or user.username_normalized not in admins:
                return jsonify(code='FORBIDDEN', data=None), 403
            g.admin_user = user

    def reply(data):
        return jsonify(code='OK', data=data)

    def fail(code, status):
        return jsonify(code=code, data=None), status

    def engine():
        # The catalog tables live on the M6 engine; admin_api reads them through
        # ``auth_engine`` on today's deployment, so accept either injection.
        return (app.extensions.get('catalog_engine')
                or app.extensions.get('auth_engine'))

    def integer(name, default=None, low=0, high=MAX_VOTES):
        raw = request.args.get(name)
        if raw is None:
            return default
        if not raw or not raw.isascii() or not raw.isdecimal() or len(raw) > 12:
            raise ValueError(name)
        value = int(raw)
        if not low <= value <= high:
            raise ValueError(name)
        return value

    def iso2_codes(name):
        raw = request.args.get(name)
        if raw is None:
            return []
        parts = [part.strip().upper() for part in raw.split(',') if part.strip()]
        if not parts or len(parts) > MAX_COMPARE_COUNTRIES:
            raise ValueError(name)
        for part in parts:
            if len(part) != 2 or not part.isascii() or not part.isalpha():
                raise ValueError(name)
        return sorted(set(parts))

    def text_arg(name):
        raw = request.args.get(name)
        if raw is None:
            return None
        value = raw.strip()
        if not value or len(value) > MAX_TEXT_FILTER:
            raise ValueError(name)
        return value

    def list_arg(name):
        raw = request.args.get(name)
        if raw is None:
            return None
        parts = [part.strip() for part in raw.split(',') if part.strip()]
        if not parts or len(parts) > MAX_LIST_ITEMS:
            raise ValueError(name)
        for part in parts:
            if len(part) > MAX_TEXT_FILTER:
                raise ValueError(name)
        return parts

    def bool_arg(name):
        raw = request.args.get(name)
        if raw is None:
            return None
        value = raw.strip().lower()
        if value in ('1', 'true'):
            return True
        if value in ('0', 'false'):
            return False
        raise ValueError(name)

    def instant_arg(name):
        raw = request.args.get(name)
        if raw is None:
            return None
        value = raw.strip()
        if not value or len(value) > MAX_INSTANT_LENGTH:
            raise ValueError(name)
        try:
            datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError(name) from None
        return value

    def missing_arg():
        raw = request.args.get('missing')
        if raw is None:
            return None
        parts = [part.strip().lower() for part in raw.split(',') if part.strip()]
        if not parts or len(parts) > len(DETAIL_MISSING_TOPICS):
            raise ValueError('missing')
        for part in parts:
            if part not in DETAIL_MISSING_TOPICS:
                raise ValueError('missing')
        return parts

    def sort_arg():
        raw = text_arg('sort')
        if raw is None:
            return None
        value = raw.lower()
        if value not in DETAIL_SORT_KEYS:
            raise ValueError('sort')
        return value

    def order_arg():
        raw = text_arg('order')
        if raw is None:
            return None
        value = raw.lower()
        if value not in ('asc', 'desc'):
            raise ValueError('order')
        return value

    def source_arg():
        raw = request.args.get('source')
        if raw is None:
            return 'tmdb'
        value = raw.strip()
        if not value or len(value) > 32:
            raise ValueError('source')
        if not all(char.isascii() and (char.isalnum() or char in '-_') for char in value):
            raise ValueError('source')
        return value

    @bp.get('/coverage')
    def coverage():
        try:
            limit = integer('limit', None, 1, repository.MAX_DOCUMENTS)
            offset = integer('offset', 0, 0, MAX_DETAIL_OFFSET)
        except ValueError:
            return fail('INVALID_INPUT', 400)
        try:
            connection = engine()
            if connection is None:
                raise repository.RepositoryError('no catalog engine')
            with connection.connect() as conn:
                if limit is None:
                    data = repository.load_coverage(conn)
                    data['scope'] = {'limit': None, 'offset': 0, 'scoped': False}
                else:
                    data = _scoped_coverage(
                        repository.load_documents(conn, limit=limit, offset=offset),
                        limit, offset)
            return reply(data)
        except (repository.RepositoryError, SQLAlchemyError):
            return fail('DATA_UNAVAILABLE', 503)

    @bp.get('/analytics')
    def analytics():
        try:
            min_votes = integer('min_votes', DEFAULT_MIN_VOTES, 0, MAX_VOTES)
            limit = integer('limit', DEFAULT_DETAIL_LIMIT, 1, MAX_DETAIL_LIMIT)
            offset = integer('offset', 0, 0, MAX_DETAIL_OFFSET)
            year_from = integer('year_from', None, MIN_YEAR, MAX_YEAR)
            year_to = integer('year_to', None, MIN_YEAR, MAX_YEAR)
            filters = {'limit': limit, 'offset': offset}
            if year_from is not None:
                filters['year_from'] = year_from
            if year_to is not None:
                filters['year_to'] = year_to
            if year_from is not None and year_to is not None and year_from > year_to:
                raise ValueError('year range')
            country = iso2_codes('country')
            if country:
                filters['countries'] = country
            genre = text_arg('genre')
            if genre is not None:
                filters['genre'] = genre
            genres = list_arg('genres')
            if genres is not None:
                filters['genres'] = genres
            decades = list_arg('decades')
            if decades is not None:
                filters['decades'] = decades
            decade = text_arg('decade')
            if decade is not None:
                filters['decade'] = decade
            observed_from = instant_arg('observed_from')
            if observed_from is not None:
                filters['observed_from'] = observed_from
            observed_to = instant_arg('observed_to')
            if observed_to is not None:
                filters['observed_to'] = observed_to
            country_known = bool_arg('country_known')
            if country_known is not None:
                filters['country_known'] = country_known
            top = integer('top', None, 1, MAX_TOP)
            if top is not None:
                filters['top'] = top
            country_limit = integer('country_limit', None, 1, MAX_COUNTRY_LIMIT)
            if country_limit is not None:
                filters['country_limit'] = country_limit
            country_offset = integer('country_offset', None, 0, MAX_COUNTRY_OFFSET)
            if country_offset is not None:
                filters['country_offset'] = country_offset
            comparison = iso2_codes('countries')
            detail_country = iso2_codes('detail_country')
            if detail_country:
                filters['detail_country'] = detail_country
            keyword = integer('keyword', None, 1, MAX_VOTES)
            if keyword is not None:
                filters['keyword'] = keyword
            company = integer('company', None, 1, MAX_VOTES)
            if company is not None:
                filters['company'] = company
            missing_terms = missing_arg()
            if missing_terms is not None:
                filters['missing'] = missing_terms
            sort = sort_arg()
            if sort is not None:
                filters['sort'] = sort
            order = order_arg()
            if order is not None:
                filters['order'] = order
            overlap = iso2_codes('overlap')
            if overlap:
                filters['overlap'] = overlap
        except ValueError:
            return fail('INVALID_INPUT', 400)
        try:
            connection = engine()
            if connection is None:
                raise repository.RepositoryError('no catalog engine')
            with connection.connect() as conn:
                try:
                    catalog = repository.load_catalog_counts(conn)
                except (repository.RepositoryError, SQLAlchemyError):
                    # Directory counts are a display aid for analytics; a failure
                    # there must not blank the whole resource page.
                    catalog = {
                        'active_movies': None,
                        'tmdb_mapped': None,
                        'unmapped': None,
                        'source': 'movies+tmdb_movie_metadata',
                        'as_of': None,
                        'status': 'UNAVAILABLE',
                        'note': '目录表计数读取失败；本响应不含在架/关联数量，不补 0。',
                    }
                documents, cache = _documents(conn)
            generation = cache['load_count']
            cache_key = (
                'analytics',
                tuple((k, tuple(v) if isinstance(v, list) else v)
                      for k, v in sorted(filters.items())),
                tuple(comparison),
                min_votes,
            )
            base_data = _DOCUMENT_CACHE.get_result(generation, cache_key)
            if base_data is None:
                prepared = _DOCUMENT_CACHE.prepared_for(generation, documents)
                base_data = aggregate_movies(
                    documents, filters=filters,
                    countries=comparison or None,
                    min_votes=min_votes, prepared=prepared)
                base_data['scope']['repository'] = {
                    'documents_loaded': len(documents),
                    'cap': repository.MAX_DOCUMENTS,
                    # Hitting the cap means the aggregate may cover only that window.
                    'truncated': len(documents) >= repository.MAX_DOCUMENTS,
                }
                _DOCUMENT_CACHE.put_result(generation, cache_key, base_data)
            data = dict(base_data)
            scope = dict(base_data['scope'])
            scope['repository'] = dict(base_data['scope']['repository'])
            scope['catalog'] = catalog
            scope['cache'] = cache
            data['scope'] = scope
            return reply(data)
        except IdentityConflictError as exc:
            return jsonify(code='TMDB_IDENTITY_CONFLICT',
                           data={'review_required': True,
                                 'conflicts': exc.conflicts}), 409
        except AnalyticsInputError:
            return fail('INVALID_INPUT', 400)
        except (repository.RepositoryError, SQLAlchemyError):
            return fail('DATA_UNAVAILABLE', 503)

    @bp.get('/missing')
    def missing():
        try:
            limit = integer('limit', repository.DEFAULT_MISSING_ROWS, 1,
                            repository.MAX_MISSING_ROWS)
            offset = integer('offset', 0, 0, MAX_MISSING_OFFSET)
        except ValueError:
            return fail('INVALID_INPUT', 400)
        try:
            connection = engine()
            if connection is None:
                raise repository.RepositoryError('no catalog engine')
            with connection.connect() as conn:
                items = repository.load_missing_queue(conn, limit=limit,
                                                      offset=offset)
            return reply({
                'limit': limit,
                'offset': offset,
                'returned': len(items),
                'has_more': len(items) == limit,
                'items': items,
            })
        except (repository.RepositoryError, SQLAlchemyError):
            return fail('DATA_UNAVAILABLE', 503)

    @bp.get('/quality')
    def quality():
        try:
            denominator = (request.args.get('denominator') or 'active').strip().lower()
            if denominator not in QUALITY_DENOMINATORS:
                raise ValueError('denominator')
            top = integer('top', 10, 1, MAX_QUALITY_TOP)
            limit = integer('limit', DEFAULT_DETAIL_LIMIT, 1, MAX_QUALITY_PROBLEM_LIMIT)
            offset = integer('offset', 0, 0, MAX_QUALITY_PROBLEM_OFFSET)
            topic = text_arg('topic')
            if topic is not None:
                topic = topic.strip().lower()
                if topic not in QUALITY_PROBLEM_TOPICS:
                    raise ValueError('topic')
            category = text_arg('category')
            if category is not None:
                category = category.strip().upper()
                if category not in QUALITY_CATEGORIES:
                    raise ValueError('category')
        except ValueError:
            return fail('INVALID_INPUT', 400)
        try:
            connection = engine()
            if connection is None:
                raise repository.RepositoryError('no catalog engine')
            with connection.connect() as conn:
                catalog = repository.load_catalog_counts(conn)
                unmapped = (repository.load_unmapped_movies(conn)
                            if denominator == 'active' else [])
                documents, cache = _documents(conn)
            generation = cache['load_count']
            unmapped_sig = tuple(
                (item.get('movie_id'), item.get('title'), item.get('release_year'))
                for item in unmapped
            )
            cache_key = (
                'quality', denominator, top, limit, offset, topic, category,
                catalog.get('active_movies'), catalog.get('tmdb_mapped'),
                catalog.get('unmapped'), catalog.get('source'), unmapped_sig,
            )
            base_data = _DOCUMENT_CACHE.get_result(generation, cache_key)
            if base_data is None:
                prepared = _DOCUMENT_CACHE.prepared_for(generation, documents)
                base_data = aggregate_quality(
                    documents, catalog=catalog, denominator=denominator, top=top,
                    problem_limit=limit, problem_offset=offset, topic=topic,
                    category=category, unmapped_movies=unmapped, prepared=prepared)
                base_data['scope'] = {
                    'repository': {
                        'documents_loaded': len(documents),
                        'cap': repository.MAX_DOCUMENTS,
                        'truncated': len(documents) >= repository.MAX_DOCUMENTS,
                    },
                    'unmapped_loaded': len(unmapped),
                    'unmapped_cap': repository.MAX_UNMAPPED_ROWS,
                }
                _DOCUMENT_CACHE.put_result(generation, cache_key, base_data)
            data = dict(base_data)
            data['catalog'] = dict(base_data['catalog'])
            data['catalog']['as_of'] = catalog.get('as_of')
            data['scope'] = dict(base_data['scope'])
            data['scope']['repository'] = dict(base_data['scope']['repository'])
            data['cache'] = cache
            return reply(data)
        except IdentityConflictError as exc:
            return jsonify(code='TMDB_IDENTITY_CONFLICT',
                           data={'review_required': True,
                                 'conflicts': exc.conflicts}), 409
        except AnalyticsInputError:
            return fail('INVALID_INPUT', 400)
        except (repository.RepositoryError, SQLAlchemyError):
            return fail('DATA_UNAVAILABLE', 503)

    @bp.get('/batches')
    def batches():
        try:
            limit = integer('limit', repository.DEFAULT_BATCH_ROWS, 1,
                            repository.MAX_BATCH_ROWS)
            offset = integer('offset', 0, 0, repository.MAX_BATCH_OFFSET)
            status = text_arg('status')
            if status is not None:
                status = status.strip().lower()
                if status not in repository.BATCH_STATUSES:
                    raise ValueError('status')
            source = source_arg()
            started_from = instant_arg('started_from')
            started_to = instant_arg('started_to')
        except ValueError:
            return fail('INVALID_INPUT', 400)
        try:
            connection = engine()
            if connection is None:
                raise repository.RepositoryError('no catalog engine')
            with connection.connect() as conn:
                data = repository.load_batches(
                    conn, limit=limit, offset=offset, status=status,
                    source=source, started_from=started_from,
                    started_to=started_to)
            data['limit'] = limit
            data['offset'] = offset
            data['returned'] = len(data['items'])
            data['has_more'] = offset + len(data['items']) < data['total']
            return reply(data)
        except (repository.RepositoryError, SQLAlchemyError):
            return fail('DATA_UNAVAILABLE', 503)

    app.register_blueprint(bp)