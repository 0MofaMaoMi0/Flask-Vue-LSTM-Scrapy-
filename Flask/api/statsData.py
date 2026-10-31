"""统计图表的取数逻辑。

原先每个 `/movie/getChart*` 接口各写一遍查询，`/stats/overview` 需要复用同一批
数据，因此抽到这里。返回结构与旧接口保持一致，避免影响现有前端。
"""

from sqlalchemy import func

from base.core import db
from models.movie import Movie


YEAR_RANGES = [
    ('1900', '1950'), ('1950', '1960'), ('1960', '1970'), ('1970', '1980'),
    ('1980', '1990'), ('1990', '2000'), ('2000', '2010'), ('2010', '2020'),
    ('2020', '2030'),
]
AREA_RANGES = [('1900', '1970'), ('1970', '1990'), ('1990', '2000'),
               ('2000', '2010'), ('2010', '2020')]
TYPE_NAMES = [
    '惊悚', '古装', '武侠', '冒险', '喜剧', '恐怖', '犯罪', '历史', '歌舞',
    '纪录片', '动画', '科幻', '西部', '战争', '家庭', '传记', '悬疑',
    '儿童', '灾难', '奇幻', '剧情', '同性', '动作', '运动', '音乐', '情色', '爱情',
]
NATION_NAMES = [
    '摩纳哥', '西班牙', '印度', '比利时', '塞浦路斯', '英国', '韩国', '希腊',
    '奥地利', '意大利', '德国', '泰国', '澳大利亚', '中国台湾', '巴西', '中国香港',
    '墨西哥', '加拿大', '匈牙利', '中国大陆', '瑞典', '新西兰', '卡塔尔', '捷克',
    '瑞士', '南非', '法国', '伊朗', '黎巴嫩', '阿联酋', '日本', '约旦', '爱尔兰',
    '波兰', '丹麦', '美国', '阿根廷', '荷兰',
]
RATE_TYPES = ['剧情', '爱情', '喜剧', '冒险', '犯罪']
TIMELINE_YEARS = list(range(2000, 2021))
TIMELINE_NATIONS = [
    '美国', '英国', '日本', '中国香港', '中国大陆', '法国', '德国', '韩国',
    '意大利', '加拿大', '中国台湾', '澳大利亚', '西班牙', '印度', '瑞士', '新西兰',
]


def _count_by_range(lo, hi, genre=None):
    query = db.session.query(Movie).filter(Movie.year >= lo, Movie.year < hi)
    if genre:
        query = query.filter(Movie.genres.like('%' + genre + '%'))
    return query.count()


def chart1_data():
    """按年代分段：全部 / 科幻 / 动作 / 爱情 / 喜剧。"""
    buckets = {'all': [], 'kh': [], 'dz': [], 'aq': [], 'xj': []}
    for lo, hi in YEAR_RANGES:
        name = lo + '-' + hi
        buckets['all'].append(dict(name=name, value=_count_by_range(lo, hi)))
        buckets['kh'].append(dict(name=name, value=_count_by_range(lo, hi, '科幻')))
        buckets['dz'].append(dict(name=name, value=_count_by_range(lo, hi, '动作')))
        buckets['aq'].append(dict(name=name, value=_count_by_range(lo, hi, '爱情')))
        buckets['xj'].append(dict(name=name, value=_count_by_range(lo, hi, '喜剧')))
    return buckets


def area_data():
    """科幻片的分段产量（字段名沿用 kh）。"""
    return {'kh': [
        dict(name=lo + '-' + hi, value=_count_by_range(lo, hi, '科幻'))
        for lo, hi in AREA_RANGES
    ]}


def year_total_data():
    """按年份分组的总量，跨全部年份。"""
    rows = db.session.query(
        Movie.year.label('name'), func.count('*').label('value')
    ).group_by(Movie.year).order_by(Movie.year.asc()).all()
    return [dict(name=row[0], value=row[1]) for row in rows]


def recent_years_data(start=2001, end=2020):
    """指定年份区间每年的影片数（旧 /movie/getChart2 的形状）。"""
    data = []
    for year in range(start, end + 1):
        data.append(dict(name=year, value=db.session.query(Movie).filter(
            Movie.year == year).count()))
    return data


def type_rank_data():
    rows = [
        dict(name=name, value=db.session.query(Movie).filter(
            Movie.genres.like('%' + name + '%')).count())
        for name in TYPE_NAMES
    ]
    return {'datas': sorted(rows, key=lambda item: item['value'], reverse=True)}


def nation_rank_data():
    rows = [
        dict(name=name, value=db.session.query(Movie).filter(
            Movie.regions.like('%' + name + '%')).count())
        for name in NATION_NAMES
    ]
    return {'datas': sorted(rows, key=lambda item: item['value'], reverse=True)}


def type_rate_data():
    """散点图：5 个类型各自的 [年份, 评分] 点。"""
    series = []
    for genre in RATE_TYPES:
        movies = db.session.query(Movie).filter(
            Movie.genres.like('%' + genre + '%'),
            Movie.douban_score > 0,
            Movie.year > 0,
        ).all()
        series.append([[int(movie.year), float(movie.douban_score)] for movie in movies])
    return {'datas': series, 'labels': list(RATE_TYPES)}


def timeline_data():
    """2000-2020 年 × 16 个国家的产量矩阵。"""
    matrix = []
    for year in TIMELINE_YEARS:
        matrix.append([
            db.session.query(Movie).filter(
                Movie.year == year, Movie.regions.like('%' + nation + '%')
            ).count()
            for nation in TIMELINE_NATIONS
        ])
    return {'datas': matrix, 'years': TIMELINE_YEARS,
            'nations': list(TIMELINE_NATIONS)}


def overview():
    """一次返回全部图表数据，替代前端并发请求 7 个接口。"""
    return {
        'chart1': chart1_data(),
        'area': area_data(),
        'year_total': year_total_data(),
        'recent_years': recent_years_data(),
        'type_rank': type_rank_data(),
        'nation_rank': nation_rank_data(),
        'type_rate': type_rate_data(),
        'timeline': timeline_data(),
    }