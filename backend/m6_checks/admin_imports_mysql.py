"""Real MySQL checks ONLY in the separately provisioned import-check schema."""
import json,os,secrets,uuid
from pathlib import Path
from sqlalchemy import create_engine,select,func
from app.m6_database import verify_m6_connection
from catalog.admin_importer import CatalogImporter,ImportRejected,capture
from catalog.models import CatalogBase
from catalog.import_jobs import ImportJobs

SCHEMA='mi2_m6_importcheck_20261113'
_checks=[]
def main():
    cfg=json.loads(Path(os.environ['MI2_IMPORT_CHECK_CONFIG']).read_text())
    if cfg['M6_SCHEMA']!=SCHEMA:raise RuntimeError('TEST_SCOPE_REQUIRED')
    engine=create_engine(cfg.pop('url'),hide_parameters=True,pool_pre_ping=True)
    with engine.connect() as c:verify_m6_connection(c,cfg)
    importer=CatalogImporter(engine,cfg);checks=_checks
    def check(name,ok):
        checks.append(dict(name=name,passed=bool(ok)))
        if not ok:raise AssertionError(name)
    base=9000000000+secrets.randbelow(900000000)
    def raw(tid):return dict(id=tid,adult=False,title='合成接口验证 '+str(tid),original_title='Synthetic '+str(tid),release_date='2001-01-01',runtime=100,original_language='en',overview='synthetic fixture',genres=[{'id':9999999,'name':'合成测试类型'}],poster_path='/synthetic.jpg',backdrop_path='/synthetic-backdrop.jpg',vote_average=8.0,vote_count=100,credits={'cast':[dict(id=base+2,name='合成人物',credit_id='synthetic-'+str(tid),character='测试角色',order=0,profile_path='/synthetic.jpg')],'crew':[]})
    payload=capture(raw(base));plans=importer.preview([payload]);check('preview_append_readonly',plans[0]['action']=='append')
    def count(name):
        with engine.connect() as c:return c.scalar(select(func.count()).select_from(CatalogBase.metadata.tables[name]))
    before=count('movies')
    try:importer.apply(str(uuid.uuid4()),[payload],lambda _:(_ for _ in ()).throw(RuntimeError('Synthetic backup failure')),plans)
    except RuntimeError:pass
    check('backup_failure_no_write',count('movies')==before)
    journal=[];key=str(uuid.uuid4());first=importer.apply(key,[payload],journal.append,plans)
    check('real_mysql_committed',count('movies')==before+1 and first['items'][0]['movie_id']>0)
    check('backup_before_mutation',len(journal)==1 and journal[0]['movies']==[])
    replay=importer.apply(key,[payload],journal.append,plans)
    check('same_job_replay',replay['replayed'] and count('movies')==before+1)
    for name in ('movie_source_records','movie_external_ids','movie_ratings_summary','movie_assets','movie_credits','people','person_external_ids','movie_genres'):
        check('persist_'+name,count(name)>0)
    newer=importer.preview([payload]);second=importer.apply(str(uuid.uuid4()),[payload],journal.append,newer)
    check('same_source_new_job_no_duplicate',second['items'][0]['already_present'] and count('movies')==before+1)
    bad=capture(raw(base+1));bad['imdb_id']='tt'+str(base)
    with engine.begin() as c:
        e=CatalogBase.metadata.tables['movie_external_ids']
        c.execute(e.insert().values(movie_id=first['items'][0]['movie_id'],source='imdb',external_id='tt'+str(base),verified_method='synthetic',created_at=__import__('datetime').datetime.utcnow()))
    try:importer.preview([bad]);conflict=False
    except ImportRejected:conflict=True
    check('cross_movie_identity_conflict',conflict)
    changed=dict(payload,entries=[dict(payload['entries'][0],character='冲突测试')])
    before_sources=count('movie_source_records')
    try:importer.apply(str(uuid.uuid4()),[changed],journal.append,importer.preview([changed]));rolled=False
    except ImportRejected:rolled=True
    check('credit_conflict_atomic_rollback',rolled and count('movie_source_records')==before_sources)
    invalid_plan=[dict(newer[0],movie_id=None)]
    try:importer.apply(str(uuid.uuid4()),[payload],journal.append,invalid_plan);stopped=False
    except ImportRejected:stopped=True
    check('preview_target_drift_rejected',stopped)
    folder=Path(cfg['state'])/str(uuid.uuid4());jobs=ImportJobs(folder,importer,lambda uid:uid==1)
    job=jobs.preview(1,[payload],newer);jobs.approve(job['job_id'],1,job['digest']);jobs.process_one()
    check('durable_worker_real_sql_receipt',jobs.get(job['job_id'],1)['status']=='SUCCEEDED')
    doc=jobs.read(job['job_id']);doc['status']='RUNNING';jobs.write(jobs.path(job['job_id']),doc);jobs.process_one()
    check('crash_reconciles_committed_receipt',jobs.get(job['job_id'],1)['result']['reconciled'])
    check('readback_receipt_source_hashes',importer.receipt(key)['source_hashes']==first['source_hashes'])
    check('real_catalog_not_targeted',engine.url.database==SCHEMA)
    print(json.dumps(dict(all_pass=True,checks=checks,model_dispatches=0)))
if __name__=='__main__':
    try:main()
    except Exception as e:
        import traceback
        frames=traceback.extract_tb(e.__traceback__)
        print(json.dumps(dict(all_pass=False,checks=_checks,error_type=type(e).__name__+'@'+','.join(Path(f.filename).name+':'+str(f.lineno) for f in frames[-3:]))));raise SystemExit(1)
