"""Authenticated behavior and existing catalog/recommendation contracts on MySQL."""
from collections import Counter, defaultdict
from concurrent.futures import Future
from datetime import datetime
import json
import re
import threading
import time
from weakref import WeakKeyDictionary
from flask import Blueprint, request, jsonify
from sqlalchemy import column, inspect, select, insert, func, table
from sqlalchemy.dialects.mysql import insert as mysql_insert
from app.auth import session_row, csrf_ok
from app.models import User
from app.m6_activity import activity_tables
from . import public_catalog_cache
from .models import CatalogBase
from .recommendation import validate_event, transition, Conflict


class MySQLBehaviorRepository:
    def __init__(self,engine,config):
        self.engine=engine
        self.tables=activity_tables(config['M6_ACTIVITY_SCHEMA'],config['M6_SCHEMA'])
    def snapshot(self,user):
        states=self.tables['states']
        with self.engine.connect() as connection:
            return {str(row.movie_id):row.state for row in connection.execute(select(states.c.movie_id,states.c.state).where(states.c.user_id==user))}
    def watched_timeline(self,user):
        """Latest watched/set timestamp per movie, derived from immutable events."""
        events=self.tables['events']
        with self.engine.connect() as connection:
            rows=connection.execute(select(events.c.movie_id,events.c.payload,events.c.created_at).where(events.c.user_id==user).order_by(events.c.seq)).all()
        timeline={}
        for movie_id,payload,created_at in rows:
            if not isinstance(payload,dict) or payload.get('kind')!='watched':continue
            timeline[int(movie_id)]=created_at if payload.get('action')=='set' else None
        return timeline
    def apply(self,user,event):
        md=CatalogBase.metadata;movies=md.tables['movies'];events=self.tables['events'];states=self.tables['states']
        if type(event) is not dict or type(event.get('movie_id')) is not int:raise ValueError('Invalid event')
        with self.engine.begin() as connection:
            # User-row serialization protects event idempotency and concurrent
            # updates to all of this user's movie states across service workers.
            account=connection.execute(select(User.id).where(User.id==user,User.status=='active').with_for_update()).scalar_one_or_none()
            if account is None:raise PermissionError('Authentication required')
            mid=event['movie_id']
            active=connection.execute(select(movies.c.movie_id).where(movies.c.movie_id==mid,movies.c.catalog_status=='active')).scalar_one_or_none()
            signature=validate_event(event,{active} if active is not None else set(),source='USER_ACTION')
            previous=connection.execute(select(events).where(events.c.user_id==user,events.c.event_id==event['event_id'])).mappings().first()
            if previous:
                if json.dumps(previous['payload'],sort_keys=True,separators=(',',':'))!=signature:raise Conflict('Event id conflict')
                return {'duplicate':True,'version':previous['seq']}
            old=connection.execute(select(states.c.state).where(states.c.user_id==user,states.c.movie_id==mid)).scalar_one_or_none()
            state=dict(old or {});transition(state,event)
            result=connection.execute(insert(events).values(user_id=user,event_id=event['event_id'],movie_id=mid,payload=event,created_at=datetime.utcnow()))
            version=result.inserted_primary_key[0]
            statement=mysql_insert(states).values(user_id=user,movie_id=mid,state=state,version=version)
            connection.execute(statement.on_duplicate_key_update(state=statement.inserted.state,version=statement.inserted.version))
            return {'duplicate':False,'version':version}


_CATALOG_DATA_LOCK = threading.Lock()
_COUNTRY_ROWS_CACHE = WeakKeyDictionary()
_COUNTRY_ROWS_INFLIGHT = {}
_GLOBE_SUMMARY_CACHE = WeakKeyDictionary()
_GLOBE_SUMMARY_INFLIGHT = {}


def _cached_catalog_data(cache_map, inflight_map, engine, builder):
    ttl = public_catalog_cache._ttl()
    revision = public_catalog_cache.cache_revision(engine)
    if ttl <= 0 or engine is None:
        return builder()
    now_mono = time.monotonic()
    inflight_key = (id(engine), revision)
    with _CATALOG_DATA_LOCK:
        cached = cache_map.get(engine)
        if cached and cached[0] == revision and cached[1] > now_mono:
            return cached[2]
        pending = inflight_map.get(inflight_key)
        if pending is None:
            pending = inflight_map[inflight_key] = Future()
            owner = True
        else:
            owner = False
    if not owner:
        return pending.result()
    try:
        data = builder()
        with _CATALOG_DATA_LOCK:
            if public_catalog_cache.cache_revision(engine) == revision:
                cache_map[engine] = (revision, time.monotonic() + ttl, data)
        pending.set_result(data)
        return data
    except BaseException as error:
        pending.set_exception(error)
        raise
    finally:
        with _CATALOG_DATA_LOCK:
            inflight_map.pop(inflight_key, None)


def _clone_country_rows(items):
    return [{'movie_id': row['movie_id'], 'countries': [dict(c) for c in row['countries']]} for row in items]


def _build_production_country_rows(engine):
    from . import current_state
    from .recommendation_data import _ordered_source_snapshots
    md=CatalogBase.metadata
    movies=md.tables['movies']
    records=md.tables['movie_source_records']
    external=md.tables['movie_external_ids']
    items=[]
    with engine.connect() as connection:
        active=[int(row[0]) for row in connection.execute(
            select(movies.c.movie_id).where(movies.c.catalog_status=='active').order_by(movies.c.movie_id))]
        active_set=set(active)
        mappings=dict(connection.execute(
            select(external.c.movie_id,external.c.external_id).where(external.c.source=='tmdb')).all())
        has_metadata=current_state.has_metadata_table(engine)
        current={}
        if has_metadata:
            current={mid:entry for mid,entry in current_state.load_topics(connection).items()
                     if mid in active_set}
        try:
            has_countries_table=inspect(connection).has_table('tmdb_movie_countries')
        except Exception:
            has_countries_table=False
        tmdb_countries={}
        if has_countries_table:
            tc=table('tmdb_movie_countries',column('movie_id'),column('country_iso2'),column('country_name'))
            seen_iso={}
            for mid,iso2,cname in connection.execute(
                select(tc.c.movie_id,tc.c.country_iso2,tc.c.country_name)
            ).all():
                if mid is None:
                    continue
                mid=int(mid)
                if mid not in active_set:
                    continue
                code=str(iso2 or '').strip().upper()
                if not code:
                    continue
                bucket=seen_iso.setdefault(mid,set())
                if code in bucket:
                    continue
                bucket.add(code)
                tmdb_countries.setdefault(mid,[]).append({'iso2':code,'name':cname})
            for bucket in tmdb_countries.values():
                bucket.sort(key=lambda item:item['iso2'])
        countries={}
        pending=[]
        fetch_entries=[]
        for movie_id in active:
            entry=current.get(movie_id)
            fact=(entry or {}).get('topics',{}).get(current_state.COUNTRIES_TOPIC)
            if not fact or fact.get('state') not in current_state.SUCCESS_STATES:
                pending.append(movie_id)
                continue
            if fact.get('state')=='EMPTY':
                countries[movie_id]=[]
                continue
            mapping=mappings.get(movie_id)
            if tmdb_countries and movie_id in tmdb_countries and mapping is not None and str(mapping)==str((entry or {}).get('tmdb_id')):
                countries[movie_id]=tmdb_countries[movie_id]
                continue
            fetch_entries.append((movie_id,entry,fact,mapping))
        if fetch_entries:
            wanted=[fact.get('source_record_pk') for _mid,_entry,fact,_map in fetch_entries if fact.get('source_record_pk') is not None]
            fetched=current_state.fetch_countries(connection,wanted)
            for movie_id,entry,fact,mapping in fetch_entries:
                record=fetched.get(fact.get('source_record_pk'))
                if not current_state.countries_ok(movie_id,(entry or {}).get('tmdb_id'),mapping,record):
                    pending.append(movie_id)
                    continue
                row={}
                current_state.apply_countries(row,{'production_countries':record[1]},
                                              fact.get('fetched_at'))
                countries[movie_id]=[{'iso2':item['iso2'],'name':item['name']}
                                     for item in row['production_countries']]
        if pending:
            for movie_id,payload,source_at,source in _ordered_source_snapshots(
                    connection,records,movies,source_names=('tmdb','legacy_movies2'),
                    order_by_movie=True,movie_ids=pending,include_source=True):
                movie_id=int(movie_id)
                if movie_id in countries or not isinstance(payload,dict):continue
                if source=='tmdb' and (not mappings.get(movie_id) or str(payload.get('id'))!=str(mappings[movie_id])):continue
                row={}
                current_state.apply_countries(row,payload,None)
                countries[movie_id]=[{'iso2':item['iso2'],'name':item['name']}
                                     for item in row['production_countries']]
        for movie_id in active:
            items.append({'movie_id':movie_id,'countries':countries.get(movie_id,[])})
    return items


def production_country_rows(engine):
    """Current-state production countries for every active movie (legacy fallback).

    Countries come from the ``countries`` topic attribution only, so a base
    capture that happens to contain countries cannot override a newer countries
    capture.  FAILED keeps the retained success (provenance is not replaced);
    EMPTY is a real "no countries" answer and does not fall back to history.
    Movies without any supplement capture (e.g. the 114 unmapped titles) keep the
    newest valid legacy/TMDB record so no title drops out.
    """
    cached = _cached_catalog_data(
        _COUNTRY_ROWS_CACHE, _COUNTRY_ROWS_INFLIGHT, engine,
        lambda: _build_production_country_rows(engine),
    )
    return _clone_country_rows(cached)


def _clone_globe_summary(summary):
    return {
        'total_unique_movies': summary['total_unique_movies'],
        'covered_country_count': summary['covered_country_count'],
        'overview_genres': [list(pair) for pair in summary['overview_genres']],
        'genre_options': list(summary['genre_options']),
        'countries': [
            {
                'iso2': row['iso2'],
                'catalog_count': row['catalog_count'],
                'genres': [list(pair) for pair in row['genres']],
            }
            for row in summary['countries']
        ],
    }


def _build_globe_summary(engine):
    country_rows = production_country_rows(engine)
    md = CatalogBase.metadata
    movies, links, genres = md.tables['movies'], md.tables['movie_genres'], md.tables['genres']
    movie_genres = {}
    global_genres = Counter()
    with engine.connect() as connection:
        for mid, name_zh in connection.execute(
            select(links.c.movie_id, genres.c.name_zh)
            .select_from(links.join(genres, genres.c.genre_id == links.c.genre_id)
                         .join(movies, movies.c.movie_id == links.c.movie_id))
            .where(movies.c.catalog_status == 'active')
        ).all():
            if not name_zh:
                continue
            bucket = movie_genres.setdefault(int(mid), [])
            if name_zh not in bucket:
                bucket.append(name_zh)
                global_genres[name_zh] += 1
    country_counts = Counter()
    country_genres = defaultdict(Counter)
    for row in country_rows:
        mid = row['movie_id']
        m_genres = movie_genres.get(mid, ())
        for country in row['countries']:
            iso2 = country['iso2']
            country_counts[iso2] += 1
            for gname in m_genres:
                country_genres[iso2][gname] += 1
    ordered_genres = sorted(global_genres.items(), key=lambda item: (-item[1], item[0]))
    countries = [
        {
            'iso2': iso2,
            'catalog_count': count,
            'genres': [
                [gname, gcount]
                for gname, gcount in sorted(country_genres[iso2].items(), key=lambda item: (-item[1], item[0]))
            ],
        }
        for iso2, count in sorted(country_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        'total_unique_movies': len(country_rows),
        'covered_country_count': len(countries),
        'overview_genres': [[name, count] for name, count in ordered_genres[:6]],
        'genre_options': [name for name, _ in ordered_genres[:10]],
        'countries': countries,
    }


def globe_summary_payload(engine):
    cached = _cached_catalog_data(
        _GLOBE_SUMMARY_CACHE, _GLOBE_SUMMARY_INFLIGHT, engine,
        lambda: _build_globe_summary(engine),
    )
    return _clone_globe_summary(cached)


def install_m6_business(app):
    engine=app.extensions['auth_engine']
    from .api_read import create_read_blueprint
    def executor(statement):
        with engine.connect() as connection:return list(connection.execute(statement).mappings())
    def personal_ids(kind):
        user=identity()
        if user is None:raise PermissionError('Authentication required')
        return [int(mid) for mid,state in repository.snapshot(user).items() if state.get(kind)]
    app.register_blueprint(create_read_blueprint(executor,personal_ids))
    repository=MySQLBehaviorRepository(engine,app.config)
    app.extensions['m6_behavior']=repository
    bp=Blueprint('m6_business',__name__)
    def response(data=None,status=200,code='OK'):
        return jsonify(code=code,data=data),status
    def identity(mutate=False):
        with app.extensions['auth_db'].begin() as db:
            row=session_row(db,lock=mutate)
            if not row or row.user_id is None:return None
            if mutate and not csrf_ok(row):raise PermissionError('CSRF')
            return row.user_id
    @bp.get('/api/catalog/demo-info')
    def info():
        receipts=repository.tables['receipts']
        with engine.connect() as connection:
            receipt=connection.execute(select(receipts.c.receipt).limit(1)).scalar_one_or_none()
        return jsonify(mode='M6_MYSQL_AUTHENTICATED',persistence='MYSQL',source=receipt.get('source_kind') if receipt else 'empty')
    @bp.get('/api/catalog/genres')
    def genres():
        if request.args:return response(status=400,code='INVALID_INPUT')
        md=CatalogBase.metadata;g=md.tables['genres'];links=md.tables['movie_genres'];movies=md.tables['movies']
        statement=select(g.c.genre_id,g.c.name_zh,g.c.name_en).join(links,links.c.genre_id==g.c.genre_id).join(movies,movies.c.movie_id==links.c.movie_id).where(movies.c.catalog_status=='active').distinct().order_by(g.c.genre_id).limit(500)
        return response({'items':[dict(row) for row in executor(statement)]})
    @bp.get('/api/catalog/languages')
    def languages():
        if request.args:return response(status=400,code='INVALID_INPUT')
        md=CatalogBase.metadata;movies=md.tables['movies']
        total=func.count().label('count')
        statement=(select(movies.c.original_language.label('code'),total)
                   .where(movies.c.catalog_status=='active',movies.c.original_language.isnot(None),movies.c.original_language!='')
                   .group_by(movies.c.original_language)
                   .order_by(total.desc(),movies.c.original_language)
                   .limit(100))
        return response({'items':[{'code':row.code,'count':int(row.count)} for row in executor(statement)]})
    @bp.get('/api/catalog/behavior')
    def behavior():
        user=identity()
        if user is None:return response(status=401,code='AUTHENTICATION_REQUIRED')
        states=repository.snapshot(user)
        timeline=repository.watched_timeline(user)
        for movie_id,state in states.items():
            if isinstance(state,dict) and state.get('watched'):
                stamp=timeline.get(int(movie_id))
                state['watched_at']=stamp.strftime('%Y-%m-%dT%H:%M:%SZ') if stamp else None
        return response({'user':str(user),'state':states,'mode':'MYSQL_AUTHENTICATED'})

    @bp.get('/api/catalog/production-countries')
    def production_countries():
        return response({'items':production_country_rows(engine),
                         'source':'tmdb_current_state'})

    @bp.get('/api/catalog/globe-summary')
    def globe_summary():
        if request.args:
            return response(status=400,code='INVALID_INPUT')
        return response(globe_summary_payload(engine))
    @bp.post('/api/catalog/behavior')
    def record():
        try:
            user=identity(mutate=True)
            if user is None:return response(status=401,code='AUTHENTICATION_REQUIRED')
            if not request.is_json:return response(status=400,code='INVALID_INPUT')
            result=repository.apply(user,request.get_json(silent=True))
            if not result.get('duplicate'):
                try:
                    from .recommendation_boards import invalidate_board_cache, warm_user_home_async
                    invalidate_board_cache(engine,user,behavior_version=result['version'])
                    warm_user_home_async(app,user)
                except Exception:
                    app.logger.warning('Recommendation cache refresh could not be scheduled')
            return response(result)
        except PermissionError:return response(status=403,code='REQUEST_VERIFICATION_FAILED')
        except Conflict:return response(status=409,code='STATE_CONFLICT')
        except ValueError:return response(status=400,code='INVALID_INPUT')
    @bp.get('/api/catalog/recommendation-channels')
    def recommendation_channels():
        if identity() is None:return response(status=401,code='AUTHENTICATION_REQUIRED')
        if request.args:return response(status=400,code='INVALID_INPUT')
        from .recommendation_boards import CHANNELS,PUBLIC,DESCRIPTIONS
        return response({'items':[{'id':k,'title':v,'public':k in PUBLIC,'description':DESCRIPTIONS[k]} for k,v in CHANNELS.items() if k!='personal']})
    @bp.get('/api/catalog/recommendations')
    def recommendations():
        user=identity()
        if user is None:return response(status=401,code='AUTHENTICATION_REQUIRED')
        try:
            keys={'limit','favorite_weight','want_weight','rating_weight','channel','genre','year_from','year_to','runtime_max','language','unwatched','keyword','min_votes','scope'}
            if set(request.args)-keys or any(len(request.args.getlist(k))!=1 for k in request.args):raise ValueError()
            def number(key,default=None):
                raw=request.args.get(key)
                if raw is None:return default
                if not re.fullmatch(r'[0-9]{1,8}',raw):raise ValueError()
                return int(raw)
            filters={k:number(k) for k in ('year_from','year_to','runtime_max') if k in request.args}
            for k in ('genre','language'):
                if k in request.args:filters[k]=request.args[k]
            if 'unwatched' in request.args:
                if request.args['unwatched'] not in ('true','false'):raise ValueError()
                filters['unwatched']=request.args['unwatched']=='true'
            weights={k:float(request.args.get(k+'_weight',default)) for k,default in [('favorite',3),('want',2),('rating',1)]}
            from .recommendation_boards import for_app
            channel=request.args.get('channel','personal')
            if channel=='box_office':channel='revenue'
            return response(for_app(app,user,channel,limit=number('limit',12),filters=filters,keyword=request.args.get('keyword',''),min_votes=number('min_votes'),scope=request.args.get('scope','all'),weights=weights))
        except ValueError:return response(status=400,code='INVALID_INPUT')
        except OSError:return response(status=503,code='CHANNEL_CACHE_UNAVAILABLE')
    app.register_blueprint(bp)
