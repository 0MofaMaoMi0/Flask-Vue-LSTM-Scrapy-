"""Administrator-only dashboard aggregates. Read-only; no raw identities or AI content.

The catalog is an all-active-movies snapshot. Events/usage use UTC calendar days,
including the partial current day. Missing telemetry is never represented as zero.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from collections import Counter, defaultdict

from sqlalchemy import (BigInteger, Numeric, and_, case, cast, distinct, func, or_, select, text)
from sqlalchemy.exc import SQLAlchemyError

from app.models import AuthSession, User
from catalog.models import CatalogBase
from catalog.read_queries import _PERSON_ASSETS


ALLOWED_DAYS = frozenset((7, 30, 90))
EVENT_LABELS = {
    'watched': '已看', 'favorite': '收藏', 'want': '想看',
    'rating': '评分', 'not_interested': '不感兴趣',
}
COUNTRIES_SQL = text("""
WITH ranked AS (
    SELECT s.id, s.movie_id,
           ROW_NUMBER() OVER (
               PARTITION BY s.movie_id
               ORDER BY CASE WHEN s.source = 'tmdb' THEN 0 ELSE 1 END,
                        s.fetched_at DESC, s.id DESC
           ) AS source_rank
    FROM movie_source_records s
    JOIN movies m ON m.movie_id = s.movie_id AND m.catalog_status = 'active'
    WHERE s.source IN ('tmdb', 'legacy_movies2')
), valid_countries AS (
    SELECT DISTINCT r.movie_id, UPPER(j.iso2) AS iso2
    FROM ranked r
    JOIN movie_source_records s ON s.id = r.id
    JOIN JSON_TABLE(s.metadata_json, '$.production_countries[*]'
        COLUMNS (iso2 VARCHAR(8) PATH '$.iso_3166_1' NULL ON EMPTY NULL ON ERROR)
    ) AS j ON TRUE
    WHERE r.source_rank = 1 AND j.iso2 REGEXP '^[A-Za-z]{2}$'
)
SELECT iso2, COUNT(DISTINCT movie_id) AS movie_count,
       (SELECT COUNT(DISTINCT movie_id) FROM valid_countries) AS covered_movies
FROM valid_countries
GROUP BY iso2
ORDER BY movie_count DESC, iso2
""")


def parse_days(args):
    if set(args) - {'days'} or any(len(args.getlist(k)) != 1 for k in args):
        raise ValueError('Invalid parameters')
    raw = args.get('days', '30')
    if raw not in ('7', '30', '90'):
        raise ValueError('Invalid window')
    return int(raw)


def window_for(days, now=None):
    if type(days) is not int or days not in ALLOWED_DAYS:
        raise ValueError('Invalid window')
    now = now or datetime.utcnow()
    if now.tzinfo is not None:
        from datetime import timezone
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now


def filled_daily(rows, start, end):
    """Fill days with no records only after a successful aggregate query."""
    counts = {str(day)[:10]: int(count) for day, count in rows}
    return [
        {'date': (start + timedelta(days=i)).date().isoformat(),
         'count': counts.get((start + timedelta(days=i)).date().isoformat(), 0)}
        for i in range((end.date() - start.date()).days + 1)
    ]


def _count(connection, statement):
    return int(connection.scalar(statement) or 0)


def _true_json(column, key):
    value = func.json_extract(column, '$.' + key)
    return and_(func.json_type(value) == 'BOOLEAN', func.json_unquote(value) == 'true')


def _valid_state_rating(value):
    """Mirror the SQL JSON-number + DECIMAL(6,2) range check in Python.

    Hidden state counts must equal the former per-flag COUNT queries, including
    MySQL's rounding when casting JSON numbers to two decimals.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return False
    try:
        scaled = Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return False
    return Decimal('0.5') <= scaled <= Decimal(5)


def catalog_snapshot(connection):
    tables = CatalogBase.metadata.tables
    movies = tables['movies']; external = tables['movie_external_ids']
    genres = tables['genres']; links = tables['movie_genres']
    ratings = tables['movie_ratings_summary']; assets = tables['movie_assets']
    credits = tables['movie_credits']; photos = _PERSON_ASSETS
    active = movies.c.catalog_status == 'active'
    total = _count(connection, select(func.count()).select_from(movies).where(active))
    mapped = _count(connection, select(func.count(distinct(external.c.movie_id)))
                    .select_from(external.join(movies)).where(active, external.c.source == 'tmdb'))
    genre_rows = connection.execute(
        select(genres.c.name_zh, func.count(distinct(links.c.movie_id)))
        .select_from(links.join(genres).join(movies)).where(active)
        .group_by(genres.c.name_zh).order_by(func.count(distinct(links.c.movie_id)).desc(), genres.c.name_zh)
    ).all()
    classified = _count(connection, select(func.count(distinct(links.c.movie_id)))
                        .select_from(links.join(movies)).where(active))
    decade = func.floor(movies.c.release_year / 10) * 10
    decade_rows = connection.execute(select(decade, func.count()).where(active)
                                     .group_by(decade).order_by(decade)).all()
    comparable = and_(active, ratings.c.source == 'douban', ratings.c.score_scale == 10,
                      ratings.c.score_value.is_not(None), ratings.c.score_value >= 0,
                      ratings.c.score_value <= 10)
    bucket = case((ratings.c.score_value < 2, '0–2'), (ratings.c.score_value < 4, '2–4'),
                  (ratings.c.score_value < 6, '4–6'), (ratings.c.score_value < 8, '6–8'), else_='8–10')
    rating_counts = dict(connection.execute(select(bucket, func.count())
                         .select_from(ratings.join(movies)).where(comparable).group_by(bucket)).all())
    mean = connection.scalar(select(func.avg(ratings.c.score_value))
                             .select_from(ratings.join(movies)).where(comparable))
    country_rows = connection.execute(COUNTRIES_SQL).mappings().all()
    country_covered = int(country_rows[0]['covered_movies']) if country_rows else 0
    posters = _count(connection, select(func.count(distinct(assets.c.movie_id)))
                     .select_from(assets.join(movies)).where(active, assets.c.asset_type == 'poster',
                         func.length(func.trim(assets.c.source_path)) > 0))
    cast_movies = _count(connection, select(func.count(distinct(credits.c.movie_id)))
                         .select_from(credits.join(movies)).where(active, credits.c.role_type == 'cast'))
    cast_photos = _count(connection, select(func.count(distinct(credits.c.movie_id)))
                        .select_from(credits.join(movies).join(photos, photos.c.person_id == credits.c.person_id))
                        .where(active, credits.c.role_type == 'cast', photos.c.is_primary.is_(True),
                               func.length(func.trim(photos.c.source_path)) > 0))
    overviews = _count(connection, select(func.count()).select_from(movies)
                       .where(active, func.length(func.trim(movies.c.overview)) > 0))
    genre_items = [{'name': name, 'count': int(count)} for name, count in genre_rows]
    if total > classified:
        genre_items.append({'name': '未分类', 'count': total - classified})
    return {
        'total': total, 'tmdb_mapped': mapped,
        'genres': genre_items,
        'decades': [{'name': str(int(year)) + '年代' if year is not None else '年份未知',
                     'count': int(count)} for year, count in decade_rows],
        'ratings': [{'name': label, 'count': int(rating_counts.get(label, 0))}
                    for label in ('0–2', '2–4', '4–6', '6–8', '8–10')],
        'rating_source': 'douban', 'rating_scale': 10,
        'rating_count': sum(int(v) for v in rating_counts.values()),
        'rating_unknown_movies': total - sum(int(v) for v in rating_counts.values()),
        'average_rating': round(float(mean), 2) if mean is not None else None,
        'countries': [{'iso2': r['iso2'], 'count': int(r['movie_count'])} for r in country_rows],
        'countries_covered_movies': country_covered,
        'countries_unknown_movies': total - country_covered,
        'quality': {'total': total, 'tmdb_mapped': mapped, 'posters': posters,
                    'cast': cast_movies, 'cast_photos': cast_photos, 'overview': overviews},
    }


def behavior_snapshot(connection, repository, start, end):
    movies = CatalogBase.metadata.tables['movies']
    events, states = repository.tables['events'], repository.tables['states']
    window = and_(events.c.created_at >= start, events.c.created_at < end)
    event_total = _count(connection, select(func.count()).select_from(events).where(window))
    active_users = _count(connection, select(func.count(distinct(events.c.user_id))).where(window))
    daily = connection.execute(select(func.date(events.c.created_at), func.count())
                               .where(window).group_by(func.date(events.c.created_at))
                               .order_by(func.date(events.c.created_at))).all()
    kind = func.json_unquote(func.json_extract(events.c.payload, '$.kind'))
    kind_rows = connection.execute(select(kind, func.count()).where(window).group_by(kind)).all()
    known = {key: 0 for key in EVENT_LABELS}; other = 0
    for key, count in kind_rows:
        if key in known: known[key] += int(count)
        else: other += int(count)
    kinds = [{'name': label, 'kind': key, 'count': known[key]} for key, label in EVENT_LABELS.items()]
    if other: kinds.append({'name': '其他', 'kind': 'other', 'count': other})
    top_rows = connection.execute(select(movies.c.movie_id, movies.c.title, func.count())
                                  .select_from(events.join(movies, movies.c.movie_id == events.c.movie_id))
                                  .where(window, movies.c.catalog_status == 'active')
                                  .group_by(movies.c.movie_id, movies.c.title)
                                  .order_by(func.count().desc(), movies.c.movie_id).limit(8)).all()
    current_fields = []
    state_keys = ('watched', 'favorite', 'want', 'not_interested')
    for key in state_keys:
        current_fields.append(func.coalesce(func.sum(case((_true_json(states.c.state, key), 1), else_=0)), 0).label(key))
    rating = func.json_extract(states.c.state, '$.rating')
    valid_rating = and_(func.json_type(rating).in_(('INTEGER', 'DOUBLE', 'DECIMAL')),
                        cast(func.json_unquote(rating), Numeric(6, 2)) >= .5,
                        cast(func.json_unquote(rating), Numeric(6, 2)) <= 5)
    current_fields.append(func.coalesce(func.sum(case((valid_rating, 1), else_=0)), 0).label('rated'))
    current_row = connection.execute(select(*current_fields).select_from(states.join(movies, movies.c.movie_id == states.c.movie_id))
                                      .where(movies.c.catalog_status == 'active')).mappings().one()
    return {'events': event_total, 'active_users': active_users,
            'daily': filled_daily(daily, start, end), 'kinds': kinds,
            'top_movies': [{'movie_id': int(mid), 'title': title, 'count': int(count)}
                           for mid, title, count in top_rows],
            'current': {key: int(current_row[key]) for key in (*state_keys, 'rated')}}


def users_snapshot(connection, start, end):
    users, sessions = User.__table__, AuthSession.__table__
    row = connection.execute(select(
        func.count().label('accounts_total'),
        func.coalesce(func.sum(case((users.c.status == 'active', 1), else_=0)), 0).label('accounts_active'),
        func.coalesce(func.sum(case((and_(users.c.created_at >= start, users.c.created_at < end), 1), else_=0)), 0).label('new_in_window'),
    )).mappings().one()
    valid = _count(connection, select(func.count()).select_from(sessions.join(users))
                    .where(sessions.c.user_id.is_not(None), sessions.c.revoked_at.is_(None),
                           sessions.c.idle_expires_at > end, sessions.c.absolute_expires_at > end,
                           users.c.status == 'active'))
    return {**{key: int(value) for key, value in row.items()}, 'sessions_valid': valid}


def _token_value(tokens, key):
    value = func.json_extract(tokens, '$.' + key)
    number = cast(func.json_unquote(value), BigInteger)
    return case((and_(func.json_type(value) == 'INTEGER', number >= 0), number), else_=None)


def ai_snapshot(app, start, end):
    unavailable = {'status': 'UNAVAILABLE', 'calls': None, 'known_input_tokens': None,
                   'known_output_tokens': None, 'unknown_calls': None,
                   'real_calls': None, 'mock_calls': None, 'unknown_provenance_calls': None,
                   'real_input_tokens': None, 'real_output_tokens': None, 'real_unknown_calls': None,
                   'daily': [], 'statuses': [], 'models': [], 'paid_request_calls': None,
                   'amount': None, 'amount_status': 'UNKNOWN'}
    engine = app.extensions.get('m7_engine'); ledger = app.extensions.get('m7_ledger')
    if engine is None or ledger is None:
        return unavailable
    table = ledger.md.tables['usage']
    window = and_(table.c.created_at >= start, table.c.created_at < end, table.c.status != 'NOT_DISPATCHED')
    input_tokens = _token_value(table.c.tokens, 'input_tokens')
    output_tokens = _token_value(table.c.tokens, 'output_tokens')
    kind = func.upper(func.json_unquote(func.json_extract(table.c.tokens, '$.kind')))
    mock = or_(func.lower(table.c.model).like('mock%'), kind.in_(('MOCK', 'M7_MOCK')))
    real = and_(kind == 'BYOK_AGENT', _true_json(table.c.tokens, 'paid_request'), ~mock)
    # SQL JSON nulls need an explicit false fallback; UNKNOWN is never called real.
    mock_flag = case((mock, 1), else_=0)
    real_flag = case((real, 1), else_=0)
    try:
        with engine.connect() as connection:
            row = connection.execute(select(func.count().label('calls'),
                func.sum(input_tokens).label('known_input_tokens'), func.count(input_tokens).label('input_reported'),
                func.sum(output_tokens).label('known_output_tokens'), func.count(output_tokens).label('output_reported'),
                func.coalesce(func.sum(case((and_(input_tokens.is_not(None), output_tokens.is_not(None)), 0), else_=1)), 0).label('unknown_calls'),
                func.coalesce(func.sum(case((_true_json(table.c.tokens, 'paid_request'), 1), else_=0)), 0).label('paid_request_calls'),
                func.coalesce(func.sum(real_flag), 0).label('real_calls'),
                func.coalesce(func.sum(mock_flag), 0).label('mock_calls'),
                func.sum(case((real_flag == 1, input_tokens), else_=None)).label('real_input_tokens'),
                func.count(case((real_flag == 1, input_tokens), else_=None)).label('real_input_reported'),
                func.sum(case((real_flag == 1, output_tokens), else_=None)).label('real_output_tokens'),
                func.count(case((real_flag == 1, output_tokens), else_=None)).label('real_output_reported'),
                func.coalesce(func.sum(case((and_(real_flag == 1, or_(input_tokens.is_(None), output_tokens.is_(None))), 1), else_=0)), 0).label('real_unknown_calls'),
            ).where(window)).mappings().one()
            daily = connection.execute(select(func.date(table.c.created_at), func.count()).where(window)
                                        .group_by(func.date(table.c.created_at))).all()
            statuses = connection.execute(select(table.c.status, func.count()).where(window)
                                           .group_by(table.c.status).order_by(func.count().desc())).all()
            models = connection.execute(select(table.c.model, func.count()).where(window)
                                         .group_by(table.c.model).order_by(func.count().desc(), table.c.model)).all()
        calls = int(row['calls'])
        real_calls = int(row['real_calls']); mock_calls = int(row['mock_calls'])
        return {'status': 'AVAILABLE', 'calls': calls,
                'known_input_tokens': int(row['known_input_tokens'] or 0) if row['input_reported'] or not calls else None,
                'known_output_tokens': int(row['known_output_tokens'] or 0) if row['output_reported'] or not calls else None,
                'unknown_calls': int(row['unknown_calls']), 'paid_request_calls': int(row['paid_request_calls']),
                'real_calls': real_calls, 'mock_calls': mock_calls,
                'unknown_provenance_calls': calls - real_calls - mock_calls,
                'real_input_tokens': int(row['real_input_tokens'] or 0) if row['real_input_reported'] or not real_calls else None,
                'real_output_tokens': int(row['real_output_tokens'] or 0) if row['real_output_reported'] or not real_calls else None,
                'real_unknown_calls': int(row['real_unknown_calls']),
                'daily': filled_daily(daily, start, end),
                'statuses': [{'name': name, 'count': int(count)} for name, count in statuses],
                'models': [{'name': name, 'count': int(count)} for name, count in models],
                'amount': None, 'amount_status': 'UNKNOWN'}
    except SQLAlchemyError:
        return unavailable


def build_dashboard(app, days, now=None):
    start, end = window_for(days, now)
    generated = {}
    with app.extensions['auth_engine'].connect() as connection:
        catalog = catalog_snapshot(connection)
        generated['catalog'] = _stamp(datetime.utcnow())
        behavior = behavior_snapshot(connection, app.extensions['m6_behavior'], start, end)
        generated['behavior'] = _stamp(datetime.utcnow())
        users = users_snapshot(connection, start, end)
        generated['users'] = _stamp(datetime.utcnow())
        try:
            feedback = feedback_tables(app)
        except ValueError:
            feedback = None
        recommendation = recommendation_snapshot(connection, feedback, start, end)
        generated['recommendation'] = _stamp(datetime.utcnow())
    ai = ai_snapshot(app, start, end)
    generated['ai'] = _stamp(datetime.utcnow())
    system = system_block(generated, ai, end)
    record_generation('dashboard', end)
    return {'as_of': end.isoformat() + 'Z',
            'window': {'days': days, 'from': start.isoformat() + 'Z', 'to': end.isoformat() + 'Z', 'timezone': 'UTC'},
            'catalog': catalog, 'behavior': behavior, 'users': users,
            'ai': ai, 'recommendation': recommendation, 'system': system,
            'notes': [
                '电影目录与质量为全部在架影片的当前快照，不随时间窗口改变。',
                '事件与模型调用按 UTC 服务端时间统计，窗口含今日未结束部分；零事件日期补零。',
                '类型与制作国家均为多标签计数，同一电影在同一标签内仅计一次；跨标签总和可超过片数。',
                '制作国家优先采用 TMDB 来源，否则采用最新 legacy_movies2 来源；无可靠元数据计入未知。',
                '评分只统计豆瓣 10 分制有效汇总，每片一次；不混合来源或模拟推荐准确率。',
                '演员、海报和照片覆盖表示存在数据库关联或路径，未逐张验证远端可访问性。',
                '启用账号数不是活跃用户；窗口活跃用户仅为窗口内产生电影行为事件的不同账号。',
                '当前已看、收藏、想看、评分与不感兴趣为在架影片的独立状态，不从事件次数推算。',
                '热门影片按窗口行为事件数排序（含设置与撤销），仅列出当前在架影片。',
                '模型调用排除未派发尝试；包括 Mock 与历史测试记录，按模型分组展示。真实调用仅为明确 BYOK_AGENT 且 paid_request=true 的派发尝试，不等于成功或账单；其余来源标为未知。',
                '已知 Token 为小计，未知用量不计成零；全账本与真实Provider小计分别返回，Mock不用于估算用户费用。金额未知。',
                '推荐曝光/点击只统计有效视口 cohort（board_version=visible-v2）；窗口内没有曝光即真实为零，不补造频道或历史曲线。',
                'system.generated_at 是进程内计时，进程重启后清空；它不代表数据库快照时间。',
            ]}


# --- Admin cockpit V2.0 operational analytics (read-only, WP-B) -------------

METRICS_VERSION = 'admin-metrics-v2.0'
HIGH_RATING_MIN = 4
HIGH_RATING_SCALE = 5
MIN_PREFERENCE_USERS = 3
MIN_INTEREST_PAIRS = 3
MIN_INTEREST_IMPRESSIONS = 10
MIN_JUDGEABLE_IMPRESSIONS = 10
AI_FAILURE_RATE_THRESHOLD = 0.5
AI_FAILURE_MIN_CALLS = 10
AI_FAILURE_WINDOW_DAYS = 7
RECOMMENDATION_GAP_WINDOW_DAYS = 7
RECOMMENDATION_GAP_STALE_HOURS = 24
RECOMMENDATION_GAP_MIN_IMPRESSIONS = 10
TMDB_MIN_VOTES = 50
EXPOSURE_RETENTION_DAYS = 180

PREFERENCE_SIGNALS = ('favorite', 'want', 'high_rating')
PREFERENCE_DIMENSIONS = ('genre', 'decade', 'country')
UNKNOWN_LABELS = {'genre': '未分类', 'decade': '年份未知', 'country': '未知'}

GENERATION_BLOCKS = ('dashboard', 'users_analytics', 'interest_match',
                     'recommendation_analytics', 'ai_analytics', 'health')
PROCESS_STARTED_AT = datetime.utcnow()
_GENERATION = {}


def _stamp(value=None):
    return (value or datetime.utcnow()).isoformat() + 'Z'


def record_generation(block, at=None):
    """Remember the last successful generation time of an admin stats block."""
    if block not in GENERATION_BLOCKS:
        raise ValueError('Unknown stats block')
    _GENERATION[block] = at or datetime.utcnow()


def generation_state(now=None):
    now = now or datetime.utcnow()
    return {'status': 'available', 'source': '进程内记录', 'as_of': _stamp(now),
            'sample_size': {'blocks_recorded': len(_GENERATION)},
            'process_started_at': _stamp(PROCESS_STARTED_AT),
            'last_success': {block: (_stamp(_GENERATION[block]) if block in _GENERATION else None)
                             for block in GENERATION_BLOCKS},
            'definition': '各管理统计接口最近一次成功返回时间；进程重启后清空，不代表数据库快照时间'}


def _single_values(args):
    if any(len(args.getlist(key)) != 1 for key in args):
        raise ValueError('Invalid parameters')


def _days_arg(args):
    raw = args.get('days', '30')
    if raw not in ('7', '30', '90'):
        raise ValueError('Invalid window')
    return int(raw)


def parse_kind_days(args):
    _single_values(args)
    if set(args) - {'days', 'kind'}:
        raise ValueError('Invalid parameters')
    kind = args.get('kind')
    if kind is not None and kind not in EVENT_LABELS:
        raise ValueError('Invalid kind')
    return _days_arg(args), kind


def parse_dimension_days(args, dimensions):
    _single_values(args)
    if set(args) - {'days', 'dimension'}:
        raise ValueError('Invalid parameters')
    dimension = args.get('dimension', 'genre')
    if dimension not in dimensions:
        raise ValueError('Invalid dimension')
    return _days_arg(args), dimension


def _parse_timestamp(raw):
    if type(raw) is not str or not raw:
        raise ValueError('Invalid timestamp')
    if len(raw) == 10:
        if not raw.isascii():
            raise ValueError('Invalid timestamp')
        return datetime.strptime(raw, '%Y-%m-%d')
    value = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def parse_events_args(args):
    _single_values(args)
    if set(args) - {'limit', 'offset', 'from', 'to', 'kind', 'movie_id'}:
        raise ValueError('Invalid parameters')
    for key in ('limit', 'offset', 'movie_id'):
        raw = args.get(key)
        if raw is not None and (not raw.isascii() or not raw.isdecimal()):
            raise ValueError('Invalid ' + key)
    limit = int(args.get('limit', '60'))
    offset = int(args.get('offset', '0'))
    if not 1 <= limit <= 100 or not 0 <= offset <= 1000000:
        raise ValueError('Invalid pagination')
    movie_id = int(args['movie_id']) if 'movie_id' in args else None
    if movie_id is not None and movie_id <= 0:
        raise ValueError('Invalid movie id')
    kind = args.get('kind')
    if kind is not None and kind not in EVENT_LABELS:
        raise ValueError('Invalid kind')
    start = _parse_timestamp(args['from']) if 'from' in args else None
    end = _parse_timestamp(args['to']) if 'to' in args else None
    if start is not None and end is not None and start >= end:
        raise ValueError('Invalid window')
    return {'limit': limit, 'offset': offset, 'start': start, 'end': end,
            'kind': kind, 'movie_id': movie_id}


def feedback_tables(app):
    """Activity-schema exposure/click tables; raises ValueError when unconfigured."""
    schema = app.config.get('M6_ACTIVITY_SCHEMA')
    if not schema:
        raise ValueError('Activity schema not configured')
    from catalog.recommendation_feedback import tables
    return tables(schema)


def recommendation_snapshot(connection, feedback, start, end):
    """Page 01 recommendation KPI block; degrades to an explicit status."""
    from catalog.recommendation_feedback import EFFECTIVE_EXPOSURE_DEFINITION, summarize
    block = {'status': 'unavailable',
             'source': 'recommendation_impressions + recommendation_clicks（有效视口 cohort）',
             'sample_size': None, 'as_of': _stamp(end),
             'definition': EFFECTIVE_EXPOSURE_DEFINITION,
             'impressions': None, 'clicks': None, 'ctr': None, 'accounts': None,
             'channels': [], 'daily': [], 'unattributed_clicks': None, 'reason': None}
    if feedback is None:
        return dict(block, reason='推荐曝光表未配置（M6_ACTIVITY_SCHEMA 缺失）')
    try:
        data = summarize(connection, feedback[0], feedback[1], start, end)
    except SQLAlchemyError:
        return dict(block, reason='推荐曝光/点击表不可用（未初始化或查询失败）')
    return dict(block, status='available', sample_size=data['impressions'],
                impressions=data['impressions'], clicks=data['clicks'], ctr=data['ctr'],
                accounts=data['accounts'], channels=data['channels'], daily=data['daily'],
                unattributed_clicks=data['unattributed_clicks'])


def system_block(generated, ai, end):
    failures = None
    failure_status = 'unknown'
    if ai.get('status') == 'AVAILABLE':
        failures = sum(int(row['count']) for row in ai.get('statuses') or []
                       if ai_status_group(row.get('name')) == 'failed')
        failure_status = 'available'
    elif ai.get('status') == 'UNAVAILABLE':
        failure_status = 'not_instrumented'
    return {'status': 'available', 'source': '进程内计时 + M7 usage 账本',
            'sample_size': ai.get('calls'), 'as_of': _stamp(end),
            'generated_at': generated, 'process_started_at': _stamp(PROCESS_STARTED_AT),
            'note': 'generated_at 为进程内记录，进程重启后清空；不代表数据库快照时间。',
            'ai_failures': failures, 'ai_failure_status': failure_status,
            'definition': 'AI 失败数=窗口内已派发（排除 NOT_DISPATCHED）且账本状态为 FAILED/ERROR 的调用数'}


def _active_movies(connection):
    movies = CatalogBase.metadata.tables['movies']
    return [(int(mid), year) for mid, year in connection.execute(
        select(movies.c.movie_id, movies.c.release_year)
        .where(movies.c.catalog_status == 'active')).all()]


def _movie_genres(connection, movie_ids=None):
    tables = CatalogBase.metadata.tables
    links, genres, movies = tables['movie_genres'], tables['genres'], tables['movies']
    statement = (select(links.c.movie_id, genres.c.name_zh)
                 .select_from(links.join(genres).join(movies))
                 .where(movies.c.catalog_status == 'active'))
    if movie_ids is not None:
        ids = {int(mid) for mid in movie_ids}
        if not ids:
            return {}
        statement = statement.where(links.c.movie_id.in_(ids))
    mapping = defaultdict(list)
    for mid, name in connection.execute(statement).all():
        mapping[int(mid)].append(name)
    return dict(mapping)


def _movie_decades(connection, movie_ids=None):
    movies = CatalogBase.metadata.tables['movies']
    statement = (select(movies.c.movie_id, movies.c.release_year)
                 .where(movies.c.catalog_status == 'active'))
    if movie_ids is not None:
        ids = {int(mid) for mid in movie_ids}
        if not ids:
            return {}
        # Filter in SQL so a bounded preference pass never reloads all active movies.
        statement = statement.where(movies.c.movie_id.in_(ids))
    return {int(mid): ['年份未知' if year is None else '%d年代' % (int(year) // 10 * 10)]
            for mid, year in connection.execute(statement).all()}


def _movie_countries(connection, movie_ids):
    """Latest TMDB (fallback legacy_movies2) production countries for bounded ids.

    Never ORDER BY a query that selects ``metadata_json`` (avoids MySQL 1038
    Out of sort memory): pick winning PKs on narrow columns first, then read
    ``metadata_json`` by PK without sorting.
    """
    ids = {int(mid) for mid in movie_ids}
    if not ids:
        return {}
    sources = CatalogBase.metadata.tables['movie_source_records']
    key_rows = connection.execute(
        select(sources.c.id, sources.c.movie_id, sources.c.source, sources.c.fetched_at)
        .where(sources.c.movie_id.in_(ids), sources.c.source.in_(('tmdb', 'legacy_movies2')))
    ).all()
    winners = {}
    for row_id, mid, source, fetched_at in key_rows:
        mid = int(mid)
        rank = (1 if source == 'tmdb' else 0, fetched_at or datetime.min, int(row_id))
        prev = winners.get(mid)
        if prev is None or rank > prev[0]:
            winners[mid] = (rank, int(row_id))
    if not winners:
        return {}
    win_ids = [item[1] for item in winners.values()]
    chosen = {}
    for mid, payload in connection.execute(
            select(sources.c.movie_id, sources.c.metadata_json)
            .where(sources.c.id.in_(win_ids))).all():
        chosen[int(mid)] = payload
    mapping = {}
    for mid, payload in chosen.items():
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = None
        countries = []
        if isinstance(payload, dict):
            for item in payload.get('production_countries') or []:
                if isinstance(item, dict):
                    iso2 = str(item.get('iso_3166_1') or '').strip().upper()
                    if len(iso2) == 2 and iso2.isascii() and iso2.isalpha():
                        countries.append(iso2)
        if countries:
            mapping[mid] = sorted(set(countries))
    return mapping


def _signal_distribution(pairs, mapping, unknown_label):
    pair_counts, user_sets = {}, {}
    for uid, mid in pairs:
        for label in mapping.get(int(mid)) or [unknown_label]:
            pair_counts[label] = pair_counts.get(label, 0) + 1
            user_sets.setdefault(label, set()).add(uid)
    total_pairs = len(pairs)
    total_users = len({uid for uid, _ in pairs})

    def rows(counts, total):
        ordered = sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))
        return [{'name': name, 'count': int(count),
                 'share': round(count / total, 6) if total else 0.0}
                for name, count in ordered]

    return {'by_pair': rows(pair_counts, total_pairs),
            'by_user': rows({key: len(value) for key, value in user_sets.items()}, total_users)}


def users_analytics(connection, repository, start, end, kind=None, now=None):
    """Page 06 read model: accounts, events, current states, preferences, boards."""
    now = now or datetime.utcnow()
    movies = CatalogBase.metadata.tables['movies']
    users = User.__table__
    events, states = repository.tables['events'], repository.tables['states']
    window = and_(events.c.created_at >= start, events.c.created_at < end)
    kind_expr = func.json_unquote(func.json_extract(events.c.payload, '$.kind'))
    event_window = and_(window, kind_expr == kind) if kind else window
    registered = connection.execute(select(
        func.count().label('total'),
        func.coalesce(func.sum(case((users.c.status == 'active', 1), else_=0)), 0).label('active'),
        func.coalesce(func.sum(case((and_(users.c.created_at >= start, users.c.created_at < end), 1),
                                    else_=0)), 0).label('new_in_window'),
    )).mappings().one()
    registration_daily = connection.execute(
        select(func.date(users.c.created_at), func.count())
        .where(users.c.created_at >= start, users.c.created_at < end)
        .group_by(func.date(users.c.created_at))).all()
    event_daily = connection.execute(
        select(func.date(events.c.created_at), func.count())
        .where(event_window).group_by(func.date(events.c.created_at))).all()
    active_daily = connection.execute(
        select(func.date(events.c.created_at), func.count(distinct(events.c.user_id)))
        .where(event_window).group_by(func.date(events.c.created_at))).all()
    event_total = _count(connection, select(func.count()).select_from(events).where(event_window))
    active_accounts = _count(connection, select(func.count(distinct(events.c.user_id))).where(event_window))

    active_movies = movies.c.catalog_status == 'active'
    joined = states.join(movies, movies.c.movie_id == states.c.movie_id)
    # One materialized scan feeds both the hidden state counts and preference pairs.
    state_rows = connection.execute(
        select(states.c.user_id, states.c.movie_id, states.c.state)
        .select_from(joined).where(active_movies)).all()
    state_counts = {'favorite': 0, 'want': 0, 'watched': 0, 'not_interested': 0, 'rating': 0}
    signal_pairs = {signal: set() for signal in PREFERENCE_SIGNALS}
    for uid, mid, state in state_rows:
        if not isinstance(state, dict):
            continue
        pair = (int(uid), int(mid))
        for key in ('favorite', 'want', 'watched', 'not_interested'):
            if state.get(key) is True:
                state_counts[key] += 1
        if state.get('favorite') is True:
            signal_pairs['favorite'].add(pair)
        if state.get('want') is True:
            signal_pairs['want'].add(pair)
        rating_value = state.get('rating')
        if type(rating_value) in (int, float) and HIGH_RATING_MIN <= rating_value <= HIGH_RATING_SCALE:
            signal_pairs['high_rating'].add(pair)
        if _valid_state_rating(rating_value):
            state_counts['rating'] += 1
    state_counts = {key: state_counts[key] for key in ('favorite', 'want', 'watched', 'rating', 'not_interested')}
    state_counts['status'] = 'available'
    pair_movies = {mid for pairs in signal_pairs.values() for _uid, mid in pairs}
    mappings = {'genre': _movie_genres(connection, pair_movies),
                'decade': _movie_decades(connection, pair_movies),
                'country': _movie_countries(connection, pair_movies)}
    signals = {}
    for signal in PREFERENCE_SIGNALS:
        pairs = signal_pairs[signal]
        signal_users = len({uid for uid, _mid in pairs})
        entry = {'status': 'available', 'sample_size': {'users': signal_users, 'pairs': len(pairs)},
                 'by_user': {}, 'by_pair': {}}
        if signal_users < MIN_PREFERENCE_USERS:
            entry.update(status='suppressed', reason='去重账户少于 %d，隐藏细分维度' % MIN_PREFERENCE_USERS)
            for dimension in PREFERENCE_DIMENSIONS:
                entry['by_user'][dimension] = []
                entry['by_pair'][dimension] = []
        else:
            for dimension in PREFERENCE_DIMENSIONS:
                distributed = _signal_distribution(pairs, mappings[dimension], UNKNOWN_LABELS[dimension])
                entry['by_user'][dimension] = distributed['by_user']
                entry['by_pair'][dimension] = distributed['by_pair']
        signals[signal] = entry
    preference_users = {uid for pairs in signal_pairs.values() for uid, _mid in pairs}
    preference_pairs = {pair for pairs in signal_pairs.values() for pair in pairs}
    preference_status = 'available' if any(entry['status'] == 'available'
                                           for entry in signals.values()) else 'suppressed'
    preference = {
        'status': preference_status, 'source': 'user_movie_states 当前快照',
        'sample_size': {'users': len(preference_users), 'pairs': len(preference_pairs)},
        'signals': signals,
        'reason': None if preference_status == 'available' else
                  '当前正向状态账户少于 %d，细分维度已隐藏' % MIN_PREFERENCE_USERS,
        'definition': ('当前状态快照，不随时间窗口变化；收藏/想看=JSON 布尔 true，高评分=rating>=%d（%d 分制）；'
                       'by_user=每用户每类别计 1 次，by_pair=(user,movie) 计 1 次；类型为多标签，份额之和可超过 100%%'
                       % (HIGH_RATING_MIN, HIGH_RATING_SCALE)),
    }

    def board_rows(statement):
        return [{'movie_id': int(mid), 'title': title, 'count': int(count)}
                for mid, title, count in connection.execute(statement).all()]

    most_events = board_rows(
        select(movies.c.movie_id, movies.c.title, func.count())
        .select_from(events.join(movies, movies.c.movie_id == events.c.movie_id))
        .where(event_window, active_movies)
        .group_by(movies.c.movie_id, movies.c.title)
        .order_by(func.count().desc(), movies.c.movie_id).limit(10))

    def state_board(key):
        return board_rows(
            select(movies.c.movie_id, movies.c.title, func.count(distinct(states.c.user_id)))
            .select_from(states.join(movies, movies.c.movie_id == states.c.movie_id))
            .where(active_movies, _true_json(states.c.state, key))
            .group_by(movies.c.movie_id, movies.c.title)
            .order_by(func.count(distinct(states.c.user_id)).desc(), movies.c.movie_id).limit(10))

    return {
        'status': 'available',
        'source': ('auth_users + user_movie_events + user_movie_states + '
                   'movies/movie_genres/movie_source_records'),
        'sample_size': {'active_accounts': active_accounts, 'events': event_total,
                        'state_pairs': len(state_rows)},
        'as_of': _stamp(now), 'kind_filter': kind,
        'definition': {
            'registered': '注册总量=全部账户（含停用）；活跃=status=active；窗口新增=created_at 在窗口内',
            'active_accounts': '窗口内至少一条电影行为事件的不同账户，不是登录 DAU',
            'current_states': 'user_movie_states 当前快照（不随窗口变化），仅统计在架影片；评分为 5 分制有效评分',
            'events_vs_states': '行为事件次数、去重账户数、当前状态数三者独立计算，不互相推算',
        },
        'registered': {key: int(registered[key]) for key in ('total', 'active', 'new_in_window')},
        'daily': {'registrations': filled_daily(registration_daily, start, end),
                  'events': filled_daily(event_daily, start, end),
                  'active_accounts': filled_daily(active_daily, start, end)},
        'current_states': state_counts,
        'avg_events_per_active_account': {
            'value': round(event_total / active_accounts, 6) if active_accounts else None,
            'status': 'available',
            'definition': '窗口行为事件数 ÷ 窗口去重活跃账户数（与 kind 过滤一致）；无活跃账户时为 null'},
        'preference': preference,
        'boards': {'status': 'available',
                   'definition': ('事件最多=窗口行为事件数（含撤销，与 kind 过滤一致）；'
                                  '收藏/想看=当前状态去重账户数；均只含在架影片并给出真实 movie_id'),
                   'most_events': most_events,
                   'most_favorited': state_board('favorite'),
                   'most_wanted': state_board('want')},
    }


def interest_match(connection, repository, feedback, start, end, dimension='genre', now=None):
    """Page 07 descriptive supply/interest/exposure/click comparison (no shortfall claim)."""
    now = now or datetime.utcnow()
    if dimension not in ('genre', 'decade', 'country'):
        raise ValueError('Invalid dimension')
    common = {'status': 'insufficient_sample', 'dimension': dimension, 'as_of': _stamp(now),
              'source': ('movies/movie_genres/genres + user_movie_events + '
                         'recommendation_impressions/clicks'),
              'sample_size': {}, 'categories': [], 'reason': None,
              'definition': {
                  'supply': '在架影片按类别去重数；份额分母=全部在架影片（含未分类）；类型多标签，份额之和可超过 100%',
                  'interest': '窗口内 action=set 的正向事件：favorite/want/rating>=4，去重 (user, movie) 对',
                  'exposure': '窗口内有效曝光按曝光影片归属类别；份额分母=可归属类别的有效曝光数',
                  'click': '与有效曝光关联且 clicked_at>=shown_at 的 (request_id, position) 去重点击；CTR=点击/曝光',
                  'notice': '仅描述性对比，不输出“短缺/推荐无效”等结论；样本不足不下判断'},
              'thresholds': {'min_interest_pairs': MIN_INTEREST_PAIRS,
                             'min_impressions': MIN_INTEREST_IMPRESSIONS}}
    if dimension == 'country':
        return dict(common, status='not_instrumented',
                    supply={'active_movies': None, 'classified_movies': None, 'unknown_movies': None},
                    interest={'signal_counts': {}, 'any_positive_pairs': None, 'users': None},
                    exposure={'impressions': None, 'attributable_impressions': None,
                              'unattributable_impressions': None, 'clicks': None},
                    reason='制作国家维度需要逐片国家归属，首批未启用（country）')
    active_rows = _active_movies(connection)
    mapping = _movie_genres(connection) if dimension == 'genre' else _movie_decades(connection)
    total_active = len(active_rows)
    supply = Counter()
    for labels in mapping.values():
        for label in labels:
            supply[label] += 1
    classified = len(mapping)

    events = repository.tables['events']
    kind_expr = func.json_unquote(func.json_extract(events.c.payload, '$.kind'))
    action_expr = func.json_unquote(func.json_extract(events.c.payload, '$.action'))
    rating_raw = func.json_extract(events.c.payload, '$.value')
    positive = and_(events.c.created_at >= start, events.c.created_at < end, action_expr == 'set',
                    or_(kind_expr.in_(('favorite', 'want')),
                        and_(kind_expr == 'rating',
                             func.json_type(rating_raw).in_(('INTEGER', 'DOUBLE', 'DECIMAL')),
                             cast(func.json_unquote(rating_raw), Numeric(6, 2)) >= HIGH_RATING_MIN)))
    pair_sets = {'favorite': set(), 'want': set(), 'high_rating': set()}
    for uid, mid, kind in connection.execute(
            select(events.c.user_id, events.c.movie_id, kind_expr).where(positive)).all():
        key = 'high_rating' if kind == 'rating' else kind
        if key in pair_sets:
            pair_sets[key].add((int(uid), int(mid)))
    any_pairs = set().union(*pair_sets.values())
    interest_users = {uid for uid, _mid in any_pairs}
    attributable_pairs = sum(1 for _uid, mid in any_pairs if mapping.get(mid))

    totals, exposure_status = {}, 'available'
    try:
        from catalog.recommendation_feedback import movie_totals
        if feedback is not None:
            totals = movie_totals(connection, feedback[0], feedback[1], start, end)
        else:
            exposure_status = 'unavailable'
    except SQLAlchemyError:
        exposure_status = 'unavailable'
    exposure_by_label, clicks_by_label = Counter(), Counter()
    attributable_impressions = attributable_clicks = 0
    total_impressions = sum(int(stats['impressions']) for stats in totals.values())
    for mid, stats in totals.items():
        labels = mapping.get(int(mid))
        if not labels:
            continue
        attributable_impressions += int(stats['impressions'])
        attributable_clicks += int(stats['clicks'])
        for label in labels:
            exposure_by_label[label] += int(stats['impressions'])
            clicks_by_label[label] += int(stats['clicks'])

    label_pairs = defaultdict(lambda: {signal: set() for signal in PREFERENCE_SIGNALS})
    for signal, pairs in pair_sets.items():
        for uid, mid in pairs:
            for label in mapping.get(mid) or []:
                label_pairs[label][signal].add((uid, mid))

    categories = []
    for label in set(supply) | set(label_pairs) | set(exposure_by_label):
        entry = label_pairs.get(label) or {}
        favorite_pairs = len(entry.get('favorite') or ())
        want_pairs = len(entry.get('want') or ())
        high_pairs = len(entry.get('high_rating') or ())
        combined = set()
        for pairs in entry.values():
            combined |= pairs
        positive_pairs = len(combined)
        users = len({uid for pairs in entry.values() for uid, _mid in pairs})
        impressions = int(exposure_by_label.get(label, 0))
        clicks = int(clicks_by_label.get(label, 0))
        movies_count = int(supply.get(label, 0))
        categories.append({
            'key': label, 'name': label,
            'status': ('available' if positive_pairs >= MIN_INTEREST_PAIRS and
                       impressions >= MIN_INTEREST_IMPRESSIONS else 'insufficient_sample'),
            'supply': {'movies': movies_count,
                       'share': round(movies_count / total_active, 6) if total_active else 0.0},
            'interest': {'favorite_pairs': favorite_pairs, 'want_pairs': want_pairs,
                         'high_rating_pairs': high_pairs, 'positive_pairs': positive_pairs,
                         'users': users,
                         'share': round(positive_pairs / len(any_pairs), 6) if any_pairs else None},
            'exposure': {'impressions': impressions,
                         'share': round(impressions / attributable_impressions, 6)
                                  if attributable_impressions else None},
            'clicks': {'clicks': clicks,
                       'ctr': round(clicks / impressions, 6) if impressions else None},
            'sample_size': {'movies': movies_count, 'interest_pairs': positive_pairs,
                            'impressions': impressions, 'clicks': clicks},
        })
    categories.sort(key=lambda item: (-item['interest']['positive_pairs'],
                                      -item['exposure']['impressions'],
                                      -item['supply']['movies'], str(item['name'])))
    enough = (len(any_pairs) >= MIN_INTEREST_PAIRS and
              attributable_impressions >= MIN_INTEREST_IMPRESSIONS)
    signal_counts = {signal: len(pairs) for signal, pairs in pair_sets.items()}
    return dict(common, status='available' if enough else 'insufficient_sample',
                sample_size={'positive_pairs': len(any_pairs), 'impressions': total_impressions},
                supply={'active_movies': total_active, 'classified_movies': classified,
                        'unknown_movies': total_active - classified},
                interest={'signal_counts': signal_counts, 'any_positive_pairs': len(any_pairs),
                          'users': len(interest_users), 'attributable_pairs': attributable_pairs,
                          'unattributable_pairs': len(any_pairs) - attributable_pairs},
                exposure={'impressions': total_impressions,
                          'attributable_impressions': attributable_impressions,
                          'unattributable_impressions': total_impressions - attributable_impressions,
                          'clicks': attributable_clicks, 'status': exposure_status},
                categories=categories,
                reason=None if enough else '正向兴趣对或有效曝光低于公开阈值，仅展示描述性计数')


def recommendation_analytics(connection, feedback, start, end, now=None):
    """Page 08 recommendation operations; funnel/offline metrics never fabricated."""
    from catalog.recommendation_feedback import EFFECTIVE_EXPOSURE_DEFINITION, summarize
    now = now or datetime.utcnow()
    funnel = {'status': 'unavailable', 'available_steps': ['impression', 'click'],
              'reason': ('行为事件未记录推荐 request_id/position，无法把收藏/想看/已看归因到曝光，'
                         '且无明确归因窗口')}
    offline = {'status': 'not_evaluable',
               'reason': '当前无可信相关性标签与评估数据集；不展示 Recall@K/NDCG@K，也不把 CTR 当作准确率'}
    block = {'status': 'unavailable', 'source': 'recommendation_impressions + recommendation_clicks',
             'sample_size': None, 'as_of': _stamp(now),
             'definition': {'exposure': EFFECTIVE_EXPOSURE_DEFINITION,
                            'ctr': '有效点击/有效曝光；运营指标，不是推荐准确率',
                            'retention': '曝光与点击记录默认保留 %d 天' % EXPOSURE_RETENTION_DAYS},
             'totals': {'impressions': None, 'clicks': None, 'ctr': None, 'channels': None,
                        'accounts': None, 'unattributed_clicks': None},
             'channels': [], 'daily': [],
             'accounts': {'value': None, 'status': 'unavailable',
                          'definition': '窗口内至少一次有效曝光的去重账户数'},
             'funnel': funnel, 'offline_evaluation': offline, 'reason': None}
    if feedback is None:
        return dict(block, reason='推荐曝光表未配置（M6_ACTIVITY_SCHEMA 缺失）')
    try:
        data = summarize(connection, feedback[0], feedback[1], start, end)
    except SQLAlchemyError:
        return dict(block, reason='推荐曝光/点击表不可用（未初始化或查询失败）')
    channels = [dict(item, status=('available' if item['impressions'] >= MIN_JUDGEABLE_IMPRESSIONS
                                   else 'insufficient_sample'))
                for item in data['channels']]
    return dict(block, status='available', sample_size={'impressions': data['impressions'],
                                                        'clicks': data['clicks']},
                totals={'impressions': data['impressions'], 'clicks': data['clicks'],
                        'ctr': data['ctr'], 'channels': len(data['channels']),
                        'accounts': data['accounts'],
                        'unattributed_clicks': data['unattributed_clicks']},
                channels=channels, daily=data['daily'],
                accounts={'value': data['accounts'],
                          'status': 'available' if data['impressions'] else 'insufficient_sample',
                          'definition': '窗口内至少一次有效曝光的去重账户数'})


AI_SUCCESS_STATUSES = frozenset(('SUCCEEDED', 'VERIFIED'))
AI_FAILED_STATUSES = frozenset(('FAILED', 'ERROR'))
AI_INTERRUPTED_STATUSES = frozenset(('CANCELLED', 'CANCELLED_UNKNOWN', 'INTERRUPTED',
                                     'ABORTED', 'REJECTED'))
AI_TIMEOUT_STATUSES = frozenset(('TIMEOUT', 'TIMED_OUT'))


def ai_status_group(status):
    value = str(status or '').upper()
    if value in AI_SUCCESS_STATUSES:
        return 'success'
    if value in AI_FAILED_STATUSES:
        return 'failed'
    if value in AI_INTERRUPTED_STATUSES:
        return 'interrupted'
    if value in AI_TIMEOUT_STATUSES:
        return 'timeout'
    return 'unknown'


def _ai_metrics(connection, table, window, inp, out, cached):
    unknown = func.coalesce(func.sum(case((and_(inp.is_not(None), out.is_not(None)), 0), else_=1)), 0)
    return select(
        func.count().label('calls'),
        func.coalesce(func.sum(case((table.c.status.in_(tuple(AI_SUCCESS_STATUSES)), 1), else_=0)), 0).label('success'),
        func.sum(inp).label('known_input'),
        func.count(inp).label('input_reported'),
        func.sum(out).label('known_output'),
        func.count(out).label('output_reported'),
        func.sum(cached).label('known_cached'),
        func.count(cached).label('cached_reported'),
        unknown.label('unknown_calls'),
    ).where(window)


def ai_analytics(app, start, end, now=None):
    """Page 09 AI service read model; never exposes prompts, responses or keys."""
    now = now or datetime.utcnow()
    block = {'status': 'unavailable', 'source': 'M7 BYOK usage 账本',
             'sample_size': None, 'as_of': _stamp(now),
             'definition': {
                 'provenance': ('REAL=kind=BYOK_AGENT 且 paid_request=true 且模型非 mock*；'
                                'MOCK=模型名 mock* 或 kind∈{MOCK,M7_MOCK}；其余 UNKNOWN（未知不当作真实）'),
                 'status': ('成功=SUCCEEDED/VERIFIED；失败=FAILED/ERROR；中断=CANCELLED*/INTERRUPTED/ABORTED/REJECTED；'
                            '超时=TIMEOUT*；未知=UNKNOWN/DISPATCHED/RESERVED/UNAVAILABLE 等；NOT_DISPATCHED 不计入调用'),
                 'tokens': '已知 Token 为小计，未知用量不计 0；缓存命中输入是输入子集，不重复计入',
                 'privacy': '绝不返回 prompt、响应正文、API Key、端点或用户身份'},
             'totals': {'calls': None, 'success': None, 'failed': None, 'interrupted': None,
                        'timeout': None, 'unknown': None, 'success_rate': None,
                        'status': 'unavailable'},
             'provenance': [], 'daily': [], 'models': [],
             'tokens': {'status': 'unavailable', 'known_input_tokens': None,
                        'known_output_tokens': None, 'known_total_tokens': None,
                        'unknown_usage_calls': None},
             'cached_input': {'status': 'not_instrumented', 'tokens': None, 'reported_calls': None},
             'errors': {'status': 'not_instrumented', 'reason': '账本无错误类型字段'},
             'latency': {'status': 'not_instrumented', 'reason': '账本无请求计时字段'},
             'agent': {'status': 'not_instrumented', 'tool_calls': None, 'valid_movie_cards': None,
                       'reason': '无工具调用/卡片返回事件表'},
             'reason': None}
    engine = app.extensions.get('m7_engine')
    ledger = app.extensions.get('m7_ledger')
    if engine is None or ledger is None:
        return dict(block, reason='M7 账本未配置')
    table = ledger.md.tables['usage']
    window = and_(table.c.created_at >= start, table.c.created_at < end,
                  table.c.status != 'NOT_DISPATCHED')
    inp = _token_value(table.c.tokens, 'input_tokens')
    out = _token_value(table.c.tokens, 'output_tokens')
    cached_raw = _token_value(table.c.tokens, 'cached_input_tokens')
    cached = case((and_(inp.is_not(None), cached_raw <= inp), cached_raw), else_=None)
    kind = func.upper(func.json_unquote(func.json_extract(table.c.tokens, '$.kind')))
    mock = or_(func.lower(table.c.model).like('mock%'), kind.in_(('MOCK', 'M7_MOCK')))
    real = and_(kind == 'BYOK_AGENT', _true_json(table.c.tokens, 'paid_request'), ~mock)
    bucket = case((mock, 'MOCK'), (real, 'REAL'), else_='UNKNOWN')
    try:
        with engine.connect() as connection:
            totals = connection.execute(_ai_metrics(connection, table, window, inp, out, cached)).mappings().one()
            status_rows = connection.execute(
                select(table.c.status, func.count()).where(window).group_by(table.c.status)).all()
            provenance_rows = connection.execute(
                select(bucket.label('bucket'), table.c.status, func.count()).where(window)
                .group_by(bucket, table.c.status)).all()
            model_rows = connection.execute(
                _ai_metrics(connection, table, window, inp, out, cached)
                .add_columns(table.c.model.label('model'), bucket.label('bucket'))
                .group_by(table.c.model, bucket)).mappings().all()
            daily_rows = connection.execute(
                select(func.date(table.c.created_at), bucket, func.count()).where(window)
                .group_by(func.date(table.c.created_at), bucket)).all()
    except SQLAlchemyError:
        return dict(block, reason='M7 账本查询失败')

    def group(status_counts):
        counts = {'success': 0, 'failed': 0, 'interrupted': 0, 'timeout': 0, 'unknown': 0}
        for status, count in status_counts.items():
            counts[ai_status_group(status)] += int(count)
        calls = sum(counts.values())
        counts['calls'] = calls
        counts['success_rate'] = round(counts['success'] / calls, 6) if calls else None
        return counts

    totals_group = group({status: count for status, count in status_rows})
    totals_group['status'] = 'available'
    provenance_map = defaultdict(dict)
    for source, status, count in provenance_rows:
        provenance_map[source][status] = int(count)
    provenance = []
    for source in ('REAL', 'MOCK', 'UNKNOWN'):
        entry = group(provenance_map.get(source, {}))
        entry['source'] = source
        provenance.append(entry)

    def metric_summary(row):
        calls = int(row['calls'] or 0)
        input_reported = int(row['input_reported'] or 0)
        output_reported = int(row['output_reported'] or 0)
        return {
            'calls': calls,
            'known_input_tokens': int(row['known_input'] or 0) if input_reported or not calls else None,
            'known_output_tokens': int(row['known_output'] or 0) if output_reported or not calls else None,
            'unknown_usage_calls': int(row['unknown_calls'] or 0),
            'cached_reported_calls': int(row['cached_reported'] or 0),
            'known_cached_input_tokens': int(row['known_cached'] or 0)
                                          if row['cached_reported'] else None,
        }

    models = []
    for row in model_rows:
        summary = metric_summary(row)
        summary.update(model=row['model'], source=row['bucket'],
                       success=int(row['success'] or 0),
                       success_rate=(round(int(row['success'] or 0) / summary['calls'], 6)
                                     if summary['calls'] else None))
        models.append(summary)
    models.sort(key=lambda item: (-item['calls'], str(item['model'])))

    days = [(start + timedelta(days=offset)).date().isoformat()
            for offset in range((end.date() - start.date()).days + 1)]
    daily_map = {day: defaultdict(int) for day in days}
    for day, source, count in daily_rows:
        key = str(day)[:10]
        if key in daily_map:
            daily_map[key][source] += int(count)
    daily = [{'date': day,
              'calls': sum(daily_map[day].values()),
              'real': daily_map[day].get('REAL', 0),
              'mock': daily_map[day].get('MOCK', 0),
              'unknown_provenance': daily_map[day].get('UNKNOWN', 0)}
             for day in days]

    summary = metric_summary(totals)
    calls = summary['calls']
    unknown_usage = summary['unknown_usage_calls']
    known_total = (None if summary['known_input_tokens'] is None or summary['known_output_tokens'] is None
                   else summary['known_input_tokens'] + summary['known_output_tokens'])
    if calls == 0:
        usage_status = 'not_called'
    elif unknown_usage:
        usage_status = 'partial'
    else:
        usage_status = 'complete'
    tokens = {'status': usage_status, 'known_input_tokens': summary['known_input_tokens'],
              'known_output_tokens': summary['known_output_tokens'],
              'known_total_tokens': known_total, 'unknown_usage_calls': unknown_usage,
              'definition': 'known_* 为已上报小计；未上报的请求不补 0，未知用量单独计数'}
    cached_input = {'status': 'available' if summary['cached_reported_calls'] else 'not_instrumented',
                    'tokens': summary['known_cached_input_tokens'],
                    'reported_calls': summary['cached_reported_calls'],
                    'definition': '缓存命中输入（账本记录时给出，是输入 Token 的子集）'}
    return dict(block, status='available', sample_size={'calls': calls},
                totals=totals_group, provenance=provenance, daily=daily, models=models,
                tokens=tokens, cached_input=cached_input)


def recommendation_tracking_health(connection, feedback, now):
    from catalog.recommendation_feedback import tracking_counts
    block = {'status': 'unknown', 'source': 'recommendation_impressions', 'sample_size': None,
             'as_of': _stamp(now), 'last_impression_at': None, 'hours_since_last_impression': None,
             'impressions_7d': None, 'impressions_24h': None,
             'rule': ('%d 天窗口有效曝光 >=%d 且最近一条 <=%dh 判 normal；%d 天窗口 0 条但历史有记录判 abnormal（断档）；'
                      '1–%d 条判 observing；从未有记录判 not_instrumented；查询失败 unknown'
                      % (RECOMMENDATION_GAP_WINDOW_DAYS, RECOMMENDATION_GAP_MIN_IMPRESSIONS,
                         RECOMMENDATION_GAP_STALE_HOURS, RECOMMENDATION_GAP_WINDOW_DAYS,
                         RECOMMENDATION_GAP_MIN_IMPRESSIONS - 1)),
             'definition': '曝光断档表示采集链路可能失效；阈值固定且公开'}
    if feedback is None:
        return dict(block, status='not_instrumented', reason='推荐曝光表未配置')
    try:
        counts = tracking_counts(connection, feedback[0], now,
                                 window_days=RECOMMENDATION_GAP_WINDOW_DAYS)
    except SQLAlchemyError:
        return dict(block, reason='推荐曝光表查询失败')
    latest = counts['last_impression_at']
    hours = (round((now - latest).total_seconds() / 3600, 2) if latest else None)
    if latest is None:
        status = 'not_instrumented'
        reason = '尚无任何有效曝光记录'
    elif counts['impressions_7d'] == 0:
        status = 'abnormal'
        reason = '%d 天窗口无有效曝光，采集可能断档' % RECOMMENDATION_GAP_WINDOW_DAYS
    elif counts['impressions_7d'] < RECOMMENDATION_GAP_MIN_IMPRESSIONS:
        status = 'observing'
        reason = '样本低于 %d 条，仅观察' % RECOMMENDATION_GAP_MIN_IMPRESSIONS
    elif hours > RECOMMENDATION_GAP_STALE_HOURS:
        status = 'abnormal'
        reason = '最近有效曝光已超过 %d 小时' % RECOMMENDATION_GAP_STALE_HOURS
    else:
        status = 'normal'
        reason = None
    return dict(block, status=status,
                sample_size=counts['impressions_7d'],
                last_impression_at=(_stamp(latest) if latest else None),
                hours_since_last_impression=hours,
                impressions_7d=counts['impressions_7d'],
                impressions_24h=counts['impressions_24h'], reason=reason)


def ai_failure_health(app, now, window_days=AI_FAILURE_WINDOW_DAYS):
    block = {'status': 'unknown', 'source': 'M7 usage 账本', 'sample_size': None,
             'as_of': _stamp(now), 'calls': None, 'failed': None, 'failure_rate': None,
             'rule': ('%d 天窗口：调用 <%d 判 observing；失败率 >=%d%% 判 abnormal；否则 normal；'
                      '账本未配置判 not_instrumented；查询失败 unknown'
                      % (window_days, AI_FAILURE_MIN_CALLS, int(AI_FAILURE_RATE_THRESHOLD * 100))),
             'definition': '只看已派发调用（排除 NOT_DISPATCHED）；失败状态=FAILED/ERROR'}
    engine = app.extensions.get('m7_engine')
    ledger = app.extensions.get('m7_ledger')
    if engine is None or ledger is None:
        return dict(block, status='not_instrumented', reason='M7 账本未配置')
    table = ledger.md.tables['usage']
    window = and_(table.c.created_at >= now - timedelta(days=window_days),
                  table.c.created_at < now, table.c.status != 'NOT_DISPATCHED')
    try:
        with engine.connect() as connection:
            calls = int(connection.scalar(select(func.count()).select_from(table).where(window)) or 0)
            failed = int(connection.scalar(
                select(func.count()).select_from(table)
                .where(window, table.c.status.in_(tuple(AI_FAILED_STATUSES)))) or 0)
    except SQLAlchemyError:
        return dict(block, reason='M7 账本查询失败')
    rate = round(failed / calls, 6) if calls else None
    if calls < AI_FAILURE_MIN_CALLS:
        status, reason = 'observing', '样本低于 %d 次调用，仅观察' % AI_FAILURE_MIN_CALLS
    elif rate >= AI_FAILURE_RATE_THRESHOLD:
        status, reason = 'abnormal', '失败率达到公开阈值 %d%%' % int(AI_FAILURE_RATE_THRESHOLD * 100)
    else:
        status, reason = 'normal', None
    return dict(block, status=status, sample_size=calls, calls=calls, failed=failed,
                failure_rate=rate, reason=reason)


def health_report(app, now=None):
    now = now or datetime.utcnow()
    report = {'stats': generation_state(now)}
    feedback = None
    try:
        feedback = feedback_tables(app)
    except ValueError:
        feedback = None
    if feedback is None:
        report['recommendation_tracking'] = dict(
            recommendation_tracking_health(None, None, now))
    else:
        with app.extensions['auth_engine'].connect() as connection:
            report['recommendation_tracking'] = recommendation_tracking_health(
                connection, feedback, now)
    report['ai_failures'] = ai_failure_health(app, now)
    return report


def metrics_definitions(now=None):
    now = now or datetime.utcnow()
    constants = {
        'tmdb_min_votes': TMDB_MIN_VOTES,
        'tmdb_rating_method': 'simple_mean',
        'exposure_retention_days': EXPOSURE_RETENTION_DAYS,
        'effective_exposure': 'board_version=visible-v2：卡片至少 50% 进入视口持续 1 秒，或用户主动点击',
        'active_user': '窗口内至少产生一条电影行为事件的不同账户',
        'high_rating_threshold': HIGH_RATING_MIN,
        'high_rating_scale': HIGH_RATING_SCALE,
        'interest_min_pairs': MIN_INTEREST_PAIRS,
        'interest_min_impressions': MIN_INTEREST_IMPRESSIONS,
        'suppression_min_users': MIN_PREFERENCE_USERS,
        'ai_failure_rate_threshold': AI_FAILURE_RATE_THRESHOLD,
        'ai_failure_min_calls': AI_FAILURE_MIN_CALLS,
        'recommendation_gap_window_days': RECOMMENDATION_GAP_WINDOW_DAYS,
        'recommendation_gap_stale_hours': RECOMMENDATION_GAP_STALE_HOURS,
        'recommendation_gap_min_impressions': RECOMMENDATION_GAP_MIN_IMPRESSIONS,
        'windows': [7, 30, 90],
        'timezone': 'UTC',
    }
    definitions = [
        {'key': 'movies.active', 'title': '在架电影数',
         'definition': "movies.catalog_status='active' 的唯一 movie_id 数", 'source': 'movies',
         'unit': 'movie', 'status': 'available'},
        {'key': 'catalog.countries', 'title': '制作国家覆盖',
         'definition': '同一电影可多国；每国按 movie_id 去重；优先 TMDB 来源，否则最新 legacy_movies2；无记录计未知；占比之和可超过 100%',
         'source': 'movie_source_records.metadata_json', 'unit': 'movie', 'status': 'available'},
        {'key': 'catalog.genres', 'title': '电影类型',
         'definition': '多标签计数，同一影片在同一类型内仅计一次；类型总和可超过片数',
         'source': 'movie_genres/genres', 'unit': 'movie', 'status': 'available'},
        {'key': 'ratings.source', 'title': '评分来源分离',
         'definition': 'TMDB vote_average（vote_count>=%d，简单平均）、豆瓣评分、站内评分分开，不混用' % TMDB_MIN_VOTES,
         'source': 'movie_ratings_summary / TMDB 补采', 'unit': 'score', 'status': 'available'},
        {'key': 'users.total', 'title': '注册账户总量',
         'definition': 'auth_users 全部账户数（含停用），与窗口无关', 'source': 'auth_users',
         'unit': 'account', 'status': 'available'},
        {'key': 'users.active_window', 'title': '窗口行为活跃账户',
         'definition': '窗口内至少一条电影行为事件的不同账户；不是登录 DAU/MAU',
         'source': 'user_movie_events', 'unit': 'account', 'status': 'available'},
        {'key': 'events.total', 'title': '行为事件数',
         'definition': '窗口内事件条数（含设置与撤销）；与去重账户数、当前状态数独立',
         'source': 'user_movie_events', 'unit': 'event', 'status': 'available'},
        {'key': 'states.current', 'title': '当前状态计数',
         'definition': '当前收藏/想看/已看/评分/不感兴趣的在架影片 (user, movie) 状态数，不从事件推算',
         'source': 'user_movie_states', 'unit': 'pair', 'status': 'available'},
        {'key': 'preference.signals', 'title': '偏好信号',
         'definition': '收藏/想看=JSON 布尔 true；高评分=rating>=%d（%d 分制）；按用户去重与按用户-电影对分开；已看不视为喜欢'
                       % (HIGH_RATING_MIN, HIGH_RATING_SCALE),
         'source': 'user_movie_states', 'unit': 'pair', 'status': 'available'},
        {'key': 'recommendation.impressions', 'title': '有效曝光数',
         'definition': 'board_version=visible-v2 的曝光条数（≥50% 视口 1 秒或主动点击）',
         'source': 'recommendation_impressions', 'unit': 'impression', 'status': 'available'},
        {'key': 'recommendation.clicks', 'title': '去重有效点击',
         'definition': '与有效曝光关联、clicked_at>=shown_at 的 (request_id, position) 去重点击',
         'source': 'recommendation_clicks', 'unit': 'click', 'status': 'available'},
        {'key': 'recommendation.ctr', 'title': '推荐 CTR',
         'definition': '有效点击/有效曝光；运营指标，不是算法准确率', 'source': '推荐曝光/点击',
         'unit': 'ratio', 'status': 'available'},
        {'key': 'recommendation.retention', 'title': '推荐记录保留期',
         'definition': '曝光与点击记录默认保留 %d 天' % EXPOSURE_RETENTION_DAYS,
         'source': 'ops/m9/loop_d_schema.sql', 'unit': 'day', 'status': 'available'},
        {'key': 'interest.match', 'title': '兴趣匹配',
         'definition': ('供给=在架影片按类别去重；兴趣=窗口内 favorite/want/rating>=%d 的去重 (user,movie) 对；'
                        '曝光/点击=有效曝光 cohort；类别样本 <(%d 对, %d 曝光) 标 insufficient_sample；只做描述性对比'
                        % (HIGH_RATING_MIN, MIN_INTEREST_PAIRS, MIN_INTEREST_IMPRESSIONS)),
         'source': 'movies/genres + user_movie_events + 推荐曝光', 'unit': 'ratio', 'status': 'available'},
        {'key': 'ai.provenance', 'title': 'AI 调用来源',
         'definition': ('REAL=kind=BYOK_AGENT 且 paid_request=true 且模型非 mock；MOCK=模型名 mock* 或 kind∈{MOCK,M7_MOCK}；'
                        '其余 UNKNOWN，未知不当作真实'),
         'source': 'M7 usage 账本', 'unit': 'call', 'status': 'available'},
        {'key': 'ai.tokens', 'title': 'AI Token',
         'definition': '已知输入/输出 Token 为已上报小计；未知用量单独计数，不计 0；缓存命中输入是输入子集',
         'source': 'M7 usage 账本 tokens', 'unit': 'token', 'status': 'available'},
        {'key': 'ai.latency', 'title': 'AI 延迟',
         'definition': '账本无请求计时字段，不可计算', 'source': 'M7 usage 账本', 'unit': 'ms',
         'status': 'not_instrumented'},
        {'key': 'health.recommendation_gap', 'title': '曝光断档检测',
         'definition': ('%d 天窗口有效曝光 >=%d 且最近 <=%dh 判 normal；0 条但历史有记录判 abnormal；1–%d 条判 observing'
                        % (RECOMMENDATION_GAP_WINDOW_DAYS, RECOMMENDATION_GAP_MIN_IMPRESSIONS,
                           RECOMMENDATION_GAP_STALE_HOURS, RECOMMENDATION_GAP_MIN_IMPRESSIONS - 1)),
         'source': 'recommendation_impressions', 'unit': 'status', 'status': 'available'},
        {'key': 'health.ai_failures', 'title': 'AI 失败激增',
         'definition': '%d 天窗口调用 <%d 判 observing；失败率 >=%d%% 判 abnormal'
                       % (AI_FAILURE_WINDOW_DAYS, AI_FAILURE_MIN_CALLS, int(AI_FAILURE_RATE_THRESHOLD * 100)),
         'source': 'M7 usage 账本', 'unit': 'status', 'status': 'available'},
    ]
    return {'version': METRICS_VERSION, 'as_of': _stamp(now), 'status': 'available',
            'source': '服务端常量与实现口径', 'sample_size': None, 'timezone': 'UTC',
            'windows': [7, 30, 90], 'constants': constants, 'definitions': definitions,
            'notes': ['口径与后端实现同源；前端只读展示，不允许在线修改全站算法或生产配置。',
                      '未知值不补 0；样本不足时展示状态而非结论。']}


def events_page(connection, repository, spec, now=None):
    """Page 12 event feed with pagination/filtering; payload is never returned."""
    now = now or datetime.utcnow()
    events = repository.tables['events']
    movies = CatalogBase.metadata.tables['movies']
    conditions = []
    if spec['start'] is not None:
        conditions.append(events.c.created_at >= spec['start'])
    if spec['end'] is not None:
        conditions.append(events.c.created_at < spec['end'])
    if spec['kind'] is not None:
        conditions.append(func.json_unquote(func.json_extract(events.c.payload, '$.kind')) == spec['kind'])
    if spec['movie_id'] is not None:
        conditions.append(events.c.movie_id == spec['movie_id'])
    statement = (select(events.c.created_at, User.username, movies.c.title,
                        events.c.movie_id, events.c.payload)
                 .join(User, User.id == events.c.user_id)
                 .join(movies, movies.c.movie_id == events.c.movie_id))
    count_statement = (select(func.count()).select_from(events)
                       .join(User, User.id == events.c.user_id)
                       .join(movies, movies.c.movie_id == events.c.movie_id))
    if conditions:
        statement = statement.where(*conditions)
        count_statement = count_statement.where(*conditions)
    total = int(connection.scalar(count_statement) or 0)
    rows = connection.execute(statement.order_by(events.c.seq.desc())
                              .offset(spec['offset']).limit(spec['limit'])).all()
    items = []
    for created, username, title, movie_id, payload in rows:
        payload = payload if isinstance(payload, dict) else {}
        items.append({'at': created.isoformat() if created else None,  # legacy format kept
                      'user': username, 'title': title,
                      'movie_id': int(movie_id) if movie_id is not None else None,
                      'kind': payload.get('kind'), 'action': payload.get('action')})
    return {'items': items, 'total': total, 'limit': spec['limit'], 'offset': spec['offset'],
            'has_more': spec['offset'] + len(items) < total,
            'filters': {'from': spec['start'].isoformat() + 'Z' if spec['start'] else None,
                        'to': spec['end'].isoformat() + 'Z' if spec['end'] else None,
                        'kind': spec['kind'], 'movie_id': spec['movie_id']},
            'status': 'available', 'source': 'user_movie_events',
            'sample_size': len(items), 'as_of': _stamp(now),
            'definition': '不含事件 payload；total 为同过滤条件总数；按 seq 倒序'}
