"""Shared full-catalog, read-only ranking inputs. Missing source facts stay missing."""
import math,json
from pathlib import Path
from datetime import datetime
from threading import Lock
from time import monotonic
from weakref import WeakKeyDictionary
from urllib.parse import quote
from sqlalchemy import select
from .models import CatalogBase
from .media_urls import asset_url

_cache=WeakKeyDictionary()
_lock=Lock()

def invalidate(engine):
    with _lock:_cache.pop(engine,None)

def load_candidates(engine):
    from .tmdb_connection import enrich
    with _lock:
        entry=_cache.get(engine)
        if entry and monotonic()-entry[0]<30:return enrich([dict(m) for m in entry[1]])
        rows=_load_candidates(engine);_cache[engine]=(monotonic(),rows)
        return enrich([dict(m) for m in rows])

def valid_number(value,maximum=None):
    return type(value) in (int,float) and math.isfinite(value) and value>=0 and (maximum is None or value<=maximum)

def features(row,payload,stamp,source):
    heat=payload.get('popularity');score=payload.get('vote_average');votes=payload.get('vote_count');revenue=payload.get('revenue')
    row.update(tmdb_popularity=float(heat) if valid_number(heat) else None,tmdb_rating=float(score) if valid_number(score,10) and type(votes) is int and votes>0 else None,vote_count=votes if type(votes) is int and votes>=0 else None,revenue=int(revenue) if valid_number(revenue) and revenue>0 else None,popularity_observed_at=stamp,feature_source=source,feature_as_of=stamp)
    if 'revenue' in payload:row['revenue_as_of']=stamp

def _load_candidates(engine):
    t=CatalogBase.metadata.tables
    m,g,links,sources,external,assets=[t[n] for n in ('movies','genres','movie_genres','movie_source_records','movie_external_ids','movie_assets')]
    with engine.connect() as c:
        movies=[dict(row) for row in c.execute(select(m).where(m.c.catalog_status=='active').order_by(m.c.movie_id)).mappings()]
        byid={row['movie_id']:row for row in movies}
        for row in movies:row.update(genres=[],tmdb_popularity=None,popularity_observed_at=None,poster=None,tmdb_id=None,tmdb_rating=None,vote_count=None,revenue=None,feature_source=None,feature_as_of=None,actor_ids=[],director_ids=[])
        for mid,name in c.execute(select(links.c.movie_id,g.c.name_zh).join(g,g.c.genre_id==links.c.genre_id).join(m,m.c.movie_id==links.c.movie_id).where(m.c.catalog_status=='active')):
            byid[mid]['genres'].append(name)
        mappings=dict(c.execute(select(external.c.movie_id,external.c.external_id).where(external.c.source=='tmdb')).all())
        for mid,tid in mappings.items():
            if mid in byid:byid[mid]['tmdb_id']=tid
        seen=set()
        for mid,payload,observed in c.execute(select(sources.c.movie_id,sources.c.metadata_json,sources.c.fetched_at).join(m,m.c.movie_id==sources.c.movie_id).where(m.c.catalog_status=='active',sources.c.source=='tmdb').order_by(sources.c.fetched_at.desc(),sources.c.id.desc())):
            if mid in seen:continue
            seen.add(mid)
            if not isinstance(payload,dict) or not mappings.get(mid) or str(payload.get('id'))!=str(mappings[mid]):continue
            features(byid[mid],payload,observed.isoformat()+'Z' if observed else None,'TMDB_DATABASE_SNAPSHOT')
        archive=Path(__file__).with_name('data')/'archived_features.json'
        if archive.is_file():
            doc=json.loads(archive.read_text(encoding='utf-8'))
            for fact in doc.get('rows',[]):
                mid=fact.get('movie_id');row=byid.get(mid)
                if not row or str(row['tmdb_id'])!=str(fact.get('id')):continue
                stamp=fact.get('archive_date','')+'T00:00:00Z'
                if not row['feature_as_of'] or row['feature_as_of']<stamp:
                    features(row,fact,stamp,'APPROVED_STATIC_TMDB_CAPTURE')
        credits=t.get('movie_credits')
        if credits is not None:
            from sqlalchemy import or_,and_
            for mid,pid,role,job in c.execute(select(credits.c.movie_id,credits.c.person_id,credits.c.role_type,credits.c.job).join(m,m.c.movie_id==credits.c.movie_id).where(m.c.catalog_status=='active',or_(and_(credits.c.role_type=='crew',credits.c.job=='Director'),and_(credits.c.role_type=='cast',credits.c.sort_order<10)))):
                byid[mid]['director_ids' if role=='crew' else 'actor_ids'].append(int(pid))
        for row in c.execute(select(assets).join(m,m.c.movie_id==assets.c.movie_id).where(m.c.catalog_status=='active',assets.c.asset_type=='poster').order_by(assets.c.is_primary.desc(),assets.c.asset_id)).mappings():
            if byid[row['movie_id']]['poster']:continue
            byid[row['movie_id']]['poster']=asset_url(row)
    maximum=max((math.log1p(row['tmdb_popularity']) for row in movies if row['tmdb_popularity'] is not None),default=0)
    for row in movies:
        row['catalog_popularity']=math.log1p(row['tmdb_popularity'])/maximum if maximum and row['tmdb_popularity'] is not None else 0
    return movies
