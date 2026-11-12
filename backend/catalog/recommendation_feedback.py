"""Recommendation exposure/click capture and evaluation (Loop D).

Writes live in the activity schema; the frozen catalog schema is untouched.
"""
import json
import math
import re
import uuid
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, request, jsonify, g
from sqlalchemy import (MetaData, Table, Column, BigInteger, Integer, String, DateTime, select, insert, func, delete)
from sqlalchemy.exc import IntegrityError
from app.auth import session_row, csrf_ok
from app.models import User


def tables(schema):
    md = MetaData()
    imp = Table('recommendation_impressions', md,
                Column('impression_id', BigInteger, primary_key=True, autoincrement=True),
                Column('request_id', String(36), nullable=False),
                Column('user_id', BigInteger, nullable=False),
                Column('channel', String(32), nullable=False),
                Column('period', String(16)),
                Column('board_version', String(32), nullable=False),
                Column('context', String(32), nullable=False),
                Column('position', Integer, nullable=False),
                Column('movie_id', BigInteger),
                Column('tmdb_id', BigInteger),
                Column('shown_at', DateTime, nullable=False), schema=schema)
    clicks = Table('recommendation_clicks', md,
                   Column('click_id', BigInteger, primary_key=True, autoincrement=True),
                   Column('request_id', String(36), nullable=False),
                   Column('position', Integer, nullable=False),
                   Column('user_id', BigInteger, nullable=False),
                   Column('channel', String(32), nullable=False),
                   Column('movie_id', BigInteger),
                   Column('tmdb_id', BigInteger),
                   Column('clicked_at', DateTime, nullable=False), schema=schema)
    return imp, clicks


def _uuid(value):
    try:
        uuid.UUID(str(value))
        return str(value)
    except Exception:
        raise ValueError('request_id must be a uuid')


def _int(value, lo, hi):
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError('invalid integer')
    return value


def install(app):
    schema = app.config['M6_ACTIVITY_SCHEMA']
    imp, clicks = tables(schema)
    bp = Blueprint('recommendation_feedback', __name__)
    engine = app.extensions['auth_engine']
    states = app.extensions['m6_behavior'].tables['states']

    def identity(mutate=False):
        with app.extensions['auth_db'].begin() as db:
            row = session_row(db, lock=mutate)
            if not row or row.user_id is None:
                return None
            if mutate and not csrf_ok(row):
                raise PermissionError('CSRF')
            return row.user_id

    def reply(data=None, status=200, code='OK'):
        return jsonify(code=code, data=data), status

    @bp.post('/api/catalog/recommendation-impressions')
    def impressions():
        try:
            user = identity(mutate=True)
            if user is None:
                return reply(status=401, code='AUTHENTICATION_REQUIRED')
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                raise ValueError('JSON required')
            request_id = _uuid(body.get('request_id'))
            channel = str(body.get('channel') or '')[:32]
            context = str(body.get('context') or '')[:32]
            version = str(body.get('board_version') or '')[:32]
            period = body.get('period')
            period = str(period)[:16] if period else None
            items = body.get('items')
            if not channel or not context or not version or not isinstance(items, list) or not 1 <= len(items) <= 50:
                raise ValueError('invalid batch')
            rows, seen = [], set()
            now = datetime.utcnow()
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError('invalid item')
                pos = _int(item.get('position'), 1, 50)
                if pos in seen:
                    raise ValueError('duplicate position')
                seen.add(pos)
                movie_id = item.get('movie_id')
                tmdb_id = item.get('tmdb_id')
                movie_id = _int(movie_id, 1, 2 ** 62) if movie_id is not None else None
                tmdb_id = _int(tmdb_id, 1, 2 ** 62) if tmdb_id is not None else None
                if movie_id is None and tmdb_id is None:
                    raise ValueError('movie_id or tmdb_id required')
                rows.append(dict(request_id=request_id, user_id=user, channel=channel, period=period,
                                 board_version=version, context=context, position=pos,
                                 movie_id=movie_id, tmdb_id=tmdb_id, shown_at=now))
            try:
                with engine.begin() as c:
                    c.execute(insert(imp), rows)
                return reply({'request_id': request_id, 'inserted': len(rows), 'duplicate': False}, 201)
            except IntegrityError:
                return reply({'request_id': request_id, 'inserted': 0, 'duplicate': True})
        except PermissionError:
            return reply(status=403, code='REQUEST_VERIFICATION_FAILED')
        except ValueError:
            return reply(status=400, code='INVALID_INPUT')

    @bp.post('/api/catalog/recommendation-clicks')
    def click():
        try:
            user = identity(mutate=True)
            if user is None:
                return reply(status=401, code='AUTHENTICATION_REQUIRED')
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                raise ValueError('JSON required')
            request_id = _uuid(body.get('request_id'))
            pos = _int(body.get('position'), 1, 50)
            with engine.connect() as c:
                row = c.execute(select(imp.c.channel, imp.c.movie_id, imp.c.tmdb_id).where(
                    imp.c.request_id == request_id, imp.c.position == pos, imp.c.user_id == user)).first()
            if not row:
                return reply(status=404, code='IMPRESSION_NOT_FOUND')
            try:
                with engine.begin() as c:
                    c.execute(insert(clicks).values(request_id=request_id, position=pos, user_id=user, channel=row[0],
                                                    movie_id=row[1], tmdb_id=row[2], clicked_at=datetime.utcnow()))
                return reply({'request_id': request_id, 'position': pos, 'duplicate': False}, 201)
            except IntegrityError:
                return reply({'request_id': request_id, 'position': pos, 'duplicate': True})
        except PermissionError:
            return reply(status=403, code='REQUEST_VERIFICATION_FAILED')
        except ValueError:
            return reply(status=400, code='INVALID_INPUT')

    def admin_required():
        with app.extensions['auth_db'].begin() as db:
            row = session_row(db)
            if not row or row.user_id is None:
                return None, 401
            user = db.get(User, row.user_id)
            admins = {str(x).strip().lower() for x in app.config.get('ADMIN_USERS', []) if str(x).strip()}
            if not user or user.username_normalized not in admins:
                return None, 403
            return user, 200

    @bp.get('/api/admin/recommendation-feedback')
    def feedback():
        user, status = admin_required()
        if user is None:
            return reply(status=status, code='FORBIDDEN' if status == 403 else 'AUTHENTICATION_REQUIRED')
        try:
            days = int(request.args.get('days', '30'))
            if not 1 <= days <= 90:
                raise ValueError()
            k = int(request.args.get('k', '10'))
            if not 1 <= k <= 50:
                raise ValueError()
            end = datetime.utcnow()
            start = end - timedelta(days=days)
            with engine.connect() as c:
                imp_rows = c.execute(select(imp.c.user_id, imp.c.channel, imp.c.movie_id, imp.c.position)
                                     .where(imp.c.shown_at >= start, imp.c.shown_at < end)).all()
                click_rows = c.execute(select(clicks.c.user_id, clicks.c.channel, clicks.c.movie_id)
                                       .where(clicks.c.clicked_at >= start, clicks.c.clicked_at < end)).all()
            imp_by_channel, click_by_channel = {}, {}
            for _, channel, _, _ in imp_rows:
                imp_by_channel[channel] = imp_by_channel.get(channel, 0) + 1
            for _, channel, _ in click_rows:
                click_by_channel[channel] = click_by_channel.get(channel, 0) + 1
            channels = []
            for channel in sorted(set(imp_by_channel) | set(click_by_channel)):
                i = imp_by_channel.get(channel, 0)
                clicks_n = click_by_channel.get(channel, 0)
                channels.append({'channel': channel, 'impressions': i, 'clicks': clicks_n,
                                 'ctr': round(clicks_n / i, 6) if i else None})
            # ranking metrics: board order = best (smallest) position per movie
            by_user = {}
            for uid, channel, movie_id, position in imp_rows:
                if movie_id is None:
                    continue
                key = (uid, channel)
                entry = by_user.setdefault(key, {})
                entry[movie_id] = min(position, entry.get(movie_id, position))
            user_ids = {uid for uid, _ in by_user}
            positive = {}
            if user_ids:
                with engine.connect() as c:
                    for uid, movie_id, state in c.execute(select(states.c.user_id, states.c.movie_id, states.c.state)
                                                          .where(states.c.user_id.in_(user_ids))):
                        s = state or {}
                        if s.get('watched') or s.get('favorite') or (isinstance(s.get('rating'), (int, float)) and s['rating'] >= 4):
                            positive.setdefault(uid, set()).add(movie_id)
            precisions, recalls, ndcgs = [], [], []
            for (uid, _channel), ranking in by_user.items():
                ranked = [mid for mid, _ in sorted(ranking.items(), key=lambda x: (x[1], x[0]))][:k]
                relevant = positive.get(uid, set()) & set(ranking)
                if not relevant:
                    continue
                hits = len([m for m in ranked if m in relevant])
                precisions.append(hits / k)
                recalls.append(hits / len(relevant))
                dcg = sum(1 / math.log2(i + 2) for i, m in enumerate(ranked) if m in relevant)
                idcg = sum(1 / math.log2(i + 2) for i in range(min(len(relevant), k)))
                ndcgs.append(dcg / idcg if idcg else 0)
            ranking = {
                'k': k,
                'users': len(precisions),
                'precision_at_k': round(sum(precisions) / len(precisions), 6) if precisions else None,
                'recall_at_k': round(sum(recalls) / len(recalls), 6) if recalls else None,
                'ndcg_at_k': round(sum(ndcgs) / len(ndcgs), 6) if ndcgs else None,
                'status': 'OK' if precisions else 'INSUFFICIENT_DATA',
                'ground_truth': '本地已看/收藏/评分>=4（评估时点快照）',
            }
            return reply({'window': {'from': start.isoformat() + 'Z', 'to': end.isoformat() + 'Z', 'days': days},
                          'impressions': len(imp_rows), 'clicks': len(click_rows),
                          'ctr': round(len(click_rows) / len(imp_rows), 6) if imp_rows else None,
                          'channels': channels, 'ranking': ranking,
                          'definition_version': 'feedback-v1', 'as_of': end.isoformat() + 'Z'})
        except ValueError:
            return reply(status=400, code='INVALID_INPUT')

    app.register_blueprint(bp)
