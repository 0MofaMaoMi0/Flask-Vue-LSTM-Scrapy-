"""Read-only administrator API for TMDB supplement statistics.

Three GET endpoints, all of them read-only and bounded:

* ``/api/admin/tmdb-supplement/coverage``  per-topic status counts.
* ``/api/admin/tmdb-supplement/analytics`` bounded aggregate over the loaded
  ``tmdb-supplement-v1`` documents (never echoes a document back).
* ``/api/admin/tmdb-supplement/missing``   the bounded retry/failure queue.

Authorisation mirrors ``admin_api.py`` exactly: the session row must exist and
the user must be listed in ``ADMIN_USERS`` (``MI2_ADMIN_USERS``), otherwise
401 ``AUTHENTICATION_REQUIRED`` / 403 ``FORBIDDEN``.  No endpoint writes, and
nothing here can trigger an upstream refresh.
"""
from flask import Blueprint, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from catalog.tmdb_supplement import repository
from catalog.tmdb_supplement.analytics import (
    AnalyticsInputError,
    DEFAULT_MIN_VOTES,
    IdentityConflictError,
    MAX_COMPARE_COUNTRIES,
    MAX_COUNTRY_LIMIT,
    MAX_COUNTRY_OFFSET,
    MAX_TOP,
    aggregate_movies,
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
        except ValueError:
            return fail('INVALID_INPUT', 400)
        try:
            connection = engine()
            if connection is None:
                raise repository.RepositoryError('no catalog engine')
            with connection.connect() as conn:
                documents = repository.load_documents(
                    conn, limit=repository.MAX_DOCUMENTS)
            data = aggregate_movies(documents, filters=filters,
                                    countries=comparison or None,
                                    min_votes=min_votes)
            data['scope']['repository'] = {
                'documents_loaded': len(documents),
                'cap': repository.MAX_DOCUMENTS,
                # Hitting the cap means the aggregate may cover only that window.
                'truncated': len(documents) >= repository.MAX_DOCUMENTS,
            }
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

    app.register_blueprint(bp)