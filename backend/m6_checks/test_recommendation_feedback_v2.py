from datetime import datetime, timedelta
from catalog.recommendation_feedback import evaluate
from catalog import board_snapshots
import json

NOW=datetime(2026,11,13)
def imp(uid,req,mid,pos,ch='daily'):
    return (uid,ch,mid,pos,req,'2026-11-13','visible-v2','test',NOW)

def test_real_requests_not_best_historical_rank():
    rows=[imp(1,'a',2,1),imp(1,'a',1,2),imp(1,'b',1,1),imp(1,'b',2,2)]
    r=evaluate(rows,[],[(1,1,{'favorite':True})],1)['ranking']
    assert r['precision_at_k']==r['recall_at_k']==r['ndcg_at_k']==0.5
    assert r['users']==1 and r['batches']==2

def test_zero_hit_users_retained_and_users_distinct():
    rows=[imp(1,'a',1,1),imp(1,'b',1,1,'weekly'),imp(2,'c',2,1)]
    r=evaluate(rows,[],[(1,1,{'favorite':True}),(2,3,{'favorite':True})],1)['ranking']
    assert r['precision_at_k']==0.666667 and r['users']==2 and r['batches']==3

def test_click_cohort_ownership_time_and_duplicates():
    rows=[imp(1,'a',1,1),imp(1,'a',2,2)]
    clicks=[(1,'a',1,NOW),(1,'a',1,NOW),(2,'a',2,NOW),(1,'old',1,NOW),(1,'a',2,NOW-timedelta(seconds=1))]
    r=evaluate(rows,clicks,[],10)
    assert r['impressions']==2 and r['clicks']==1 and r['ctr']==0.5

def test_recall_uses_all_positive_movies():
    r=evaluate([imp(1,'a',1,1)],[],[(1,1,{'favorite':True}),(1,2,{'watched':True})],1)
    assert r['ranking']['recall_at_k']==0.5

def test_no_data_and_external_only():
    r=evaluate([imp(1,'a',None,1)],[(1,'a',1,NOW)],[],10)
    assert r['ctr']==1 and r['ranking']['status']=='INSUFFICIENT_DATA'
    assert evaluate([],[],[],10)['ctr'] is None

def test_scheduler_cache_matches_reader(tmp_path,monkeypatch):
    monkeypatch.setenv('MI2_TMDB_BOARD_DIR',str(tmp_path))
    doc=dict(schema=1,channel='trending_day',captured_at='2026-11-13T00:00:00+00:00',items=[{'id':1}])
    p=tmp_path/'trending_day.json';p.write_text(json.dumps(doc))
    assert board_snapshots.read('trending_day')==doc
    doc['schema']=0;p.write_text(json.dumps(doc));assert board_snapshots.read('trending_day') is None


def test_invisible_higher_ranks_not_compacted():
    r=evaluate([imp(1,'a',1,10)],[],[(1,1,{'favorite':True})],1)
    assert r['ranking']['precision_at_k']==0 and r['ranking']['ndcg_at_k']==0


def test_refresh_uses_shared_client_and_reader_protocol(tmp_path,monkeypatch,capsys):
    import stat
    from types import SimpleNamespace
    from pathlib import Path
    from ops.m9 import refresh_trending as job
    key=tmp_path/'operator.key';key.write_text('a'*32)
    monkeypatch.setenv('MI2_TMDB_KEY_FILE',str(key))
    monkeypatch.setenv('MI2_TMDB_BOARD_DIR',str(tmp_path/'boards'))
    original=Path.lstat
    monkeypatch.setattr(Path,'lstat',lambda p,*a,**kw: SimpleNamespace(st_mode=stat.S_IFREG|0o600,st_uid=0) if p==key else original(p,*a,**kw))
    calls=[]
    def get(path,params,token):
        calls.append((path,params,token))
        if path=='genre/movie/list':return {'genres':[{'id':1,'name':'喜剧'}]}
        return {'results':[{'id':1,'title':'合成影片','genre_ids':[1],'adult':False}]}
    monkeypatch.setattr(job.TMDBConnection,'_http',get)
    monkeypatch.setattr(job.time,'sleep',lambda _:None)
    job.main()
    for ch in job.ENDPOINTS:
        doc=board_snapshots.read(ch)
        assert doc['pages']==3 and len(doc['items'])==1 and doc['items'][0]['source_rank']==1
    assert len(calls)==10 and all(token is None for _,_,token in calls)
    assert 'a'*32 not in capsys.readouterr().out
