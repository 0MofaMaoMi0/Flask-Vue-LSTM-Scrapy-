"""Quality regressions use synthetic inputs; live checks remain separately labeled."""
from datetime import datetime, timedelta
import pytest
from catalog.admin_importer import capture, validate, CatalogImporter, ImportRejected
from catalog.recommendation_data import _load_candidates
from test_recommendation_inputs import fixture
from test_admin_imports import raw


def test_invalid_newest_identity_cannot_hide_valid_older_facts():
    engine=fixture()
    from catalog.models import CatalogBase
    s=CatalogBase.metadata.tables['movie_source_records']
    with engine.begin() as c:
        c.execute(s.insert().values(id=100,movie_id=1005,source='tmdb',metadata_json={'id':99999,'revenue':99999999},fetched_at=datetime(2026,11,14)))
    row=next(r for r in _load_candidates(engine) if r['movie_id']==1005)
    assert row['tmdb_popularity']==42.5 and row['revenue'] is None


def test_partial_new_capture_preserves_historical_revenue_and_actual_date():
    engine=fixture()
    from catalog.models import CatalogBase
    s=CatalogBase.metadata.tables['movie_source_records']
    with engine.begin() as c:
        c.execute(s.insert().values(id=100,movie_id=1005,source='tmdb',metadata_json={'id':1005,'revenue':4000,'vote_average':8,'vote_count':30},fetched_at=datetime(2026,11,12)))
    row=next(r for r in _load_candidates(engine) if r['movie_id']==1005)
    assert row['revenue']==4000 and row['revenue_as_of']=='2026-11-12T00:00:00Z'
    assert row['tmdb_popularity']==42.5 and row['vote_count'] is None


def test_unknown_online_or_archive_revenue_never_redates_known_value():
    from catalog.recommendation_data import features
    row=dict(revenue=500,revenue_as_of='2026-11-01T00:00:00Z',revenue_source='TMDB_DATABASE_SNAPSHOT')
    for p in ({'revenue':0},{'revenue':None},{'popularity':20}):
        features(row,p,'2026-11-13T00:00:00Z','TMDB_ONLINE_CACHE')
        assert row['revenue']==500 and row['revenue_as_of']=='2026-11-01T00:00:00Z' and row['revenue_source']=='TMDB_DATABASE_SNAPSHOT'


@pytest.mark.parametrize('value',[True,-1,2.5,float('inf'),'100',9223372036854775808])
def test_invalid_revenue_not_promoted_to_recommendation_facts(value):
    with pytest.raises((ValueError,TypeError)):capture(dict(raw(),revenue=value))


def test_metadata_refresh_cannot_append_or_accept_different_title_or_year():
    engine=fixture();importer=CatalogImporter(engine,{})
    def p(**changes):return dict(capture(dict(raw(1005),title='film 1005',original_title=None,release_date='1980-01-01',**changes)),entries=[],metadata_only=True)
    payload=p()
    with engine.connect() as c:
        assert importer.plan(c,payload)['movie_id']==1005
        for changed in (dict(payload,id=9999),dict(payload,title='其他作品'),dict(payload,release_date='2001-01-01')):
            with pytest.raises(ImportRejected):importer.plan(c,changed)
    with pytest.raises(ImportRejected):validate(dict(payload,entries=capture(raw())['entries']))


def test_quality_inventory_is_admin_only(tmp_path,monkeypatch):
    from test_admin_imports import api_app
    for identity,status in [('anonymous',401),('viewer',403)]:
        c=api_app(tmp_path/identity,monkeypatch,identity).test_client()
        assert c.get('/api/admin/imports/quality').status_code==status
        assert c.post('/api/admin/imports/quality/runs',json={'confirm':True}).status_code==status
    c=api_app(tmp_path/'csrf',monkeypatch,csrf=False).test_client()
    assert c.post('/api/admin/imports/quality/runs',json={'confirm':True}).status_code==403


def test_bounded_quality_sweep_never_imports_search_or_board_candidates(tmp_path):
    import uuid
    from types import SimpleNamespace
    from catalog.quality_runs import QualityRuns
    from catalog.import_jobs import ImportJobs
    from test_admin_imports import Adapter
    adapter=Adapter();adapter.engine=fixture()
    adapter.preview=lambda rows:[dict(movie_id=1,tmdb_id=p['id'],action='supplement') for p in rows]
    jobs=ImportJobs(tmp_path/'imports',adapter,lambda uid:uid==1)
    jobs.start=jobs.process_one
    service=SimpleNamespace(_get=lambda user,binding,path,params:dict(results=[{'id':111,'title':'同名电影'}],total_results=1) if path=='search/movie' else raw(int(path.split('/')[-1])))
    runs=QualityRuns(tmp_path/'runs',jobs,service)
    scope=dict(gaps=[dict(movie_id=9,title='同名电影',year=2001)],pending=[9000000001],refresh=[dict(movie_id=1,tmdb_id='9000000002')])
    doc=dict(job_id=str(uuid.uuid4()),owner=1,scope=scope,status='RUNNING',items=[],completed=0,created_at='now')
    runs._run(doc,'binding')
    assert doc['status']=='COMPLETED' and doc['completed']==3
    assert adapter.applied==1 and len(list(jobs.folder.glob('*.payload')))==1
    assert doc['items'][0]['status']=='CANDIDATE_REQUIRES_REVIEW'
    assert doc['items'][1]['status']=='VERIFIED_EXTERNAL_MOVIE_NOT_IMPORTED'
    assert doc['items'][2]['status']=='SUCCEEDED'


def test_quality_network_failure_stops_after_bounded_read_retries_without_writes(tmp_path):
    import uuid
    from types import SimpleNamespace
    from catalog.quality_runs import QualityRuns
    from catalog.import_jobs import ImportJobs
    from catalog.tmdb_connection import TMDBError
    from test_admin_imports import Adapter
    adapter=Adapter();jobs=ImportJobs(tmp_path/'imports',adapter,lambda uid:True)
    calls=[]
    def fail(*args):calls.append(1);raise TMDBError('RATE_LIMITED',60)
    runs=QualityRuns(tmp_path/'runs',jobs,SimpleNamespace(_get=fail))
    runs._wait_for_network=lambda doc:None
    doc=dict(job_id=str(uuid.uuid4()),owner=1,scope=dict(gaps=[dict(movie_id=1,title='测试')],pending=[],refresh=[]),status='RUNNING',items=[],completed=0)
    runs._run(doc,'binding')
    assert doc['status']=='STOPPED' and doc['error_code']=='RATE_LIMITED' and len(calls)==1 and adapter.applied==0
    def unavailable(*args):calls.append(1);raise TMDBError('NETWORK_UNAVAILABLE')
    runs.service._get=unavailable;calls.clear();doc['status']='RUNNING'
    runs._run(doc,'binding')
    assert doc['status']=='STOPPED' and len(calls)==3 and adapter.applied==0


@pytest.mark.skipif(__import__('os').name!='posix',reason='Linux process lease')
def test_quality_live_lease_and_interrupted_process_no_replay(tmp_path):
    import uuid
    from types import SimpleNamespace
    from catalog.quality_runs import QualityRuns
    from catalog.import_jobs import ImportJobs
    from test_admin_imports import Adapter
    adapter=Adapter();jobs=ImportJobs(tmp_path/'imports',adapter,lambda uid:True)
    runs=QualityRuns(tmp_path/'runs',jobs,SimpleNamespace())
    doc=dict(job_id=str(uuid.uuid4()),owner=1,scope={},status='RUNNING',items=[],completed=0,created_at='now')
    runs.store.write(runs.store.path(doc['job_id']),doc)
    lease=runs._lease()
    assert runs.latest(1)['status']=='RUNNING' and runs._lease() is None
    assert runs.latest(2) is None
    lease.close()
    recovered=runs.latest(1)
    assert recovered['status']=='STOPPED' and recovered['error_code']=='PROCESS_INTERRUPTED_NO_AUTOMATIC_REPLAY'
    assert adapter.applied==0


def test_search_candidates_distinguish_remakes_missing_dates_and_insufficient_identity():
    from catalog.data_quality import classify_search
    target=dict(title='气球',year=2019)
    assert classify_search(target,[])['code']=='SEARCH_NO_MOVIE_CANDIDATE'
    assert classify_search(target,[dict(id=1,title='气球',release_date='2018-01-01')])['code']=='YEAR_CONFLICT'
    assert classify_search(target,[dict(id=1,title='气球',release_date='')])['code']=='UNDATED_CANDIDATE'
    assert classify_search(target,[dict(id=1,title='气球',release_date='2019-01-01')])['code']=='TITLE_YEAR_CANDIDATE_ONLY'
    assert classify_search(target,[dict(id=1,title='热气球之旅',release_date='2019-01-01')])['code']=='SEARCH_RESULTS_NOT_EXACT'


def test_missing_tmdb_movie_does_not_disable_valid_key_or_stop_other_items(tmp_path):
    import uuid
    from types import SimpleNamespace
    from catalog.quality_runs import QualityRuns
    from catalog.import_jobs import ImportJobs
    from catalog.tmdb_connection import TMDBConnection,TMDBError
    from test_admin_imports import Adapter
    calls=[]
    def get(user,binding,path,params):
        calls.append(path)
        if len(calls)==1:raise TMDBError('NOT_FOUND')
        return raw(int(path.split('/')[-1]))
    adapter=Adapter();adapter.engine=fixture();jobs=ImportJobs(tmp_path/'imports',adapter,lambda uid:True)
    runs=QualityRuns(tmp_path/'runs',jobs,SimpleNamespace(_get=get))
    doc=dict(job_id=str(uuid.uuid4()),owner=1,scope=dict(gaps=[],pending=[9000000001,9000000002],refresh=[]),status='RUNNING',items=[],completed=0)
    runs._run(doc,'binding')
    assert doc['status']=='COMPLETED' and doc['completed']==2 and adapter.applied==0
    assert doc['items'][0]['error_code']=='UPSTREAM_MOVIE_NOT_FOUND'
    connection=TMDBConnection(tmp_path/'tmdb');connection.put(1,'a',dict(access_token='SYNTHETIC_TOKEN_FOR_QUALITY_ONLY_123456',consent=True))
    connection._failed(1,'a',connection._entries[(1,'a')]['generation'],TMDBError('NOT_FOUND'))
    assert connection.status(1,'a')['retry_after']==0


def test_quality_resume_only_reads_remaining_targets_and_rejects_unknown_commit(tmp_path):
    import uuid
    from types import SimpleNamespace
    from catalog.quality_runs import QualityRuns
    from catalog.import_jobs import ImportJobs
    from catalog.admin_importer import digest
    from test_admin_imports import Adapter
    adapter=Adapter();adapter.engine=fixture();jobs=ImportJobs(tmp_path/'imports',adapter,lambda uid:True)
    calls=[]
    def get(user,binding,path,params):calls.append(path);return raw(int(path.split('/')[-1]))
    runs=QualityRuns(tmp_path/'runs',jobs,SimpleNamespace(_get=get))
    scope=dict(gaps=[dict(movie_id=1,title='已完成')],pending=[9000000001],refresh=[])
    doc=dict(job_id=str(uuid.uuid4()),owner=1,scope=scope,scope_digest=digest(scope),created_at='now',status='RUNNING',items=[dict(kind='gaps',status='SEARCH_NO_MOVIE_CANDIDATE')],completed=1)
    runs._run(doc,'binding')
    assert calls==['movie/9000000001'] and doc['completed']==2 and adapter.applied==0
    doc.update(status='STOPPED',error_code='COMMIT_REQUIRES_RECONCILIATION')
    runs.store.write(runs.store.path(doc['job_id']),doc)
    with pytest.raises(ImportRejected):runs.resume(doc['job_id'],1,'binding')
    with pytest.raises(PermissionError):runs.resume(doc['job_id'],2,'binding')
