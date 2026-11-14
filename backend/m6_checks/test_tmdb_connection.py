"""TMDB connection isolation/HTTP/cache tests. All network is explicitly mocked."""
import json,time,threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from contextlib import contextmanager
from unittest.mock import patch
import pytest
from flask import Flask
from catalog.tmdb_connection import TMDBConnection,TMDBError,using
from catalog.tmdb_api import install
from catalog import recommendation_boards as boards

TOKEN='SYNTHETIC_TMDB_NOT_A_REAL_CREDENTIAL_12345'
def transport(path,params,token):
    assert token==TOKEN
    if path=='configuration':return {'images':{'secure_base_url':'https://image.tmdb.org/t/p/'}}
    if path=='genre/movie/list':return {'genres':[{'id':18,'name':'剧情'}]}
    if path=='search/movie':return {'results':[dict(id=999,title=params['query'],release_date='2026-01-01')]}
    if path.startswith('movie/') and path[6:].isdigit():return dict(id=int(path[6:]),adult=False,title='TMDB电影',vote_count=10000,vote_average=8.5,popularity=120,revenue=1000000,credits={'cast':[dict(id=1,name='actor')],'crew':[dict(id=2,name='director',job='Director')]})
    return {'results':[dict(id=999+params['page'],title='官方影片',genre_ids=[18],adult=False,release_date='2026-01-01',popularity=8,vote_count=500,vote_average=9.)]}

@pytest.fixture
def service(tmp_path):
    c=TMDBConnection(tmp_path,transport=transport)
    # Remove pacing only in tests, never in the production HTTP transport.
    c._next_request=-999
    c.put(1,'a',dict(access_token=TOKEN,consent=True))
    return c

def test_secret_not_returned_or_persisted_and_bound_by_user_session(service):
    assert service.status(1,'a')['configured']
    assert not service.status(2,'a')['configured'] and not service.status(1,'b')['configured']
    service.put(1,'b',dict(access_token=TOKEN,consent=True))
    assert service.status(1,'a')['configured'] and service.status(1,'b')['configured']
    entry=service._entries[(1,'a')]
    assert TOKEN.encode() not in entry['secret'] and TOKEN not in json.dumps(service.status(1,'a'))
    assert not list(service.folder.glob('**/*.json'))
    service.disconnect_binding('a')
    assert not service.status(1,'a')['configured'] and service.status(1,'b')['configured']

@pytest.mark.parametrize('body',[{},dict(access_token=TOKEN),dict(access_token=TOKEN,consent=True,url='http://localhost'),dict(access_token='x\nAuthorization: evil',consent=True),dict(access_token=True,consent=True)])
def test_malformed_credentials_or_proxy_config_denied(service,body):
    with pytest.raises(ValueError):service.put(2,'b',body)

def test_expiration_clears_custody(tmp_path):
    c=TMDBConnection(tmp_path,ttl=1);c.put(1,'a',dict(access_token=TOKEN,consent=True))
    with patch('catalog.tmdb_connection.time.monotonic',return_value=time.monotonic()+2):assert not c.status(1,'a')['configured']

def test_bounded_source_ranks_refresh_and_restart_cache(service):
    doc=service.snapshot('popular',1,'a',force=True)
    assert [r['source_rank'] for r in doc['items']]==[1,21,41]
    assert service.verify(1,'a')['verification']=='VERIFIED'
    cached=service.snapshot('popular',1,'a',force=True)
    assert doc==cached
    reopened=TMDBConnection(service.folder)
    assert reopened.snapshot('popular')==doc and not reopened.status(1,'a')['configured']
    assert all(TOKEN not in p.read_text() for p in service.folder.glob('**/*.json'))

def test_rate_limit_no_retry_and_cache_preserved(service):
    doc=service.snapshot('popular',1,'a')
    doc['captured_at']='2000-01-01T00:00:00+00:00'
    calls=[]
    def limited(*args):calls.append(1);raise TMDBError('RATE_LIMITED',120)
    service._transport=limited
    assert service.snapshot('popular',1,'a')==doc
    assert service.snapshot('popular',1,'a')==doc and len(calls)==1
    status=service.status(1,'a');assert status['last_error']=='RATE_LIMITED' and status['retry_after']>100

def test_auth_rejection_never_hidden_retry_or_other_credential(service):
    calls=[]
    def invalid(*args):calls.append(1);raise TMDBError('KEY_REJECTED')
    service._transport=invalid
    with pytest.raises(TMDBError):service.verify(1,'a')
    assert service.status(1,'a')['verification']=='KEY_REJECTED'
    service.snapshot('popular',1,'a')
    assert len(calls)==1

def test_disconnect_or_replace_during_fetch_cannot_publish(service):
    started=threading.Event();release=threading.Event()
    def blocked(path,params,token):started.set();release.wait(5);return transport(path,params,token)
    service._transport=blocked
    with ThreadPoolExecutor() as pool:
        future=pool.submit(service.snapshot,'popular',1,'a',force=True)
        assert started.wait(5);service.disconnect(1,'a');release.set()
        with pytest.raises(TMDBError):future.result()
    assert service.snapshot('popular') is None

def test_online_details_enrich_only_metrics_not_frozen_facts(service):
    doc=service.detail(1,'a',121)
    row=dict(movie_id=9,tmdb_id='121',title='可信库内片名',release_year=1990,genres=['科幻'],tmdb_popularity=None,revenue=None)
    with using(service,1,'a'):
        result=service.enrich([dict(row)])[0]
        assert result['tmdb_rating']==8.5 and result['revenue']==1000000 and result['catalog_popularity']==1
        assert result['title']==row['title'] and result['release_year']==1990 and result['genres']==['科幻']
        from assistant.discovery import TMDBVerifier
        match=TMDBVerifier(token='').verify('新发现',2026)
        assert match['tmdb_id']==999 and match['movie_id'] is None
    assert doc['movie']['cast'][0]['id']==1

def test_ai_and_http_use_same_published_board(service):
    service.snapshot('popular',1,'a',force=True)
    with using(service,1,'a'):
        result=boards.board([],{},1,'popular')
        assert result['items'][0]['in_catalog'] is False
        assert result['items'][0]['source_rank']==1
        assert boards.public_snapshot('popular',online=False)['captured_at']==result['source_as_of']


def test_public_board_does_not_refresh_missing_or_stale_cache(service,monkeypatch):
    from catalog import board_snapshots
    monkeypatch.setattr(board_snapshots,'read',lambda channel:None)
    monkeypatch.setattr(boards,'DATA',service.folder/'absent-bundled-data')
    def unexpected(*args,**kwargs):
        pytest.fail('Public board attempted credential access or external refresh')
    monkeypatch.setattr(service,'_get',unexpected)
    monkeypatch.setattr(service,'_entry',unexpected)
    with using(service,1,'a'):
        assert boards.board([],{},1,'popular')['status']=='UNAVAILABLE'
        service._boards['popular']={'schema':1,'channel':'popular','captured_at':'2000-01-01T00:00:00+00:00','items':[]}
        result=boards.board([],{},1,'popular')
        assert result['status']=='STALE' and result['source_as_of']=='2000-01-01T00:00:00+00:00'


def test_cache_only_read_does_not_wait_for_refresh_lock(service):
    doc={'schema':1,'channel':'popular','captured_at':'2000-01-01T00:00:00+00:00','items':[]}
    service._boards['popular']=doc
    lock=service._resource_lock('popular')
    with lock:
        # A timed join prevents a lock regression from hanging the test suite.
        result=[]
        reader=threading.Thread(target=lambda:result.append(service.snapshot('popular')),daemon=True)
        reader.start();reader.join(1)
        finished=not reader.is_alive()
    reader.join(1)
    assert finished and result==[doc]


@pytest.mark.parametrize('broken',[
    {'schema':2,'channel':'popular','captured_at':'2000-01-01T00:00:00+00:00','items':[]},
    {'schema':1,'channel':'trending_day','captured_at':'2000-01-01T00:00:00+00:00','items':[]},
    {'schema':1,'channel':'popular','captured_at':'invalid','items':[]},
    {'schema':1,'channel':'popular','captured_at':'2000-01-01T00:00:00+00:00','items':{}},
])
def test_cache_only_read_rejects_invalid_documents(service,broken):
    service._boards['popular']=broken
    assert service.snapshot('popular') is None


def test_public_board_prefers_newer_scheduled_snapshot(service,monkeypatch):
    from catalog import board_snapshots
    service._boards['popular']={'schema':1,'channel':'popular','captured_at':'2000-01-01T00:00:00+00:00','items':[]}
    scheduled={'schema':1,'channel':'popular','captured_at':'2001-01-01T00:00:00+00:00','items':[]}
    monkeypatch.setattr(board_snapshots,'read',lambda channel:scheduled)
    with using(service,1,'a'):
        assert boards.board([],{},1,'popular')['source_as_of']==scheduled['captured_at']

def test_new_heat_does_not_redate_or_discard_newer_detail_revenue(service):
    service._details[121]={'schema':1,'captured_at':'2026-11-13T03:00:00+00:00','movie':{'id':121,'revenue':500,'popularity':1,'vote_average':7.,'vote_count':100}}
    service._boards['popular']={'schema':1,'captured_at':'2026-11-13T04:00:00+00:00','items':[{'id':121,'popularity':10,'vote_average':8.,'vote_count':200}]}
    row=dict(movie_id=1,tmdb_id='121',title='库内',revenue=100,feature_as_of='2026-11-12T00:00:00Z')
    updated=service.enrich([row])[0]
    assert updated['revenue']==500 and updated['revenue_as_of']=='2026-11-13T03:00:00+00:00'
    assert updated['tmdb_popularity']==10 and updated['feature_as_of']=='2026-11-13T04:00:00+00:00'

def test_reachable_official_host_is_reused_without_weakening_tls():
    from urllib.error import URLError
    calls=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):return b'{"images":{}}'
    def open(req,timeout):
        calls.append(req.host)
        if req.host=='api.themoviedb.org':raise URLError('synthetic timeout')
        return Response()
    with patch.object(TMDBConnection,'_preferred_host',None),patch.object(TMDBConnection,'_preferred_until',0),patch('urllib.request.build_opener') as builder:
        builder.return_value.open.side_effect=open
        TMDBConnection._http('configuration',{},TOKEN);TMDBConnection._http('configuration',{},TOKEN)
        assert calls==['api.themoviedb.org','api.tmdb.org','api.tmdb.org']

def test_no_redirect_no_query_secret_or_untrusted_url(service):
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):return b'{"images":{}}'
    with patch('urllib.request.build_opener') as builder:
        builder.return_value.open.return_value=Response()
        TMDBConnection._http('configuration',{},TOKEN)
        req=builder.return_value.open.call_args.args[0]
        assert TOKEN not in req.full_url and req.headers['Authorization']=='Bearer '+TOKEN
        assert any(getattr(h,'__name__',type(h).__name__)=='NoRedirect' for h in builder.call_args.args)
    for path in ['http://localhost/','../etc/passwd','movie/1?api_key=secret','movie/0']:
        with pytest.raises(TMDBError):TMDBConnection._http(path,{},TOKEN)

def test_http_guard_and_existing_csrf_contract(tmp_path,monkeypatch):
    import catalog.tmdb_api as api
    current={'user':1,'binding':'a','csrf':False}
    @contextmanager
    def begin():yield object()
    monkeypatch.setattr(api,'session_row',lambda db:SimpleNamespace(user_id=current['user'],sid_hash=current['binding']) if current['user'] else None)
    monkeypatch.setattr(api,'csrf_ok',lambda row:current['csrf'])
    app=Flask(__name__);app.config.update(TESTING=True,MEDIA_CACHE_DIR=str(tmp_path),TMDB_TEST_TRANSPORT=transport)
    app.extensions['auth_db']=SimpleNamespace(begin=begin);install(app);client=app.test_client()
    assert client.post('/api/v1/tmdb/config',json=dict(access_token=TOKEN,consent=True)).status_code==403
    current['csrf']=True
    assert client.post('/api/v1/tmdb/config',json=dict(access_token=TOKEN,consent=True)).status_code==200
    assert TOKEN not in client.get('/api/v1/tmdb/status').get_data(as_text=True)
    current['binding']='other';assert not client.get('/api/v1/tmdb/status').json['data']['configured']
    current['user']=None;assert client.get('/api/v1/tmdb/status').status_code==401
    current.update(user=1,binding='a');assert client.post('/api/v1/tmdb/config',json=dict(access_token=TOKEN,consent=True,user_id=2)).status_code==400
    assert client.get('/api/v1/tmdb/status?access_token=secret').status_code==400
    def failed(*args):raise RuntimeError(TOKEN)
    app.extensions['tmdb_connection']._transport=failed
    response=client.post('/api/v1/tmdb/verify',json={})
    assert response.status_code==503 and TOKEN not in response.get_data(as_text=True)
