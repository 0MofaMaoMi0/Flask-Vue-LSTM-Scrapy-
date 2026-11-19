import pytest
from catalog.read_queries import movie_list
from assistant.intent import parse
from assistant.reader import CatalogReader

def test_watchlist_source_scope_does_not_become_global_recommendation():
 intent,f=parse('从我的想看清单推荐电影',{})
 reader=CatalogReader(None,None)
 movies=[dict(movie_id=i,title=str(i),catalog_status='active',genres=['科幻'],catalog_popularity=1,release_year=2000,original_language='en') for i in (1,2)]
 reader.snapshot=lambda user:(movies,{'2':{'want':True}},False)
 items,_,_,_=reader.resolve(7,intent,f)
 assert [m['movie_id'] for m in items]==[2]

def test_preference_seed_is_not_a_hard_watchlist_scope():
 for message in ['根据我的想看清单推荐相似电影','不要从我的想看清单推荐']:
  _,f=parse(message,{})
  assert f.get('behavior')!='want'

def test_personal_catalog_scope_is_in_sql_before_limit():
 from sqlalchemy.sql import operators
 stmt=movie_list(movie_ids=[99],limit=1,offset=0)
 from sqlalchemy.sql.visitors import iterate
 assert any(getattr(n,'operator',None)==operators.in_op and getattr(n.right,'value',None)==[99] for n in iterate(stmt.whereclause))
 assert stmt._limit_clause.value==1


def test_personal_filter_requires_identity_before_query():
 from flask import Flask
 from catalog.api_read import create_read_blueprint
 def scope(kind):raise PermissionError('No session')
 def query(stmt):raise AssertionError('Must not query')
 app=Flask(__name__);app.register_blueprint(create_read_blueprint(query,scope))
 r=app.test_client().get('/api/catalog/movies?state=want')
 assert r.status_code==401
