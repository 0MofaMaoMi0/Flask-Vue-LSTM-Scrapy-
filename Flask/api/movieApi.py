# -*- codeing = utf-8 -*-
#

# 2/16 11:23
#
# @File :movieApi.py
# #Desc :这个是电影接口的文件

from flask import Blueprint, request

from algorithm import ItemCF, UserCF
from api import statsData
from base.code import ResponseCode, ResponseMessage
from base.core import db
from base.response import ResMsg
from models.comments import Comment, comment_schema
from models.movie import Movie, getWords, serialize_movies

# Blueprint的作用就是一个前缀，相当于前端访问下面的接口的时候地址前都会加上movie
# 如  http://localhost:5000/movie/getWordCut 这样
# 好处就是很容易区分这个接口是在哪个模块下的，逻辑很清晰
movieBp = Blueprint("movie", __name__)


def _invalid(msg):
    """参数错误统一走 40002，不再让缺参数直接 500。"""
    res = ResMsg()
    res.update(code=ResponseCode.INVALID_PARAMETER, msg=msg)
    return res.data


def _parse_user_id(raw):
    try:
        return int(raw), None
    except (TypeError, ValueError):
        return None, _invalid('userId 必须是整数')


# 分词接口，调用Jieba分词，给前端的词云图提供数据
@movieBp.route('/getWordCut', methods=["GET"])
def getWordCut():
    res = ResMsg()
    result = getWords()
    res.update(code=ResponseCode.SUCCESS, data=result)
    return res.data

# 搜索，这个搜索是给电影库用的，带了关键词这个参数
@movieBp.route('/get', methods=["GET"])
def get():
    res = ResMsg()
    keyword = request.args.get('keyword')
    if keyword is None:
        keyword = ""
    # 数据库层直接 limit，避免把全表 2700+ 行 ORM 对象都拉进内存再切片
    result = db.session.query(Movie).filter(
        Movie.name.like('%' + keyword + '%')
    ).order_by(Movie.douban_score.desc(), Movie.id.asc()).limit(8).all()
    res.update(code=ResponseCode.SUCCESS, data=serialize_movies(result))
    return res.data

# 热门--根据评分
@movieBp.route('/getHot', methods=["GET"])
def getHot():
    res = ResMsg()
    # 热门只显示 4 个，直接在数据库层 limit
    result = db.session.query(Movie).order_by(
        Movie.douban_score.desc(), Movie.id.asc()
    ).limit(4).all()
    res.update(code=ResponseCode.SUCCESS, data=serialize_movies(result))
    return res.data

# 推荐 这个只是获取评分高的科幻电影，不是真实的推荐接口
@movieBp.route('/getRec', methods=["GET"])
def getRec():
    res = ResMsg()
    result = db.session.query(Movie).filter(
        Movie.genres.like('%科幻%')
    ).order_by(Movie.douban_score.desc(), Movie.id.asc()).limit(4).all()
    res.update(code=ResponseCode.SUCCESS, data=serialize_movies(result))
    return res.data

# 按年代分段：全部 / 科幻 / 动作 / 爱情 / 喜剧
@movieBp.route('/getChart1', methods=["GET"])
def getChart1():
    res = ResMsg()
    res.update(code=ResponseCode.SUCCESS, data=statsData.chart1_data())
    return res.data

# 面积图接口（科幻片分段产量）
@movieBp.route('/getAreaChart', methods=["GET"])
def getAreaChart():
    res = ResMsg()
    res.update(code=ResponseCode.SUCCESS, data=statsData.area_data())
    return res.data

# 2001-2020 每年的影片数
@movieBp.route('/getChart2', methods=["GET"])
def getChart2():
    res = ResMsg()
    res.update(code=ResponseCode.SUCCESS, data=statsData.recent_years_data())
    return res.data

# 按年份分组的总量
@movieBp.route('/getChart3', methods=["GET"])
def getChart3():
    res = ResMsg()
    res.update(code=ResponseCode.SUCCESS, data=statsData.year_total_data())
    return res.data

# 查询各类型电影的数量，给出排名
@movieBp.route('/getTypeRank', methods=["GET"])
def getTypeRank():
    res = ResMsg()
    res.update(code=ResponseCode.SUCCESS, data=statsData.type_rank_data())
    return res.data

# 查询各个国家电影的数量，给世界地图使用
@movieBp.route('/getNationRank', methods=["GET"])
def getNationRank():
    res = ResMsg()
    res.update(code=ResponseCode.SUCCESS, data=statsData.nation_rank_data())
    return res.data

# 给出各个类型的评分，给散点图提供数据（评分分析）
@movieBp.route('/getTypeRate', methods=["GET"])
def getTypeRate():
    res = ResMsg()
    res.update(code=ResponseCode.SUCCESS, data=statsData.type_rate_data())
    return res.data

# 时间轴图：2000-2020 年 × 16 个国家的产量
@movieBp.route('/getTimeLine', methods=["GET"])
def getTimeLine():
    res = ResMsg()
    timeline = statsData.timeline_data()
    res.update(code=ResponseCode.SUCCESS, data=dict(datas=timeline['datas']))
    return res.data

# 推荐算法接口（ItemCF）
@movieBp.route('/getRecomendation', methods=["GET"])
def getRecomendation():
    userId, error = _parse_user_id(request.args.get('userId'))
    if error:
        return error
    res = ResMsg()
    rates = []
    movies = []
    for movie_id, rate in ItemCF.recommend(userId):
        movie = db.session.query(Movie).filter(Movie.id == movie_id).first()
        if movie is not None:          # 评分表里存在库里已删除的 id
            movies.append(movie)
            rates.append(rate)
    res.update(code=ResponseCode.SUCCESS,
               data=dict(datas=serialize_movies(movies), rates=rates))
    return res.data

# 推荐算法接口（UserCF）
@movieBp.route('/getRecomendation2', methods=["GET"])
def getRecomendation2():
    userId, error = _parse_user_id(request.args.get('userId'))
    if error:
        return error
    res = ResMsg()
    movies = []
    rates = []
    for movie_id, rate in UserCF.recommend(userId):
        movie = db.session.query(Movie).filter(Movie.id == movie_id).first()
        if movie is not None:
            movies.append(movie)
            rates.append(rate)
    res.update(code=ResponseCode.SUCCESS,
               data=dict(datas=serialize_movies(movies), rates=rates))
    return res.data

# 查询影评数据的接口（兼容旧前端：一次返回全部；新 UI 请用 /movie/comments 分页）
@movieBp.route('/getComments', methods=["POST"])
def getComments():
    payload = request.get_json(silent=True) or {}
    douban_id = payload.get('douban_id')
    if douban_id in (None, ''):
        return _invalid('缺少 douban_id')
    res = ResMsg()
    comments = db.session.query(Comment).filter(
        Comment.douban_id == douban_id).all()
    res.update(code=ResponseCode.SUCCESS, data=comment_schema.dump(comments))
    return res.data

# 这个函数非常关键，由于数据库的连接数是有限的，所以在操作完之后，一定要关闭db连接
# 这个方法可以在请求完之后自动关闭连接，这样就不需要每个接口里手动写关闭连接接口
@movieBp.after_request
def close_session(response):
    db.session.close()
    return response