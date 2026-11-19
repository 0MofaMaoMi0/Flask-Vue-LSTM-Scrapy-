"""Synthetic regressions; no external DB/config or provider calls."""
from datetime import datetime,timedelta
from types import SimpleNamespace
from sqlalchemy import create_engine,MetaData,Table,Column,Integer,String,JSON,DateTime,Float,Boolean,Date,insert
from catalog.models import CatalogBase
from catalog.recommendation_data import load_candidates
from assistant.reader import CatalogReader

def fixture():
    engine=create_engine('sqlite://');md=MetaData()
    for name in ('movies','genres','movie_genres','movie_external_ids','movie_source_records','movie_assets','movie_credits'):
        columns=[]
        for col in CatalogBase.metadata.tables[name].columns:
            typ=col.type
            kind=JSON if isinstance(typ,JSON) else DateTime if isinstance(typ,DateTime) else Date if isinstance(typ,Date) else Boolean if isinstance(typ,Boolean) else String if isinstance(typ,String) else Float if isinstance(typ,Float) else Integer
            columns.append(Column(col.name,kind))
        Table(name,md,*columns)
    md.create_all(engine);now=datetime(2026,11,13)
    with engine.begin() as c:
        c.execute(insert(md.tables['movies']),[dict(movie_id=i,title='film '+str(i),catalog_status='active',release_year=2020 if i<=1000 else 1980) for i in range(1,1006)])
        c.execute(insert(md.tables['genres']),[dict(genre_id=1,name_zh='科幻')])
        c.execute(insert(md.tables['movie_genres']),[dict(movie_id=1005,genre_id=1)])
        c.execute(insert(md.tables['movie_external_ids']),[dict(movie_id=i,source='tmdb',external_id=str(i)) for i in [1,2,3,1005]])
        c.execute(insert(md.tables['movie_source_records']),[
            dict(id=1,movie_id=1,source='tmdb',metadata_json={'id':1,'popularity':999},fetched_at=now-timedelta(days=2)),
            dict(id=2,movie_id=1,source='tmdb',metadata_json={'id':1,'popularity':None},fetched_at=now),
            dict(id=3,movie_id=2,source='tmdb',metadata_json={'id':999,'popularity':999999},fetched_at=now),
            dict(id=4,movie_id=3,source='tmdb',metadata_json={'id':3,'popularity':True},fetched_at=now),
            dict(id=5,movie_id=1005,source='tmdb',metadata_json={'id':1005,'popularity':42.5},fetched_at=now)])
    return engine

def test_all_catalog_and_verified_heat():
    rows=load_candidates(fixture());byid={r['movie_id']:r for r in rows}
    assert len(rows)==1005 and byid[1005]['genres']==['科幻']
    assert byid[1005]['tmdb_popularity']==42.5 and byid[1005]['catalog_popularity']==1
    assert all(byid[i]['tmdb_popularity'] is None for i in [1,2,3,4])
    assert byid[1005]['popularity_observed_at']=='2026-11-13T00:00:00Z'

def test_conditions_precede_ranking_past_old_top50_and_top1000():
    reader=CatalogReader(fixture(),SimpleNamespace(snapshot=lambda user:{}))
    rows,_,truncated,all_movies=reader.resolve(1,'recommend',{'year_to':1990,'genres_any':['科幻']})
    assert len(all_movies)==1005 and not truncated
    assert rows and rows[0]['movie_id']==1005
    assert all(r['release_year']<=1990 for r in rows)

def test_hard_exclusions_and_filtered_profile_preserved():
    from catalog.recommendation import recommend
    movies=[dict(movie_id=1,title='liked',catalog_status='active',genres=['科幻']),dict(movie_id=2,title='match',catalog_status='active',genres=['科幻']),dict(movie_id=3,title='other',catalog_status='active',genres=['喜剧'])]
    result=recommend(movies[1:],{'1':{'favorite':True}},profile_movies=movies)
    assert result['items'][0]['movie_id']==2 and result['items'][0]['components']['preference']>0
    reader=CatalogReader(fixture(),SimpleNamespace(snapshot=lambda user:{'1005':{'not_interested':True}}))
    assert not reader.resolve(1,'recommend',{'year_to':1990,'genres_any':['科幻']})[0]


def test_keyword_filtered_before_top_k():
    reader=CatalogReader(fixture(),SimpleNamespace(snapshot=lambda user:{}))
    rows,_,_,_=reader.resolve(1,'recommend',{},keyword='film 1004')
    assert [r['movie_id'] for r in rows]==[1004]


def test_valid_low_ranked_candidates_not_lost_after_top50():
    reader=CatalogReader(fixture(),SimpleNamespace(snapshot=lambda user:{}))
    rows,_,_,_=reader.resolve(1,'recommend',{'year_to':1990})
    assert {r['movie_id'] for r in rows}==set(range(1001,1006))
