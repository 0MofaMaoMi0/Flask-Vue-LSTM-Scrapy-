"""面向新 UI 的读取接口：分页列表、影片详情、评论分页、统计聚合。

旧的 `/movie/get`（固定 8 条）、`/movie/getComments`（一次全返）保持兼容不动，
新 UI 直接用这里的接口即可。
"""

import math
from datetime import datetime

from flask import Blueprint, request

from api import statsData
from base.code import ResponseCode
from base.core import db
from base.response import ResMsg
from models.comments import Comment, comment_schema
from models.movie import Movie, movie_schema, serialize_movies


catalogBp = Blueprint('catalog', __name__)
statsBp = Blueprint('stats', __name__)

LIST_SORTS = {
    'score': (Movie.douban_score, 'desc'),
    'votes': (Movie.douban_votes, 'desc'),
    'year': (Movie.year, 'desc'),
    'name': (Movie.name, 'asc'),
}
COMMENT_LABELS = ('positive', 'negative')


def _error(code, msg):
    res = ResMsg()
    res.update(code=code, msg=msg)
    return res.data


def _int_arg(name, default, minimum, maximum):
    raw = request.args.get(name)
    if raw in (None, ''):
        return default, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, _error(ResponseCode.INVALID_PARAMETER, '{} 必须是整数'.format(name))
    if value < minimum or value > maximum:
        return None, _error(
            ResponseCode.INVALID_PARAMETER,
            '{} 需在 {}~{} 之间'.format(name, minimum, maximum),
        )
    return value, None


def _page_args(default_size=24, max_size=60):
    page, error = _int_arg('page', 1, 1, 100000)
    if error:
        return None, None, error
    size, error = _int_arg('size', default_size, 1, max_size)
    if error:
        return None, None, error
    return page, size, None


def _paged_payload(items, total, page, size, **extra):
    payload = {
        'items': items,
        'total': total,
        'page': page,
        'size': size,
        'pages': int(math.ceil(total / size)) if size else 0,
    }
    payload.update(extra)
    return payload


@catalogBp.route('/list', methods=['GET'])
def movie_list():
    """分页 + 筛选的影片列表。

    查询参数：
      page(1..) size(1..60) q(片名模糊) genre(类型包含) region(地区包含)
      year_from / year_to(闭区间) sort(score|votes|year|name)
    """
    page, size, error = _page_args()
    if error:
        return error

    sort_key = (request.args.get('sort') or 'score').strip()
    if sort_key not in LIST_SORTS:
        return _error(ResponseCode.INVALID_PARAMETER,
                      'sort 只能是 {}'.format('/'.join(LIST_SORTS)))

    year_from, error = _int_arg('year_from', None, 0, 9999)
    if error:
        return error
    year_to, error = _int_arg('year_to', None, 0, 9999)
    if error:
        return error
    if year_from is not None and year_to is not None and year_from > year_to:
        return _error(ResponseCode.INVALID_PARAMETER, 'year_from 不能大于 year_to')

    query = db.session.query(Movie)
    keyword = (request.args.get('q') or request.args.get('keyword') or '').strip()
    if keyword:
        query = query.filter(Movie.name.like('%' + keyword + '%'))
    genre = (request.args.get('genre') or '').strip()
    if genre:
        query = query.filter(Movie.genres.like('%' + genre + '%'))
    region = (request.args.get('region') or '').strip()
    if region:
        query = query.filter(Movie.regions.like('%' + region + '%'))
    if year_from is not None:
        query = query.filter(Movie.year >= year_from)
    if year_to is not None:
        query = query.filter(Movie.year <= year_to)

    total = query.count()
    column, direction = LIST_SORTS[sort_key]
    order = column.desc() if direction == 'desc' else column.asc()
    rows = query.order_by(order, Movie.id.asc()).offset(
        (page - 1) * size).limit(size).all()

    res = ResMsg()
    res.update(code=ResponseCode.SUCCESS,
               data=_paged_payload(serialize_movies(rows), total, page, size))
    return res.data


@catalogBp.route('/detail/<douban_id>', methods=['GET'])
def movie_detail(douban_id):
    """单部影片详情：完整字段 + 评论数 + 同类型推荐。"""
    if not str(douban_id).isdigit():
        return _error(ResponseCode.INVALID_PARAMETER, 'douban_id 必须是数字')
    movie = db.session.query(Movie).filter(
        Movie.douban_id == douban_id).first()
    if movie is None:
        return _error(ResponseCode.NO_RESOURCE_FOUND, '未找到该影片')

    data = serialize_movies([movie])[0]
    data['comment_count'] = db.session.query(Comment).filter(
        Comment.douban_id == douban_id).count()

    related = []
    first_genre = (movie.genres or '').split('/')[0].strip()
    if first_genre:
        related = db.session.query(Movie).filter(
            Movie.genres.like('%' + first_genre + '%'),
            Movie.id != movie.id,
        ).order_by(Movie.douban_score.desc(), Movie.id.asc()).limit(6).all()

    data['related'] = serialize_movies(related)
    res = ResMsg()
    res.update(code=ResponseCode.SUCCESS, data=data)
    return res.data


@catalogBp.route('/comments', methods=['GET'])
def movie_comments():
    """评论分页 + 情感筛选。

    查询参数：douban_id(必填) page size label(positive|negative) sort(votes|time)
    """
    douban_id = (request.args.get('douban_id') or '').strip()
    if not douban_id.isdigit():
        return _error(ResponseCode.INVALID_PARAMETER, 'douban_id 必须是数字')

    page, size, error = _page_args(default_size=20, max_size=100)
    if error:
        return error

    label = (request.args.get('label') or '').strip()
    if label and label not in COMMENT_LABELS:
        return _error(ResponseCode.INVALID_PARAMETER,
                      'label 只能是 positive 或 negative')

    sort_key = (request.args.get('sort') or 'votes').strip()
    if sort_key not in ('votes', 'time'):
        return _error(ResponseCode.INVALID_PARAMETER, 'sort 只能是 votes 或 time')

    base = db.session.query(Comment).filter(Comment.douban_id == douban_id)
    total = base.count()
    if total == 0:
        res = ResMsg()
        res.update(code=ResponseCode.SUCCESS,
                   data=_paged_payload([], 0, page, size,
                                       stats={'positive': 0, 'negative': 0}))
        return res.data

    positive = base.filter(Comment.label == 'positive').count()
    negative = base.filter(Comment.label == 'negative').count()

    query = base
    if label:
        query = query.filter(Comment.label == label)
    filtered_total = query.count()
    order = (Comment.votes.desc() if sort_key == 'votes'
             else Comment.comment_time.desc())
    rows = query.order_by(order, Comment.id.asc()).offset(
        (page - 1) * size).limit(size).all()

    res = ResMsg()
    res.update(code=ResponseCode.SUCCESS, data=_paged_payload(
        comment_schema.dump(rows), filtered_total, page, size,
        stats={'positive': positive, 'negative': negative},
    ))
    return res.data


@statsBp.route('/overview', methods=['GET'])
def stats_overview():
    """一次返回全部图表数据，替代前端并发请求 7 个接口。"""
    res = ResMsg()
    data = statsData.overview()
    data['generated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    res.update(code=ResponseCode.SUCCESS, data=data)
    return res.data