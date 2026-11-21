"""Recommendation exposure/click capture and evaluation (Loop D).

Writes live in the activity schema; the frozen catalog schema is untouched.
"""
import math
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify
from sqlalchemy import (MetaData, Table, Column, BigInteger, Integer, String, DateTime,
                        and_, distinct, select, insert, func)
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
                imp_rows = c.execute(select(imp.c.user_id, imp.c.channel, imp.c.movie_id,
                    imp.c.position, imp.c.request_id, imp.c.period, imp.c.board_version,
                    imp.c.context, imp.c.shown_at).where(imp.c.shown_at >= start,
                    imp.c.shown_at < end, imp.c.board_version == 'visible-v2')).all()
                click_rows = c.execute(select(clicks.c.user_id, clicks.c.request_id,
                    clicks.c.position, clicks.c.clicked_at).where(clicks.c.clicked_at >= start,
                    clicks.c.clicked_at < end)).all()
                user_ids = {r[0] for r in imp_rows}
                state_rows = c.execute(select(states.c.user_id, states.c.movie_id,
                    states.c.state).where(states.c.user_id.in_(user_ids))).all() if user_ids else []
            result = evaluate(imp_rows, click_rows, state_rows, k)
            return reply(dict(result, window={'from': start.isoformat() + 'Z',
                'to': end.isoformat() + 'Z', 'days': days},
                definition_version='feedback-v2-visible-cohort', as_of=end.isoformat() + 'Z'))
        except ValueError:
            return reply(status=400, code='INVALID_INPUT')

    app.register_blueprint(bp)


def evaluate(impressions, clicks, states, k):
    """Visible exposure cohort; ranking per real request, descriptive state overlap.

    These metrics are not causal attribution: the existing state table has no
    historic event snapshot. Zero-hit requests remain in the denominator.
    """
    cohort = {(r[0], r[4], r[3]): r for r in impressions}
    clicked = {tuple(r[:3]) for r in clicks if tuple(r[:3]) in cohort
               and r[3] >= cohort[tuple(r[:3])][8]}
    counts, boards, positive = {}, {}, {}
    for uid, mid, state in states:
        state = state or {}
        if state.get('watched') or state.get('favorite') or (
            type(state.get('rating')) in (int, float) and state['rating'] >= 4):
            positive.setdefault(uid, set()).add(mid)
    for key, r in cohort.items():
        uid, channel, mid, position, req, period, version, context, _ = r
        c = counts.setdefault(channel, [0, 0]); c[0] += 1; c[1] += key in clicked
        if mid is not None:
            boards.setdefault((uid, req, channel, period, version, context), []).append((position, mid))
    values = []
    for key, entries in boards.items():
        relevant = positive.get(key[0], set())
        ranked = [(pos, mid) for pos, mid in sorted(entries) if pos <= k]
        hits = sum(mid in relevant for _, mid in ranked)
        dcg = sum(1 / math.log2(pos + 1) for pos, mid in ranked if mid in relevant)
        ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(relevant))))
        values.append((hits / k, hits / len(relevant) if relevant else 0, dcg / ideal if ideal else 0))
    ranking = dict(k=k, users=len({key[0] for key in boards}), batches=len(boards),
        status='OK' if values else 'INSUFFICIENT_DATA',
        ground_truth='当前全库已看/收藏/评分≥4；按可见榜单批次计算，包含零命中；非转化归因')
    for i, name in enumerate(('precision_at_k', 'recall_at_k', 'ndcg_at_k')):
        ranking[name] = round(sum(v[i] for v in values) / len(values), 6) if values else None
    return dict(impressions=len(cohort), clicks=len(clicked),
        ctr=round(len(clicked) / len(cohort), 6) if cohort else None,
        channels=[dict(channel=ch, impressions=n, clicks=c, ctr=round(c/n, 6))
                  for ch, (n, c) in sorted(counts.items())], ranking=ranking)


# --- Read-only administrator aggregates (WP-B V2 ops) -----------------------
#
# The write path, cohort definition and 180-day retention above are untouched.
# Everything below only SELECTs and is shared by /api/admin/* read models.

VISIBLE_BOARD_VERSION = 'visible-v2'
EXPOSURE_RETENTION_DAYS = 180
EFFECTIVE_EXPOSURE_DEFINITION = (
    "有效曝光=board_version='visible-v2'（电影卡片至少 50% 进入视口持续 1 秒，或用户主动点击）；"
    "有效点击=与同一 (request_id, position, user_id) 有效曝光关联且 clicked_at>=shown_at 的去重点击；"
    "CTR=有效点击/有效曝光；曝光与点击记录默认保留 180 天")


def _window(column, start, end):
    return and_(column >= start, column < end)


def _impression_window(imp, start, end):
    return and_(_window(imp.c.shown_at, start, end), imp.c.board_version == VISIBLE_BOARD_VERSION)


def _attributable_clicks(clicks, imp):
    """Click must belong to an effective impression of the same user and come after it."""
    return clicks.join(imp, and_(imp.c.request_id == clicks.c.request_id,
                                 imp.c.position == clicks.c.position,
                                 imp.c.user_id == clicks.c.user_id,
                                 clicks.c.clicked_at >= imp.c.shown_at))


def _ratio(part, whole):
    return round(part / whole, 6) if whole else None


def _calendar_days(start, end):
    return [(start + timedelta(days=i)).date().isoformat()
            for i in range((end.date() - start.date()).days + 1)]


def summarize(connection, imp, clicks, start, end):
    """One windowed read model: totals, channel ranking and daily series."""
    impressions_where = _impression_window(imp, start, end)
    clicks_where = _window(clicks.c.clicked_at, start, end)
    impressions = int(connection.scalar(
        select(func.count()).select_from(imp).where(impressions_where)) or 0)
    clicks_total = int(connection.scalar(
        select(func.count()).select_from(clicks).where(clicks_where)) or 0)
    attributed = int(connection.scalar(
        select(func.count(distinct(imp.c.impression_id)))
        .select_from(_attributable_clicks(clicks, imp))
        .where(impressions_where, clicks_where)) or 0)
    # Unattributed clicks are raw click rows that match no effective exposure.
    # Count click rows (not impressions) so one click matching two impressions is
    # still only subtracted once from clicks_total.
    attributable_clicks = int(connection.scalar(
        select(func.count(distinct(clicks.c.click_id)))
        .select_from(_attributable_clicks(clicks, imp))
        .where(impressions_where, clicks_where, imp.c.impression_id.is_not(None))) or 0)
    accounts = int(connection.scalar(
        select(func.count(distinct(imp.c.user_id))).where(impressions_where)) or 0)
    per_channel = {channel: int(count) for channel, count in connection.execute(
        select(imp.c.channel, func.count()).where(impressions_where)
        .group_by(imp.c.channel)).all()}
    clicks_channel = {channel: int(count) for channel, count in connection.execute(
        select(imp.c.channel, func.count(distinct(imp.c.impression_id)))
        .select_from(_attributable_clicks(clicks, imp))
        .where(impressions_where, clicks_where).group_by(imp.c.channel)).all()}
    daily_impressions = {str(day)[:10]: int(count) for day, count in connection.execute(
        select(func.date(imp.c.shown_at), func.count()).where(impressions_where)
        .group_by(func.date(imp.c.shown_at))).all()}
    daily_clicks = {str(day)[:10]: int(count) for day, count in connection.execute(
        select(func.date(imp.c.shown_at), func.count(distinct(imp.c.impression_id)))
        .select_from(_attributable_clicks(clicks, imp))
        .where(impressions_where, clicks_where)
        .group_by(func.date(imp.c.shown_at))).all()}
    channels = [{'channel': channel, 'impressions': n,
                 'clicks': clicks_channel.get(channel, 0),
                 'ctr': _ratio(clicks_channel.get(channel, 0), n)}
                for channel, n in sorted(per_channel.items(), key=lambda kv: (-kv[1], kv[0]))]
    daily = [{'date': day, 'impressions': daily_impressions.get(day, 0),
              'clicks': daily_clicks.get(day, 0),
              'ctr': _ratio(daily_clicks.get(day, 0), daily_impressions.get(day, 0))}
             for day in _calendar_days(start, end)]
    return {'impressions': impressions, 'clicks': attributed,
            'ctr': _ratio(attributed, impressions), 'accounts': accounts,
            'channels': channels, 'daily': daily,
            'unattributed_clicks': max(0, clicks_total - attributable_clicks)}


def movie_totals(connection, imp, clicks, start, end):
    """Per-movie exposure/click counts for the same effective-exposure cohort."""
    impressions_where = _impression_window(imp, start, end)
    clicks_where = _window(clicks.c.clicked_at, start, end)
    totals = {int(mid): {'impressions': int(count), 'clicks': 0} for mid, count in
              connection.execute(select(imp.c.movie_id, func.count())
                                 .where(impressions_where, imp.c.movie_id.is_not(None))
                                 .group_by(imp.c.movie_id)).all()}
    for mid, count in connection.execute(
            select(imp.c.movie_id, func.count(distinct(imp.c.impression_id)))
            .select_from(_attributable_clicks(clicks, imp))
            .where(impressions_where, clicks_where, imp.c.movie_id.is_not(None))
            .group_by(imp.c.movie_id)).all():
        totals.setdefault(int(mid), {'impressions': 0, 'clicks': 0})['clicks'] = int(count)
    return totals


def tracking_counts(connection, imp, now, window_days=7):
    """Latest effective exposure plus 7d/24h counts; used by the health endpoint."""
    cohort = imp.c.board_version == VISIBLE_BOARD_VERSION
    latest = connection.scalar(select(func.max(imp.c.shown_at)).where(cohort))
    impressions_7d = int(connection.scalar(
        select(func.count()).select_from(imp).where(cohort, imp.c.shown_at >= now - timedelta(days=window_days))) or 0)
    impressions_24h = int(connection.scalar(
        select(func.count()).select_from(imp).where(cohort, imp.c.shown_at >= now - timedelta(hours=24))) or 0)
    return {'last_impression_at': latest, 'impressions_7d': impressions_7d,
            'impressions_24h': impressions_24h}
