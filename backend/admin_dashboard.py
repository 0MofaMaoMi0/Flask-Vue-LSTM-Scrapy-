"""Administrator-only dashboard aggregates. Read-only; no raw identities or AI content.

The catalog is an all-active-movies snapshot. Events/usage use UTC calendar days,
including the partial current day. Missing telemetry is never represented as zero.
"""
from datetime import datetime, timedelta

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
    SELECT s.movie_id, s.metadata_json,
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
    JOIN JSON_TABLE(r.metadata_json, '$.production_countries[*]'
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
    with app.extensions['auth_engine'].connect() as connection:
        catalog = catalog_snapshot(connection)
        behavior = behavior_snapshot(connection, app.extensions['m6_behavior'], start, end)
        users = users_snapshot(connection, start, end)
    return {'as_of': end.isoformat() + 'Z',
            'window': {'days': days, 'from': start.isoformat() + 'Z', 'to': end.isoformat() + 'Z', 'timezone': 'UTC'},
            'catalog': catalog, 'behavior': behavior, 'users': users,
            'ai': ai_snapshot(app, start, end),
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
            ]}
