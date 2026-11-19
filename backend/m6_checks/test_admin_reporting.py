from datetime import datetime,timedelta
from types import SimpleNamespace
from werkzeug.datastructures import MultiDict
from sqlalchemy import Table,Column,Integer,String,DateTime,JSON,insert
import json,pytest,uuid
from test_admin_dashboard import _fixture_engine,_rows,_app
from analytics.admin_reporting import ai_usage,import_audit,audit_args
from catalog.import_jobs import ImportJobs
from test_admin_imports import Adapter


def test_token_groups_missing_provenance_cache_subset_and_no_double_counting():
    e,md=_fixture_engine();now=datetime(2026,1,2)
    rows=[dict(model='deepseek-flash',status='SUCCEEDED',created_at=now,tokens={'kind':'BYOK_AGENT','paid_request':True,'input_tokens':100,'output_tokens':20,'cached_input_tokens':40}),
          dict(model='deepseek-flash',status='UNKNOWN',created_at=now,tokens={'kind':'BYOK_AGENT','paid_request':True,'input_tokens':10,'output_tokens':None,'cached_input_tokens':99}),
          dict(model='mock-agent',status='SUCCEEDED',created_at=now,tokens={'input_tokens':8,'output_tokens':2}),
          dict(model='legacy',status='UNKNOWN',created_at=now,tokens={'input_tokens':True,'output_tokens':-1}),
          dict(model='deepseek-flash',status='NOT_DISPATCHED',created_at=now,tokens={'input_tokens':99999,'output_tokens':99999})]
    _rows(e,md,'usage',rows);app=SimpleNamespace(extensions={'m7_engine':e,'m7_ledger':SimpleNamespace(md=md)})
    result=ai_usage(app,now-timedelta(days=1),now+timedelta(days=1));g={r['provenance']:r for r in result['groups']}
    assert result['calls']==4 and result['not_dispatched_calls']==1
    assert g['REAL']['known_total_tokens']==130 and g['REAL']['known_cached_input_tokens']==40
    assert g['REAL']['total_tokens'] is None and g['REAL']['cached_input_reported_calls']==1
    assert g['MOCK']['calls']==1 and g['UNCONFIRMED']['known_input_tokens'] is None
    assert result['known_total_tokens']==140 and result['amount'] is None
    assert all('user_id' not in m for m in result['models'])
    empty=ai_usage(app,now+timedelta(days=2),now+timedelta(days=3))
    assert empty['calls']==0 and empty['total_tokens']==0 and empty['amount_status']=='NOT_CALLED'
    assert ai_usage(SimpleNamespace(extensions={}),now,now)['status']=='UNAVAILABLE'


def audit_fixture(tmp_path):
    e,md=_fixture_engine()
    t=Table('ingest_batches',md,Column('batch_id',Integer),Column('source',String),Column('mode',String),Column('status',String),Column('started_at',DateTime),Column('finished_at',DateTime),Column('source_snapshot_ref',String),Column('counts_json',JSON));md.create_all(e)
    _rows(e,md,'auth_users',[dict(id=1,status='active',created_at=datetime(2026,1,1))])
    # Real User mapping selects the username column; add it to this synthetic fixture.
    with e.begin() as c:c.exec_driver_sql('ALTER TABLE auth_users ADD COLUMN username TEXT');c.exec_driver_sql("UPDATE auth_users SET username='审计管理员'")
    jobs=ImportJobs(tmp_path,Adapter(),lambda _:True);base=datetime(2026,1,2)
    docs=[]
    for n in range(6):
        key=str(uuid.uuid4());when=base+timedelta(minutes=n*2)
        doc=dict(job_id=key,owner=1,status='SUCCEEDED' if n%2 else 'CANCELLED',kind='tmdb_catalog',created_at=when.isoformat()+'Z',updated_at=when.isoformat()+'Z',plans=[dict(title='影片'+str(n),movie_id=n+1,tmdb_id=n+1)],payloads={'secret':'MUST_NOT_LEAK'},backup_path='/private/MUST_NOT_LEAK')
        jobs.write(jobs.path(key),doc);docs.append(doc)
    batches=[dict(batch_id=n+1,source='tmdb',mode='write',status='succeeded',started_at=base+timedelta(minutes=n),finished_at=base+timedelta(minutes=n),source_snapshot_ref='legacy:'+str(n),counts_json={'movies':1,'password':123456,'payload':'MUST_NOT_LEAK'}) for n in range(20)]
    batches.append(dict(batches[1],batch_id=100,source_snapshot_ref='admin-import:'+docs[1]['job_id']))
    _rows(e,md,'ingest_batches',batches)
    return SimpleNamespace(extensions={'auth_engine':e,'admin_import_jobs':jobs}),base,docs


def test_import_audit_merges_receipt_and_file_tasks_with_stable_pagination_and_no_secrets(tmp_path):
    app,start,docs=audit_fixture(tmp_path);end=start+timedelta(days=1)
    results=[import_audit(app,start,end,page,10,'ALL') for page in (1,2,3)]
    items=[i for r in results for i in r['items']]
    assert len(items)==26 and len({i['id'] for i in items})==26 and all(r['total']==26 for r in results)
    assert [i['at'] for i in items]==sorted([i['at'] for i in items],reverse=True)
    job=next(i for i in items if i['id']==docs[1]['job_id']);assert job['batch_id']==100 and job['commit_state']=='SUCCEEDED'
    assert 'MUST_NOT_LEAK' not in json.dumps(results) and '123456' not in json.dumps(results)
    assert len(import_audit(app,start,end,1,25,'CANCELLED')['items'])==3
    assert import_audit(app,start,end,100,25,'ALL')['items']==[]


def test_import_audit_expiry_new_movie_receipts_and_unreadable_records(tmp_path):
    app,start,docs=audit_fixture(tmp_path);jobs=app.extensions['admin_import_jobs'];end=start+timedelta(days=1)
    expired=dict(docs[0],status='PREVIEW',expires_at=start.isoformat()+'Z')
    jobs.write(jobs.path(expired['job_id']),expired)
    new_movie=dict(docs[1],plans=[dict(title='新影片',movie_id=None,tmdb_id=99)])
    jobs.write(jobs.path(new_movie['job_id']),new_movie)
    with app.extensions['auth_engine'].begin() as c:
        from sqlalchemy import text
        c.execute(text('UPDATE ingest_batches SET counts_json=:counts WHERE batch_id=100'),{'counts':json.dumps({'items':[{'movie_id':1234,'tmdb_id':99}],'password':123456})})
    corrupt=jobs.path(str(uuid.uuid4()));corrupt.write_text('{bad',encoding='utf-8')
    result=import_audit(app,start,end,1,50,'ALL')
    assert result['unreadable_task_records']==1
    actual=next(r for r in result['items'] if r['job_id']==new_movie['job_id'])
    assert actual['movies'][0]['movie_id']==1234 and actual['counts']=={'movies':1}
    assert import_audit(app,start,end,1,25,'EXPIRED')['items'][0]['job_id']==expired['job_id']
    assert jobs.read(expired['job_id'])['status']=='PREVIEW'  # Reporting never mutates task state.


@pytest.mark.parametrize('identity,status',[('anonymous',401),('viewer',403)])
def test_new_reports_require_real_admin_session(monkeypatch,identity,status):
    client=_app(monkeypatch,identity).test_client()
    for path in ('ai-usage','import-audit'):assert client.get('/api/admin/'+path+'?admin=1').status_code==status


def test_report_parameter_guards(monkeypatch):
    client=_app(monkeypatch).test_client()
    for query in ('days=0','days=7&days=30','page=0','status=SQL','page_size=1000','path=/private'):
        assert client.get('/api/admin/import-audit?'+query).status_code==400
    assert client.get('/api/admin/ai-usage?days=7&days=30').status_code==400
    assert audit_args(MultiDict())==(30,1,25,'ALL')
