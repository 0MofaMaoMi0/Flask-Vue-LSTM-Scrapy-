"""Bounded read-only M6 adapter; SQL values are bound parameters."""
from sqlalchemy import select
from catalog.models import CatalogBase
from catalog.recommendation import recommend
from catalog.recommendation_pipeline import rank_movies
from .conditions import matches,genre_groups,FILTER_KEYS
class CatalogReader:
    def __init__(self,engine,behavior,tmdb=None,cache_dir=None):self.engine=engine;self.behavior=behavior;self.tmdb=tmdb;self.cache_dir=cache_dir
    def snapshot(self,user):
        from catalog.recommendation_data import load_candidates
        return load_candidates(self.engine),self.behavior.snapshot(user),False
    def resolve(self,user,intent,filters,exclude=(),keyword=''):
        if type(keyword) is not str or len(keyword)>100:raise ValueError('Keyword')
        movies,state,truncated=self.snapshot(user)
        allowed=[m for m in movies if matches(m,filters,{} if intent=='behavior' else state.get(str(m['movie_id']),{})) and m['movie_id'] not in exclude and (not keyword or keyword.casefold() in (m.get('title','')+' '+(m.get('original_title') or '')).casefold())]
        if intent=='behavior':allowed=[m for m in allowed if state.get(str(m['movie_id']),{}).get(filters.get('behavior','want'))]
        if intent=='recommend' and filters.get('behavior')=='want':
            allowed=[m for m in allowed if state.get(str(m['movie_id']),{}).get('want')]
        if intent=='recommend':
            policy={k:v for k,v in filters.items() if k in FILTER_KEYS or k in ('_genre_groups','_year_groups')}
            ranking=rank_movies(allowed,state,profile_movies=movies,limit=10,filters=policy,allow_watched=filters.get('unwatched') is False)
            byid={m['movie_id']:m for m in allowed};allowed=[dict(byid[r['movie_id']],local_reasons=r['reasons'],**{k:r[k] for k in ('score','components','reason_codes','evidence','recommendation_version')}) for r in ranking['items'] if r['movie_id'] in byid]
        return allowed[:10],state,truncated,movies

    def recommendation_evidence(self,user,ids,filters):
        movies,state,_=self.snapshot(user)
        selected=[m for m in movies if m['movie_id'] in ids]
        policy={k:v for k,v in filters.items() if k in FILTER_KEYS or k in ('_genre_groups','_year_groups')}
        result=rank_movies(selected,state,profile_movies=movies,limit=10,filters=policy,allow_watched=filters.get('unwatched') is False)
        return {m['movie_id']:m for m in result['items']}

    def recommendation_board(self,user,channel,filters,keyword='',limit=10,scope='all'):
        from catalog.recommendation_boards import board
        from flask import has_app_context,current_app
        from pathlib import Path
        folder=Path(current_app.config['MEDIA_CACHE_DIR'])/'.private-recommendations' if has_app_context() else self.cache_dir
        movies,state,_=self.snapshot(user)
        return board(movies,state,user,channel,filters=filters,keyword=keyword,limit=limit,scope=scope,cache_dir=folder,trusted_filters=True)

    def get_movies(self,ids):
        if type(ids) is not list or len(ids)>10 or any(type(i) is not int or i<1 for i in ids):raise ValueError('IDs')
        md=CatalogBase.metadata;m=md.tables['movies'];g=md.tables['genres'];links=md.tables['movie_genres']
        with self.engine.connect() as c:
            rows=[dict(r) for r in c.execute(select(m).where(m.c.catalog_status=='active',m.c.movie_id.in_(ids))).mappings()]
            grouped={}
            if rows:
                for row in c.execute(select(links.c.movie_id,g.c.name_zh).join(g,g.c.genre_id==links.c.genre_id).where(links.c.movie_id.in_(ids))):grouped.setdefault(row.movie_id,[]).append(row.name_zh)
            from catalog.media_urls import asset_url
            a=md.tables['movie_assets'];posters={}
            for asset in c.execute(select(a).where(a.c.movie_id.in_(ids),a.c.asset_type=='poster').order_by(a.c.is_primary.desc(),a.c.asset_id)).mappings():
                url=asset_url(asset)
                if url:posters.setdefault(asset['movie_id'],url)
            for row in rows:row.update(genres=grouped.get(row['movie_id'],[]),poster=posters.get(row['movie_id']))
            return rows
    def live_movie(self,user,movie_id):
        from catalog.tmdb_connection import current,TMDBError
        from catalog.recommendation_data import load_candidates
        rows=self.get_movies([movie_id]);context=current()
        if not rows or not context:return rows
        service,owner,binding=context
        if owner!=user or not service.status(user,binding)['enabled']:return rows
        mapped=next((m for m in load_candidates(self.engine) if m['movie_id']==movie_id),None)
        if not mapped or not str(mapped.get('tmdb_id') or '').isdecimal():return rows
        try:
            doc=service.detail(user,binding,int(mapped['tmdb_id']));enriched=service.enrich([mapped])[0]
            for k in ('tmdb_id','tmdb_rating','vote_count','revenue','revenue_as_of','tmdb_popularity','feature_source','feature_as_of'):rows[0][k]=enriched.get(k)
            rows[0]['tmdb_details']=dict(doc['movie'],source_as_of=doc['captured_at'],identity='TMDB_ID_NOT_INTERNAL_ID')
        except TMDBError:rows[0]['tmdb_status']='CACHE_OR_LOCAL_FALLBACK'
        return rows
    def search(self,user,filters,keyword='',limit=10):
        from sqlalchemy import exists
        md=CatalogBase.metadata;m=md.tables['movies'];g=md.tables['genres'];links=md.tables['movie_genres'];stmt=select(m.c.movie_id).where(m.c.catalog_status=='active')
        for key,condition in [('year_from',lambda v:m.c.release_year>=v),('year_to',lambda v:m.c.release_year<=v),('runtime_max',lambda v:m.c.runtime_minutes<=v),('language',lambda v:m.c.original_language==v)]:
            if filters.get(key) is not None:stmt=stmt.where(condition(filters[key]))
        def has_genre(value):return exists(select(links.c.movie_id).join(g,g.c.genre_id==links.c.genre_id).where(links.c.movie_id==m.c.movie_id,g.c.name_zh.contains(value,autoescape=True)))
        from sqlalchemy import or_
        for group in genre_groups(filters):stmt=stmt.where(or_(*(has_genre(g) for g in group)))
        if filters.get('years_any') is not None:stmt=stmt.where(m.c.release_year.in_(filters['years_any']))
        for genre in filters.get('excluded_genres',[]):stmt=stmt.where(~has_genre(genre))
        for group in filters.get('_year_groups',([filters['year_ranges_any']] if filters.get('year_ranges_any') else [])):stmt=stmt.where(or_(*(m.c.release_year.between(lo,hi) for lo,hi in group)))
        for lo,hi in filters.get('excluded_year_ranges',[]):stmt=stmt.where(~m.c.release_year.between(lo,hi))
        if keyword:stmt=stmt.where(m.c.title.contains(keyword,autoescape=True)|m.c.original_title.contains(keyword,autoescape=True))
        state=self.behavior.snapshot(user);excluded=[int(mid) for mid,s in state.items() if s.get('not_interested') or (filters.get('unwatched') and s.get('watched'))]
        if excluded:stmt=stmt.where(m.c.movie_id.not_in(excluded))
        with self.engine.connect() as c:ids=list(c.execute(stmt.order_by(m.c.movie_id).limit(limit+1)).scalars())
        rows=self.get_movies(ids[:limit]);byid={m['movie_id']:m for m in rows}
        return [byid[i] for i in ids[:limit] if i in byid],len(ids)>limit
