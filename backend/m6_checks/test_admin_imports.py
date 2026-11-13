import json,threading,uuid
from contextlib import contextmanager
from types import SimpleNamespace
from datetime import datetime,timezone,timedelta
import pytest
from flask import Flask
from catalog.admin_importer import capture,ImportRejected,digest
from catalog.import_jobs import ImportJobs
from catalog import admin_import_api

def raw(tid=9000000001):
 return dict(id=tid,adult=False,title='合成测试电影',original_title='Synthetic',release_date='2001-01-01',runtime=100,original_language='en',overview='synthetic',genres=[{'id':35,'name':'喜剧'}],poster_path='/synthetic.jpg',vote_average=8.0,vote_count=100,credits={'cast':[dict(id=9000000002,name='合成人物',credit_id='synthetic-credit',character='测试角色',order=0,profile_path='/synthetic.jpg')],'crew':[]})
class Adapter:
 def __init__(self):self.applied=0;self.receipts={}
 def receipt(self,key):return self.receipts.get(key)
 def preview(self,payloads):return [dict(tmdb_id=p['id'],title=p['title'],movie_id=None,action='append') for p in payloads]
 def apply(self,key,payloads,backup,expected_plans=None):
  backup({'movies':[]});self.applied+=1
  self.receipts[key]=dict(batch_id=1,items=[{'movie_id':1}]);return self.receipts[key]
def fixture(tmp_path):
 adapter=Adapter();jobs=ImportJobs(tmp_path,adapter,lambda uid:uid==1);p=capture(raw());job=jobs.preview(1,[p],adapter.preview([p]));return adapter,jobs,job

def test_capture_reuses_movie_validator_and_rejects_bad_assets():
 p=capture(raw());assert p['entries'][0]['role']=='cast'
 for changed in ({'adult':True},{'poster_path':'http://internal/'},{'vote_count':-1},{'genres':'bad'}):
  with pytest.raises((ValueError,TypeError)):capture(dict(raw(),**changed))

def test_durable_approval_idempotence_and_ownership(tmp_path):
 adapter,jobs,job=fixture(tmp_path)
 with pytest.raises(PermissionError):jobs.get(job['job_id'],2)
 with pytest.raises(ImportRejected):jobs.approve(job['job_id'],1,'different')
 assert adapter.applied==0 and jobs.process_one() is False
 jobs.approve(job['job_id'],1,job['digest']);jobs.approve(job['job_id'],1,job['digest'])
 replacement=ImportJobs(tmp_path,adapter,lambda _:True);assert replacement.process_one()
 assert replacement.get(job['job_id'],1)['status']=='SUCCEEDED' and adapter.applied==1
 assert not replacement.process_one()
 assert (tmp_path/(job['job_id']+'.before')).is_file()
 assert 'payloads' not in replacement.get(job['job_id'],1)

def test_expiry_cancel_seal_and_revoke(tmp_path):
 adapter,jobs,job=fixture(tmp_path);doc=jobs.read(job['job_id']);doc['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat();jobs.write(jobs.path(doc['job_id']),doc)
 with pytest.raises(ImportRejected):jobs.approve(doc['job_id'],1,doc['digest'])
 jobs.cancel(doc['job_id'],1);assert jobs.get(doc['job_id'],1)['status']=='CANCELLED'
 _,jobs,job=fixture(tmp_path/'second');jobs.approve(job['job_id'],1,job['digest']);rawpath=jobs.folder/(job['job_id']+'.payload');doc=json.loads(rawpath.read_text());doc['payloads'][0]['title']='tampered';jobs.write(rawpath,doc);jobs.process_one();assert jobs.get(job['job_id'],1)['error_code']=='SEALED_SOURCE_CHANGED'
 _,jobs,job=fixture(tmp_path/'third');jobs.approve(job['job_id'],1,job['digest']);jobs.authorized=lambda _:False;jobs.process_one();assert jobs.get(job['job_id'],1)['error_code']=='ADMIN_ACCESS_REVOKED'

def test_interrupted_commit_reconciled_without_replay(tmp_path):
 adapter,jobs,job=fixture(tmp_path);doc=jobs.read(job['job_id']);doc['status']='RUNNING';jobs.write(jobs.path(doc['job_id']),doc);jobs.process_one();assert jobs.get(doc['job_id'],1)['status']=='NEEDS_REVIEW'
 doc['status']='RUNNING';jobs.write(jobs.path(doc['job_id']),doc);adapter.receipts[job['job_id']]={'batch_id':7};jobs.process_one();assert jobs.get(doc['job_id'],1)['result']['reconciled'] and adapter.applied==0

def test_poll_does_not_wait_for_database_and_worker_serialized(tmp_path):
 adapter,jobs,job=fixture(tmp_path);jobs.approve(job['job_id'],1,job['digest']);entered=threading.Event();release=threading.Event();apply=adapter.apply
 def slow(*a,**kw):entered.set();release.wait(5);return apply(*a,**kw)
 adapter.apply=slow;thread=threading.Thread(target=jobs.process_one);thread.start();assert entered.wait(2)
 try:
  assert jobs.get(job['job_id'],1)['status']=='RUNNING';assert not jobs.process_one()
 finally:release.set();thread.join()
 assert adapter.applied==1

def api_app(tmp_path,monkeypatch,identity='admin',csrf=True):
 app=Flask(__name__);adapter=Adapter();app.config.update(TESTING=True,ADMIN_USERS=['admin'],MEDIA_CACHE_DIR=str(tmp_path),IMPORT_TEST_ADAPTER=adapter)
 user=SimpleNamespace(id=1,status='active',username_normalized=identity)
 class DB:
  def get(self,*_):return user
 class Factory:
  @contextmanager
  def begin(self):yield DB()
 app.extensions.update(auth_engine=object(),auth_db=Factory(),tmdb_connection=SimpleNamespace(_get=lambda *a,**kw:raw()))
 monkeypatch.setattr(admin_import_api,'session_row',lambda _:None if identity=='anonymous' else SimpleNamespace(user_id=1,sid_hash='synthetic-binding'))
 monkeypatch.setattr(admin_import_api,'csrf_ok',lambda _:csrf)
 admin_import_api.install(app);app.extensions['admin_import_jobs'].start=lambda:None
 return app
@pytest.mark.parametrize('identity,status',[('anonymous',401),('viewer',403)])
def test_rbac_before_fetch_or_write(tmp_path,monkeypatch,identity,status):
 c=api_app(tmp_path,monkeypatch,identity).test_client();assert c.post('/api/admin/imports/preview',json={'kind':'tmdb_catalog','tmdb_ids':[1]}).status_code==status

def test_csrf_input_scope_and_explicit_confirm(tmp_path,monkeypatch):
 c=api_app(tmp_path,monkeypatch,csrf=False).test_client();assert c.post('/api/admin/imports/preview',json={}).status_code==403
 c=api_app(tmp_path/'ok',monkeypatch).test_client()
 for body in ({'kind':'sql','tmdb_ids':[1]},{'kind':'tmdb_catalog','tmdb_ids':[True]},{'kind':'tmdb_catalog','tmdb_ids':[1,1]},{'kind':'tmdb_catalog','tmdb_ids':[1],'path':'/etc/passwd'}):assert c.post('/api/admin/imports/preview',json=body).status_code==400
 r=c.post('/api/admin/imports/preview',json={'kind':'tmdb_catalog','tmdb_ids':[9000000001]});assert r.status_code==201
 job=r.json['data'];assert 'payloads' not in job
 assert c.post('/api/admin/imports',json={'preview_id':job['job_id'],'digest':job['digest']}).status_code==400
 assert c.post('/api/admin/imports',json={'preview_id':job['job_id'],'digest':job['digest'],'confirm':True}).status_code==202
 assert c.get('/api/admin/imports/../../../etc/passwd').status_code==404


def test_explicit_reconcile_only_reads_receipt(tmp_path):
 adapter,jobs,job=fixture(tmp_path);doc=jobs.read(job['job_id']);doc['status']='NEEDS_REVIEW';jobs.write(jobs.path(doc['job_id']),doc)
 assert jobs.reconcile(job['job_id'],1)['receipt_found'] is False
 assert jobs.get(job['job_id'],1)['status']=='NEEDS_REVIEW' and adapter.applied==0
 with pytest.raises(PermissionError):jobs.reconcile(job['job_id'],2)
 adapter.receipts[job['job_id']]={'batch_id':9}
 assert jobs.reconcile(job['job_id'],1)['status']=='SUCCEEDED' and adapter.applied==0
