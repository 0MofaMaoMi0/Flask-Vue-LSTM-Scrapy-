"""Read-only administrator aggregates (M9 admin panel).

Access is limited to usernames in MI2_ADMIN_USERS (default: zhang).
No raw event payloads, prompts, keys, or request bodies are returned.
"""
from datetime import datetime, timedelta, timezone
from flask import Blueprint, g, jsonify, request
from sqlalchemy import select, func, distinct, inspect
from sqlalchemy.exc import SQLAlchemyError
from app.auth import session_row
from app.models import User, Base
from catalog.models import CatalogBase


CATALOG_CHUNK_SIZE = 200
MAX_CATALOG_LIMIT = 500
MAX_CATALOG_OFFSET = (1 << 63) - 1


def _catalog_page_args(args):
    if any(len(args.getlist(key)) > 1 for key in ('limit', 'offset')):
        raise ValueError('Repeated catalog pagination parameter')

    def integer(key, default, minimum, maximum):
        raw = args.get(key)
        if raw is None:
            return default
        if not raw.isascii() or not raw.isdecimal() or len(raw) > 19:
            raise ValueError(key)
        value = int(raw)
        if not minimum <= value <= maximum:
            raise ValueError(key)
        return value

    default_limit = MAX_CATALOG_LIMIT if 'offset' in args else None
    return (integer('limit', default_limit, 1, MAX_CATALOG_LIMIT),
            integer('offset', 0, 0, MAX_CATALOG_OFFSET))


def _catalog_items(connection, rows, has_countries):
    from admin_dashboard import _movie_countries

    tables = CatalogBase.metadata.tables
    links, genres = tables['movie_genres'], tables['genres']
    ratings = tables['movie_ratings_summary']
    ids = [row.movie_id for row in rows]
    genre_map = {}
    for mid, name in connection.execute(
        select(links.c.movie_id, genres.c.name_zh)
        .join(genres, genres.c.genre_id == links.c.genre_id)
        .where(links.c.movie_id.in_(ids))
        .order_by(links.c.movie_id, links.c.genre_id)
    ):
        genre_map.setdefault(mid, []).append(name)
    rating_map = dict(connection.execute(
        select(ratings.c.movie_id, func.max(ratings.c.score_value))
        .where(ratings.c.movie_id.in_(ids)).group_by(ratings.c.movie_id)
    ).all())
    country_map = _movie_countries(
        connection, ids, has_supplement=has_countries, fallback_sources=('tmdb',),
    )

    def counts(table):
        return dict(connection.execute(
            select(table.c.movie_id, func.count()).where(table.c.movie_id.in_(ids))
            .group_by(table.c.movie_id)
        ).all())

    credit_map = counts(tables['movie_credits'])
    asset_map = counts(tables['movie_assets'])
    return [{
        'movie_id': int(mid), 'title': title, 'year': year,
        'genres': genre_map.get(mid, []),
        'rating': float(rating_map[mid]) if rating_map.get(mid) is not None else None,
        'countries': country_map.get(mid, []),
        'credits': int(credit_map.get(mid, 0)), 'assets': int(asset_map.get(mid, 0)),
    } for mid, title, year in rows]


def install(app):
    bp = Blueprint('admin_api', __name__, url_prefix='/api/admin')
    admins = {x.strip().lower() for x in app.config.get('ADMIN_USERS', []) if x.strip()}
    engine = app.extensions['auth_engine']
    repo = app.extensions['m6_behavior']

    @bp.before_request
    def guard():
        with app.extensions['auth_db'].begin() as db:
            row = session_row(db)
            if not row or row.user_id is None:
                return jsonify(code='AUTHENTICATION_REQUIRED', data=None), 401
            user = db.get(User, row.user_id)
            if not user or user.username_normalized not in admins:
                return jsonify(code='FORBIDDEN', data=None), 403
            g.admin_user = user

    def reply(data):
        return jsonify(code='OK', data=data)

    def window_payload(days, start, end):
        return {'days': days, 'from': start.isoformat() + 'Z',
                'to': end.isoformat() + 'Z', 'timezone': 'UTC'}

    @bp.get('/dashboard')
    def dashboard():
        from admin_dashboard import build_dashboard, parse_days
        try:
            days = parse_days(request.args)
        except ValueError:
            return jsonify(code='INVALID_INPUT', data=None), 400
        try:
            return reply(build_dashboard(app, days))
        except SQLAlchemyError:
            return jsonify(code='DATA_UNAVAILABLE', data=None), 503

    @bp.get('/overview')
    def overview():
        md = CatalogBase.metadata
        movies, genres, external = md.tables['movies'], md.tables['genres'], md.tables['movie_external_ids']
        states, events = repo.tables['states'], repo.tables['events']
        month_ago = datetime.utcnow() - timedelta(days=30)
        with engine.connect() as c:
            active = c.scalar(select(func.count()).select_from(movies).where(movies.c.catalog_status == 'active'))
            genre_count = c.scalar(select(func.count()).select_from(genres))
            tmdb_covered = c.scalar(select(func.count(distinct(external.c.movie_id)))
                                   .select_from(external.join(movies))
                                   .where(external.c.source == 'tmdb', movies.c.catalog_status == 'active'))
            users = c.scalar(select(func.count()).select_from(User).where(User.status == 'active'))
            sessions_t = Base.metadata.tables['auth_sessions']
            sessions = c.scalar(select(func.count()).select_from(sessions_t.join(User))
                                .where(sessions_t.c.user_id.is_not(None), sessions_t.c.revoked_at.is_(None),
                                       sessions_t.c.idle_expires_at > datetime.utcnow(),
                                       sessions_t.c.absolute_expires_at > datetime.utcnow(), User.status == 'active'))
            state_rows = list(c.execute(select(states.c.state)).scalars())
            event_count = c.scalar(select(func.count()).select_from(events).where(events.c.created_at >= month_ago))
        def has(key):
            return sum(1 for s in state_rows if isinstance(s, dict) and s.get(key))
        ai = {'available': False}
        if 'm7_engine' in app.extensions:
            usage = app.extensions['m7_ledger'].md.tables['usage']
            with app.extensions['m7_engine'].connect() as c:
                calls = c.scalar(select(func.count()).select_from(usage).where(usage.c.created_at >= month_ago))
            ai = {'available': True, 'calls_30d': int(calls or 0)}
        return reply({
            'movies_active': int(active or 0), 'genres': int(genre_count or 0),
            'tmdb_covered_movies': int(tmdb_covered or 0), 'users_active': int(users or 0),
            'sessions_active': int(sessions or 0), 'state_rows': len(state_rows),
            'watched': has('watched'), 'favorite': has('favorite'), 'want': has('want'),
            'events_30d': int(event_count or 0), 'ai': ai,
            'as_of': datetime.utcnow().isoformat() + 'Z',
        })

    @bp.get('/ai-usage')
    def ai_usage_report():
        from admin_dashboard import parse_days,window_for
        from analytics.admin_reporting import ai_usage
        try:
            days=parse_days(request.args);start,end=window_for(days)
            data=ai_usage(app,start,end)
            return reply(dict(data,window=dict(days=days,start=start.isoformat()+'Z',end=end.isoformat()+'Z',timezone='UTC')))
        except ValueError:return jsonify(code='INVALID_INPUT',data=None),400
        except SQLAlchemyError:return jsonify(code='DATA_UNAVAILABLE',data=None),503

    @bp.get('/import-audit')
    def import_audit_report():
        from admin_dashboard import window_for
        from analytics.admin_reporting import audit_args,import_audit
        try:
            days,page,size,status=audit_args(request.args);start,end=window_for(days)
            return reply(dict(import_audit(app,start,end,page,size,status),window=dict(days=days,start=start.isoformat()+'Z',end=end.isoformat()+'Z',timezone='UTC')))
        except ValueError:return jsonify(code='INVALID_INPUT',data=None),400
        except (SQLAlchemyError,OSError):return jsonify(code='DATA_UNAVAILABLE',data=None),503

    @bp.get('/catalog')
    def catalog():
        try:
            limit, offset = _catalog_page_args(request.args)
        except ValueError:
            return jsonify(code='INVALID_INPUT', data=None), 400
        movies = CatalogBase.metadata.tables['movies']
        active = movies.c.catalog_status == 'active'
        movie_page = (select(movies.c.movie_id, movies.c.title, movies.c.release_year)
                      .where(active).order_by(movies.c.movie_id))
        items = []
        try:
            catalog_engine = app.extensions.get('catalog_engine') or engine
            with catalog_engine.connect() as connection:
                total = int(connection.scalar(select(func.count()).select_from(movies).where(active)))
                if offset < total:
                    has_countries = inspect(connection).has_table('tmdb_movie_countries')
                    if limit is not None:
                        rows = connection.execute(movie_page.limit(limit).offset(offset)).all()
                        for start in range(0, len(rows), CATALOG_CHUNK_SIZE):
                            items.extend(_catalog_items(connection, rows[start:start + CATALOG_CHUNK_SIZE],
                                                        has_countries))
                    else:
                        statement = movie_page.limit(CATALOG_CHUNK_SIZE)
                        while rows := connection.execute(statement).all():
                            items.extend(_catalog_items(connection, rows, has_countries))
                            if len(rows) < CATALOG_CHUNK_SIZE:
                                break
                            statement = movie_page.where(movies.c.movie_id > rows[-1].movie_id).limit(
                                CATALOG_CHUNK_SIZE)
        except SQLAlchemyError:
            return jsonify(code='DATA_UNAVAILABLE', data=None), 503
        return reply({'items': items, 'total': total, 'limit': limit, 'offset': offset,
                      'returned': len(items), 'has_more': offset + len(items) < total})

    @bp.get('/users')
    def users():
        states, events = repo.tables['states'], repo.tables['events']
        with engine.connect() as c:
            rows = c.execute(select(User.id, User.username, User.status, User.created_at).order_by(User.id).limit(200)).all()
            state_counts = dict(c.execute(select(states.c.user_id, func.count()).group_by(states.c.user_id)).all())
            watched = dict(c.execute(select(states.c.user_id, func.count()).where(func.json_unquote(func.json_extract(states.c.state, '$.watched')) == 'true').group_by(states.c.user_id)).all())  # noqa: E712
            last = dict(c.execute(select(events.c.user_id, func.max(events.c.created_at)).group_by(events.c.user_id)).all())
        items = [{'id': int(uid), 'username': name, 'status': status, 'created_at': created.isoformat() if created else None,
                  'states': int(state_counts.get(uid, 0)), 'watched': int(watched.get(uid, 0)),
                  'last_event': last[uid].isoformat() if last.get(uid) else None} for uid, name, status, created in rows]
        return reply({'items': items})

    @bp.get('/users/analytics')
    def users_analytics_report():
        from admin_dashboard import (parse_kind_days, record_generation, users_analytics,
                                     window_for)
        try:
            days, kind = parse_kind_days(request.args)
            start, end = window_for(days)
            with engine.connect() as connection:
                data = users_analytics(connection, repo, start, end, kind)
            record_generation('users_analytics')
            return reply(dict(data, window=window_payload(days, start, end)))
        except ValueError:
            return jsonify(code='INVALID_INPUT', data=None), 400
        except SQLAlchemyError:
            return jsonify(code='DATA_UNAVAILABLE', data=None), 503

    @bp.get('/interest-match')
    def interest_match_report():
        from admin_dashboard import (feedback_tables, interest_match, parse_dimension_days,
                                     record_generation, window_for)
        try:
            days, dimension = parse_dimension_days(request.args, ('genre', 'decade', 'country'))
            start, end = window_for(days)
            try:
                feedback = feedback_tables(app)
            except ValueError:
                feedback = None
            with engine.connect() as connection:
                data = interest_match(connection, repo, feedback, start, end, dimension)
            record_generation('interest_match')
            return reply(dict(data, window=window_payload(days, start, end)))
        except ValueError:
            return jsonify(code='INVALID_INPUT', data=None), 400
        except SQLAlchemyError:
            return jsonify(code='DATA_UNAVAILABLE', data=None), 503

    @bp.get('/recommendation/analytics')
    def recommendation_analytics_report():
        from admin_dashboard import (feedback_tables, parse_days, recommendation_analytics,
                                     record_generation, window_for)
        try:
            days = parse_days(request.args)
            start, end = window_for(days)
            try:
                feedback = feedback_tables(app)
            except ValueError:
                feedback = None
            with engine.connect() as connection:
                data = recommendation_analytics(connection, feedback, start, end)
            record_generation('recommendation_analytics')
            return reply(dict(data, window=window_payload(days, start, end)))
        except ValueError:
            return jsonify(code='INVALID_INPUT', data=None), 400
        except SQLAlchemyError:
            return jsonify(code='DATA_UNAVAILABLE', data=None), 503

    @bp.get('/ai/analytics')
    def ai_analytics_report():
        from admin_dashboard import ai_analytics, parse_days, record_generation, window_for
        try:
            days = parse_days(request.args)
            start, end = window_for(days)
            data = ai_analytics(app, start, end)
            record_generation('ai_analytics')
            return reply(dict(data, window=window_payload(days, start, end)))
        except ValueError:
            return jsonify(code='INVALID_INPUT', data=None), 400
        except SQLAlchemyError:
            return jsonify(code='DATA_UNAVAILABLE', data=None), 503

    @bp.get('/metrics/definitions')
    def metrics_definitions_report():
        from admin_dashboard import metrics_definitions
        return reply(metrics_definitions())

    @bp.get('/events')
    def events_feed():
        from admin_dashboard import events_page, parse_events_args
        try:
            spec = parse_events_args(request.args)
            with engine.connect() as connection:
                data = events_page(connection, repo, spec)
            return reply(data)
        except ValueError:
            return jsonify(code='INVALID_INPUT', data=None), 400
        except SQLAlchemyError:
            return jsonify(code='DATA_UNAVAILABLE', data=None), 503

    @bp.get('/health')
    def health():
        from admin_dashboard import record_generation, health_report
        try:
            with engine.connect() as c:
                now = c.scalar(select(func.now()))
                movies = c.scalar(select(func.count()).select_from(CatalogBase.metadata.tables['movies']))
            record_generation('health')
            extras = health_report(app)
            if now is not None and hasattr(now, 'isoformat'):
                db_time = now.isoformat()
            else:
                db_time = str(now) if now is not None else None
            # db_time keeps its legacy shape; the UTC companion is always 'Z'-suffixed.
            db_time_utc = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            return reply({'db_time': db_time, 'db_time_utc': db_time_utc,
                          'movies': int(movies or 0),
                          'm7_configured': 'm7_engine' in app.extensions,
                          'admin': g.admin_user.username,
                          'config': {'schema': app.config.get('M6_SCHEMA'),
                                     'activity_schema': app.config.get('M6_ACTIVITY_SCHEMA')},
                          **extras})
        except SQLAlchemyError:
            return jsonify(code='DATA_UNAVAILABLE', data=None), 503

    app.register_blueprint(bp)
