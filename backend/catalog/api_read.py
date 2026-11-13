"""Optional read Blueprint. Explicit executor only; never registered automatically."""
from datetime import date,datetime
from decimal import Decimal
from itertools import islice
from urllib.parse import quote
from flask import Blueprint,request,jsonify
from sqlalchemy.sql.selectable import Select
from . import read_queries as queries
from .media_urls import asset_url

class InputError(ValueError):
    pass

RELATION_LIMIT=50
MOVIE_FIELDS=('movie_id','title','original_title','release_date','release_year','runtime_minutes','original_language','overview','is_adult','created_at','updated_at')
RELATED={'aliases':(queries.movie_aliases,('title','language','source')),'genres':(queries.movie_genres,('genre_id','name_zh','name_en')),'external_ids':(queries.movie_external_ids,('source','external_id')),'ratings':(queries.movie_ratings,('source','score_value','score_scale','vote_count','observed_at')),'assets':(queries.movie_assets,('asset_type','source','source_path','language','width','height','is_primary','license_status')),'credits':(queries.movie_credits,('credit_id','source','role_type','job','character_name','sort_order','person_id','name','profile_path'))}

def value(v):
    if isinstance(v,(datetime,date)):return v.isoformat()
    if isinstance(v,Decimal):
        if not v.is_finite():raise ValueError('Nonfinite value')
        return str(v)
    if v is None or type(v) in (str,int,bool):return v
    raise ValueError('Unsupported DTO value')

def dto(row,fields):return {k:value(row[k]) for k in fields if k in row}

def create_read_blueprint(executor=None,personal_ids=None):
    bp=Blueprint('catalog_read',__name__)
    def reply(data=None,status=200,code='OK'):
        return jsonify({'code':code,'data':data}),status
    def arguments(allowed):
        if set(request.args)-set(allowed) or any(len(request.args.getlist(k))!=1 for k in request.args):raise InputError('Invalid parameters')
        return request.args
    def integer(args,key,default=None):
        if key not in args:return default
        raw=args[key]
        if not raw or not raw.isascii() or not raw.isdecimal() or len(raw)>19:raise InputError('Invalid integer')
        return int(raw)
    def build(builder,*args,**kwargs):
        try:return builder(*args,**kwargs)
        except ValueError:raise InputError('Invalid query input') from None
    def execute(stmt,maximum):
        if not callable(executor):raise LookupError('Executor unavailable')
        if not isinstance(stmt,Select):raise TypeError('Only Select')
        return list(islice(iter(executor(stmt)),maximum))
    def guarded(fn):
        def call(*a,**kw):
            try:return fn(*a,**kw)
            except PermissionError:return reply(status=401,code='AUTHENTICATION_REQUIRED')
            except InputError:return reply(status=400,code='INVALID_INPUT')
            except LookupError:return reply(status=503,code='UNAVAILABLE')
            except Exception:return reply(status=500,code='INTERNAL_ERROR')
        call.__name__=fn.__name__;return call
    @bp.get('/api/catalog/movies')
    @guarded
    def movies():
        a=arguments(('keyword','year_from','year_to','genre_id','sort','limit','offset','state'))
        limit=integer(a,'limit',20);offset=integer(a,'offset',0)
        scope=None
        if 'state' in a:
            if a['state'] not in ('favorite','want','watched') or not callable(personal_ids):raise InputError('Personal filter')
            scope=personal_ids(a['state'])
        stmt=build(queries.movie_list,movie_ids=scope,keyword=a.get('keyword'),year_from=integer(a,'year_from'),year_to=integer(a,'year_to'),genre_id=integer(a,'genre_id'),sort=a.get('sort','updated'),limit=limit,offset=offset)
        rows=execute(stmt,limit)
        items=[dto(x,MOVIE_FIELDS) for x in rows if x.get('catalog_status')=='active']
        ids=[i['movie_id'] for i in items]
        posters={};fallbacks={}
        if ids:
            for art in execute(queries.movie_posters(ids),max(1,len(ids)*4)):
                url=asset_url(art)
                if not url:continue
                if art['source']=='tmdb':fallbacks.setdefault(art['movie_id'],url)
                else:posters.setdefault(art['movie_id'],url)
            for item in items:
                item['poster']=posters.get(item['movie_id']) or fallbacks.get(item['movie_id'])
                item['poster_fallback']=fallbacks.get(item['movie_id']) if posters.get(item['movie_id']) else None
        genre_map={};rating_map={}
        if ids:
            for row in execute(queries.movie_list_genres(ids),len(ids)*RELATION_LIMIT):genre_map.setdefault(row['movie_id'],[]).append(dto(row,RELATED['genres'][1]))
            for row in execute(queries.movie_list_ratings(ids),len(ids)*RELATION_LIMIT):rating_map.setdefault(row['movie_id'],[]).append(dto(row,RELATED['ratings'][1]))
        for item in items:
            item['genres']=genre_map.get(item['movie_id'],[]);item['ratings']=rating_map.get(item['movie_id'],[])
        return reply({'items':items,'limit':limit,'offset':offset})
    @bp.get('/api/catalog/movies/<int:movie_id>')
    @guarded
    def detail(movie_id):
        arguments(())
        rows=execute(build(queries.movie_detail,movie_id),1)
        if not rows or rows[0].get('catalog_status')!='active':return reply(status=404,code='NOT_FOUND')
        data=dto(rows[0],MOVIE_FIELDS)
        for name,(builder,fields) in RELATED.items():
            maximum=RELATION_LIMIT+1 if name=='credits' else RELATION_LIMIT
            related_rows=execute(builder(movie_id).limit(maximum),maximum)
            data[name]=[dto(x,fields) for x in related_rows[:RELATION_LIMIT]]
            if name=='assets':
                for asset in data[name]:asset['url']=asset_url(asset)
            if name=='credits':data['credits_has_more']=len(related_rows)>RELATION_LIMIT
        return reply(data)
    @bp.get('/api/catalog/movies/<int:movie_id>/credits')
    @guarded
    def credits_page(movie_id):
        a=arguments(('limit','offset'));limit=integer(a,'limit',RELATION_LIMIT);offset=integer(a,'offset',0)
        if not 1<=limit<=queries.MAX_LIMIT or not 0<=offset<=queries.MAX_OFFSET:raise InputError('Invalid pagination')
        movie_rows=execute(build(queries.movie_detail,movie_id),1)
        if not movie_rows or movie_rows[0].get('catalog_status')!='active':return reply(status=404,code='NOT_FOUND')
        rows=execute(queries.movie_credits(movie_id).limit(limit+1).offset(offset),limit+1)
        return reply({'items':[dto(row,RELATED['credits'][1]) for row in rows[:limit]],'limit':limit,'offset':offset,'has_more':len(rows)>limit,'next_offset':offset+limit if len(rows)>limit else None})
    @bp.get('/api/catalog/lookup')
    @guarded
    def lookup():
        a=arguments(('source','external_id'));rows=execute(build(queries.movie_by_external_id,a.get('source'),a.get('external_id')),1)
        if not rows or rows[0].get('catalog_status')!='active':return reply(status=404,code='NOT_FOUND')
        return reply(dto(rows[0],MOVIE_FIELDS))
    @bp.errorhandler(405)
    def wrong_method(error):return reply(status=405,code='METHOD_NOT_ALLOWED')
    return bp
