"""Authenticated behavior and existing catalog/recommendation contracts on MySQL."""
from datetime import datetime
import json
import re
from pathlib import Path
from flask import Blueprint, current_app, request, jsonify
from sqlalchemy import select, insert, update, func
from sqlalchemy.dialects.mysql import insert as mysql_insert
from app.auth import session_row, csrf_ok
from app.models import User
from app.m6_activity import activity_tables
from .models import CatalogBase
from .recommendation import validate_event, transition, recommend, Conflict


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
        md=CatalogBase.metadata;movies=md.tables['movies'];records=md.tables['movie_source_records']
        statement=(select(records.c.movie_id,records.c.metadata_json)
                   .join(movies,movies.c.movie_id==records.c.movie_id)
                   .where(movies.c.catalog_status=='active',records.c.source.in_(('tmdb','legacy_movies2')))
                   .order_by(records.c.movie_id))
        with engine.connect() as connection:
            rows=connection.execute(statement).all()
        items=[];seen=set()
        for movie_id,payload in rows:
            if int(movie_id) in seen:continue
            seen.add(int(movie_id))
            countries=[]
            for entry in (payload or {}).get('production_countries') or []:
                if not isinstance(entry,dict):continue
                iso2=entry.get('iso_3166_1')
                if not iso2 or any(c['iso2']==iso2 for c in countries):continue
                countries.append({'iso2':iso2,'name':entry.get('name')})
            items.append({'movie_id':int(movie_id),'countries':countries})
        return response({'items':items,'source':'tmdb_metadata'})
    @bp.post('/api/catalog/behavior')
    def record():
        try:
            user=identity(mutate=True)
            if user is None:return response(status=401,code='AUTHENTICATION_REQUIRED')
            if not request.is_json:return response(status=400,code='INVALID_INPUT')
            return response(repository.apply(user,request.get_json(silent=True)))
        except PermissionError:return response(status=403,code='REQUEST_VERIFICATION_FAILED')
        except Conflict:return response(status=409,code='STATE_CONFLICT')
        except ValueError:return response(status=400,code='INVALID_INPUT')
    def movies_for_recommendation():
        from .recommendation_data import load_candidates
        return load_candidates(engine)
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
