"""Read-only administrator aggregates (M9 admin panel).

Access is limited to usernames in MI2_ADMIN_USERS (default: zhang).
No raw event payloads, prompts, keys, or request bodies are returned.
"""
from datetime import datetime, timedelta
from flask import Blueprint, g, jsonify, request
from sqlalchemy import select, func, distinct
from sqlalchemy.exc import SQLAlchemyError
from app.auth import session_row
from app.models import User, Base
from catalog.models import CatalogBase


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
        md = CatalogBase.metadata
        m, ml, g = md.tables['movies'], md.tables['movie_genres'], md.tables['genres']
        ratings, sources = md.tables['movie_ratings_summary'], md.tables['movie_source_records']
        credits, assets = md.tables['movie_credits'], md.tables['movie_assets']
        with engine.connect() as c:
            rows = c.execute(select(m.c.movie_id, m.c.title, m.c.release_year).where(m.c.catalog_status == 'active').order_by(m.c.movie_id)).all()
            genre_map = {}
            for mid, name in c.execute(select(ml.c.movie_id, g.c.name_zh).join(g, g.c.genre_id == ml.c.genre_id)):
                genre_map.setdefault(mid, []).append(name)
            rating_map = dict(c.execute(select(ratings.c.movie_id, func.max(ratings.c.score_value)).group_by(ratings.c.movie_id)).all())
            country_map = {}
            for mid, payload in c.execute(select(sources.c.movie_id, sources.c.metadata_json).where(sources.c.source == 'tmdb')):
                if mid in country_map or not isinstance(payload, dict):
                    continue
                country_map[mid] = [x.get('iso_3166_1') for x in (payload.get('production_countries') or []) if isinstance(x, dict) and x.get('iso_3166_1')]
            credit_map = dict(c.execute(select(credits.c.movie_id, func.count()).group_by(credits.c.movie_id)).all())
            asset_map = dict(c.execute(select(assets.c.movie_id, func.count()).group_by(assets.c.movie_id)).all())
        items = [{
            'movie_id': int(mid), 'title': title, 'year': year,
            'genres': genre_map.get(mid, []), 'rating': float(rating_map[mid]) if rating_map.get(mid) is not None else None,
            'countries': country_map.get(mid, []), 'credits': int(credit_map.get(mid, 0)), 'assets': int(asset_map.get(mid, 0)),
        } for mid, title, year in rows]
        return reply({'items': items})

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

    @bp.get('/events')
    def events_feed():
        events = repo.tables['events']
        m = CatalogBase.metadata.tables['movies']
        with engine.connect() as c:
            rows = c.execute(select(events.c.created_at, User.username, m.c.title, events.c.payload)
                             .join(User, User.id == events.c.user_id)
                             .join(m, m.c.movie_id == events.c.movie_id)
                             .order_by(events.c.seq.desc()).limit(60)).all()
        items = []
        for created, username, title, payload in rows:
            payload = payload if isinstance(payload, dict) else {}
            items.append({'at': created.isoformat() if created else None, 'user': username, 'title': title,
                          'kind': payload.get('kind'), 'action': payload.get('action')})
        return reply({'items': items})

    @bp.get('/health')
    def health():
        with engine.connect() as c:
            now = c.scalar(select(func.now()))
            movies = c.scalar(select(func.count()).select_from(CatalogBase.metadata.tables['movies']))
        m7 = 'm7_engine' in app.extensions
        return reply({'db_time': now.isoformat() if now else None, 'movies': int(movies or 0),
                      'm7_configured': m7, 'admin': g.admin_user.username,
                      'config': {'schema': app.config.get('M6_SCHEMA'), 'activity_schema': app.config.get('M6_ACTIVITY_SCHEMA')}})

    app.register_blueprint(bp)
