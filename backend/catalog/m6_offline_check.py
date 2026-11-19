"""M6 offline counterexamples and optional READ-ONLY approved M5 replay.

No pytest, MySQL, authentication, credentials, network or target SQL writes.
"""
import argparse
import copy
import hashlib
import json
import pathlib
import shutil
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from sqlalchemy import CheckConstraint, UniqueConstraint, String
from catalog.m6_contract import authority, digest, full_row, validate_rows
from catalog.m6_offline import Snapshot, content_hash, projection_declaration, read_snapshot, preflight, MemorySandbox, simulate_import, simulate_ddl, execute_live, LiveIntegrationBlocked


def fixture(bundle):
    rows={n:[] for n in bundle['metadata'].tables};stamp=datetime(2026,11,6,tzinfo=timezone.utc)
    def add(n,**kw):rows[n].append(full_row(bundle,n,kw))
    raw={'id':900001,'title':'SYNTHETIC FILM','original_title':'Alias','release_date':'2000-01-01','runtime':100,
         'original_language':'en','overview':'SYNTHETIC ONLY','adult':False,'genres':[{'id':18,'name':'Drama'}],'vote_average':8.5,'vote_count':100}
    payload_hash=digest(raw)
    add('movies',movie_id=1001,title=raw['title'],original_title='Alias',release_date=date(2000,1,1),release_year=2000,runtime_minutes=100,original_language='en',overview='SYNTHETIC ONLY',catalog_status='active',is_adult=False,created_at=stamp,updated_at=stamp)
    add('genres',genre_id=2001,name_zh='Drama',tmdb_genre_id='18')
    add('people',person_id=3001,name='SYNTHETIC PERSON',created_at=stamp,updated_at=stamp)
    add('ingest_batches',batch_id=4001,source='tmdb',mode='dry-run',status='succeeded',started_at=stamp,finished_at=stamp,source_snapshot_ref='SYNTHETIC',counts_json={})
    add('movie_external_ids',id=5001,movie_id=1001,source='tmdb',external_id='900001',verified_method='synthetic',created_at=stamp)
    add('movie_source_records',id=6001,movie_id=1001,source='tmdb',source_record_id='900001',fetched_at=stamp,payload_hash=payload_hash,metadata_json=raw,ingest_batch_id=4001,license_status='allowed')
    add('movie_aliases',id=7001,movie_id=1001,title='Alias',title_normalized='alias',language='en',source='tmdb',created_at=stamp)
    add('movie_ratings_summary',movie_id=1001,source='tmdb',score_value=Decimal('8.50'),score_scale=10,vote_count=100,observed_at=stamp,selected_source_record_pk=6001)
    add('movie_genres',movie_id=1001,genre_id=2001)
    add('person_external_ids',id=8001,person_id=3001,source='tmdb',external_id='p1',verified_method='synthetic')
    add('movie_credits',credit_id=9001,movie_id=1001,person_id=3001,source='tmdb',source_credit_key='SYN-C1',role_type='cast',sort_order=0)
    add('movie_assets',asset_id=10001,movie_id=1001,asset_type='poster',source='tmdb',source_path='SYNTHETIC-NO-IMAGE',width=1,height=1,is_primary=True,license_status='allowed',updated_at=stamp)
    add('legacy_movie_map',id=11001,legacy_table='movies',legacy_pk=1,movie_id=1001,match_status='matched')
    add('ingest_items',id=12001,batch_id=4001,source_record_id='900001',status='inserted',movie_id=1001,payload_hash=payload_hash,attempt_count=1,updated_at=stamp)
    add('match_reviews',id=13001,source='tmdb',source_record_id='review-only',candidate_movie_id=1001,match_features_json={},status='pending')
    add('data_quality_issues',id=14001,batch_id=4001,source='tmdb',source_record_id='bad-only',issue_type='synthetic',severity='warning',status='open',detail_json={},created_at=stamp)
    return rows


def synthetic_snapshot(bundle,rows):
    from sqlalchemy import Integer,Boolean
    projections=projection_declaration()['TABLES'];schema={};source={n:copy.deepcopy(rows[n]) for n in projections}
    for name in projections:
        table=bundle['metadata'].tables[name]
        schema[name]={'columns':{c.name:{'type':'INTEGER' if isinstance(c.type,(Integer,Boolean)) else 'TEXT','notnull':0,
                       'pk':1 if c.primary_key and len(table.primary_key.columns)==1 else 0} for c in table.c},'unique_indexes':[]}
    for n,cols in [('movie_external_ids',['source','external_id']),('genres',['tmdb_genre_id']),('movie_genres',['movie_id','genre_id'])]:schema[n]['unique_indexes']=[cols]
    for row in source['movie_source_records']:row['ingest_batch_id']=None
    ledger=[]
    for row in source['movie_source_records']:
        ledger.append(dict(source='tmdb',source_id=row['source_record_id'],movie_id=row['movie_id'],raw_hash=row['payload_hash'],identity_signature='SYNTHETIC',
                    payload=json.dumps(row['metadata_json']),evidence=json.dumps({'raw_sha256':row['payload_hash'],'license_ref':'https://developer.themoviedb.org/docs/faq',
                    'source_url':f'https://api.themoviedb.org/3/movie/{row["source_record_id"]}?language=zh-CN','observed_at':'2026-11-06T00:00:00+00:00'})))
    seal=content_hash(source,schema,ledger)
    return Snapshot(source,schema,ledger,digest({'synthetic':seal}),seal,'SYNTHETIC_CONTROL')


def main(output,source=None,source_hash=None):
    out=pathlib.Path(output).resolve()
    if not out.is_dir() or out.drive.upper()=='C:' or any(out.iterdir()):raise ValueError('Fresh non-C output required')
    bundle=authority();base=fixture(bundle);checks=[]
    def check(name,ok):
        checks.append({'case':name,'pass':bool(ok)})
        if not ok:raise RuntimeError(name)
    def rejects(fn):
        try:fn()
        except (ValueError,LiveIntegrationBlocked):return True
        return False
    _,problems=validate_rows(bundle,base);check('complete synthetic 16-table fixture',not problems)
    check('frozen inventory',len(bundle['metadata'].tables)==16 and sum(len(t.c) for t in bundle['metadata'].tables.values())==128 and len(bundle['statements'])==35)
    clone=out/'authority-negative';clone.mkdir()
    from catalog.m6_contract import ROOT
    for name in bundle['hashes']:
        dest=clone/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
    for name in bundle['hashes']:
        dest=clone/name;original=dest.read_bytes();dest.write_bytes(original+b'\n')
        check('pinned artifact mutation rejected '+name,rejects(lambda:authority(clone)))
        dest.write_bytes(original)
    # All 29 CHECK declarations receive an explicit invalid-value witness.
    witnesses={
    'movies':[{'catalog_status':'bad'},{'release_year':2200},{'runtime_minutes':1501}],
    'movie_external_ids':[{'source':'bad'},{'external_id':''}],
    'movie_source_records':[{'source':'bad'},{'license_status':'bad'}],
    'movie_ratings_summary':[{'score_scale':0},{'vote_count':-1},{'score_value':'10.01'},{'vote_count':0}],
    'movie_credits':[{'role_type':'bad'},{'sort_order':-1}],
    'movie_assets':[{'asset_type':'bad'},{'width':0},{'height':0},{'license_status':'restricted'}],
    'legacy_movie_map':[{'legacy_table':'bad'},{'match_status':'bad'},{'movie_id':None},{'match_status':'blocked','reason_code':None},{'reason_code':'bad'}],
    'ingest_batches':[{'mode':'bad'},{'status':'bad'}],
    'ingest_items':[{'status':'bad'},{'attempt_count':0}],
    'match_reviews':[{'status':'bad'}],
    'data_quality_issues':[{'severity':'bad'},{'status':'bad'}]}
    witnessed=[]
    for table,changes in witnesses.items():
        for index,change in enumerate(changes):
            name=f'ck_{table}_{index}';bad=copy.deepcopy(base);bad[table][0].update(change)
            _,issues=validate_rows(bundle,bad);check('CHECK witness '+name,any(i['code']=='CHECK' and i['detail']==name for i in issues));witnessed.append(name)
    declared={c.name for t in bundle['metadata'].tables.values() for c in t.constraints if isinstance(c,CheckConstraint)}
    check('CHECK witness exact coverage',set(witnessed)==declared)
    for table in bundle['metadata'].tables.values():
        for col in table.c:
            if not col.nullable and col.computed is None:
                bad=copy.deepcopy(base);bad[table.name][0][col.name]=None;_,issues=validate_rows(bundle,bad)
                check('NOT NULL '+table.name+'.'+col.name,any(i['code']=='TYPE_OR_VALUE' and i['detail']==col.name for i in issues))
            if isinstance(col.type,String) and col.type.length and col.computed is None:
                bad=copy.deepcopy(base);bad[table.name][0][col.name]='x'*(col.type.length+1);_,issues=validate_rows(bundle,bad)
                check('length '+table.name+'.'+col.name,bool(issues))
        for fk in table.foreign_key_constraints:
            bad=copy.deepcopy(base);key=fk.elements[0].parent.name;bad[table.name][0][key]=99999999;_,issues=validate_rows(bundle,bad)
            check('FK '+table.name+'.'+','.join(e.parent.name for e in fk.elements),any(i['code']=='FK' and i['table']==table.name for i in issues))
        for uq in [table.primary_key]+[c for c in table.constraints if isinstance(c,UniqueConstraint)]:
            bad=copy.deepcopy(base);clone=copy.deepcopy(bad[table.name][0]);bad[table.name].append(clone)
            # Make nullable generated slots active when testing their uniqueness.
            if table.name=='match_reviews' and uq.name=='uq_match_reviews_1':
                for r in bad[table.name]:r['status']='approved'
            _,issues=validate_rows(bundle,bad)
            check('unique '+table.name+'.'+str(uq.name or 'PRIMARY_KEY'),any(i['code']=='UNIQUE' and i['detail']==(uq.name or 'PRIMARY_KEY') for i in issues))
    for table,column,value in [('movies','runtime_minutes',32768),('movies','movie_id',2**63),('movies','is_adult',2),
        ('movie_ratings_summary','score_value','NaN'),('movie_ratings_summary','score_value','8.501'),('movie_ratings_summary','score_value',8.5),
        ('movies','release_date','2000-02-30'),('movies','created_at','not-time'),('ingest_batches','counts_json','[]'),
        ('ingest_batches','counts_json','{"a":1,"a":2}'),('ingest_batches','counts_json','{"a":NaN}'),('movie_source_records','payload_hash','short')]:
        bad=copy.deepcopy(base);bad[table][0][column]=value;_,issues=validate_rows(bundle,bad);check('typed negative '+table+'.'+column+' '+str(value),bool(issues))
    pending=copy.deepcopy(base);clone=copy.deepcopy(pending['match_reviews'][0]);clone.update(id=13002,source_record_id='other-review');pending['match_reviews'].append(clone)
    check('nullable approval slots permit distinct pending rows',not validate_rows(bundle,pending)[1])
    mismatched=copy.deepcopy(base);extra=copy.deepcopy(mismatched['movies'][0]);extra['movie_id']=1002;mismatched['movies'].append(extra);mismatched['movie_ratings_summary'][0]['movie_id']=1002
    check('rating existing source PK wrong movie rejected by composite FK',any(i['code']=='FK' and i['detail']=='selected_source_record_pk,movie_id->movie_source_records' for i in validate_rows(bundle,mismatched)[1]))
    for table,column in [('movie_external_ids','core_source_slot'),('match_reviews','approved_source_record_id'),('movie_assets','primary_slot')]:
        mismatched=copy.deepcopy(base);mismatched[table][0][column]='forged'
        check('generated slot cannot be forged '+column,any(i['code']=='GENERATED_VALUE' for i in validate_rows(bundle,mismatched)[1]))
    ambiguous=copy.deepcopy(base);clone=copy.deepcopy(ambiguous['data_quality_issues'][0]);clone.update(id=14002,issue_type='SYNTHETIC');ambiguous['data_quality_issues'].append(clone)
    check('unknown server collation ASCII case collision reviewed',any(i['code']=='UNIQUE' for i in validate_rows(bundle,ambiguous)[1]))
    ambiguous=copy.deepcopy(base);ambiguous['data_quality_issues'][0]['issue_type']='未知类型'
    check('unproven Unicode unique collation fails closed',any(i['code']=='COLLATION_UNVERIFIED' for i in validate_rows(bundle,ambiguous)[1]))
    ambiguous=copy.deepcopy(base);ambiguous['movie_assets'][0]['source_path']+=' '
    check('PAD-space identifier ambiguity rejected',bool(validate_rows(bundle,ambiguous)[1]))
    snapshot=synthetic_snapshot(bundle,base);check('synthetic source full preflight',not preflight(snapshot,bundle)[1])
    for change in ['column','type','index','json','name','score','extra_id','ledger']:
        bad=copy.deepcopy(snapshot)
        if change=='column':bad.schema['movies']['columns'].pop('overview')
        if change=='type':bad.schema['movies']['columns']['runtime_minutes']['type']='TEXT'
        if change=='index':bad.schema['movie_external_ids']['unique_indexes']=[]
        if change=='json':bad.rows['movie_source_records'][0]['metadata_json']='[]'
        if change=='name':bad.rows['movies'][0]['title']='UNVERIFIED REPLACEMENT'
        if change=='score':bad.rows['movie_ratings_summary'][0]['score_value']='1.00'
        if change=='extra_id':bad.rows['movie_external_ids'].append(dict(bad.rows['movie_external_ids'][0],id=5002,source='wikidata',external_id='Q999'))
        if change=='ledger':bad.ledger[0]['raw_hash']='0'*64
        bad.seal=content_hash(bad.rows,bad.schema,bad.ledger)
        check('source preflight negative '+change,bool(preflight(bad,bundle)[1]))
    bad=copy.deepcopy(snapshot);bad.rows['movies'][0]['title']='mutated after seal'
    check('post-read mutation rejected',any(i.get('code')=='SOURCE_MEMORY_MUTATION' for i in preflight(bad,bundle)[1]))
    shifted=copy.deepcopy(base);shifted['movies'][0]['created_at']='2026-11-06T08:00:00+08:00'
    normalized,issues=validate_rows(bundle,shifted)
    check('UTC instant normalized explicitly',not issues and normalized['movies'][0]['created_at']==datetime(2026,11,6))
    shifted=copy.deepcopy(base);shifted['movies'][0]['created_at']='2026-11-06T00:00:00.1234567+00:00'
    check('timestamp precision not silently truncated',bool(validate_rows(bundle,shifted)[1]))
    shifted=copy.deepcopy(snapshot);shifted.rows['movies'][0]['created_at']='2026-11-06T00:00:00';shifted.seal=content_hash(shifted.rows,shifted.schema,shifted.ledger)
    check('ambiguous source time rejected',bool(preflight(shifted,bundle)[1]))
    target=MemorySandbox(bundle);receipt=simulate_import(snapshot,target,bundle);check('simulated import',receipt['status']=='SIMULATED_COMMITTED' and receipt['live'] is False)
    ids=receipt['mappings']['movies'];check('movie IDs independent of source and platform',ids[0]['destination_movie_id'] not in (1001,900001))
    check('batch identity not movie identity',receipt['destination_batch_id']!=ids[0]['destination_movie_id'])
    check('all mapping groups bound to explicit simulated target version',all(m['simulation'] is True and m['target_namespace']==target.namespace and bundle['contract_hash'] in m['target_version'] for group in receipt['mappings'].values() for m in group))
    check('source record batch repaired',all(r['ingest_batch_id']==receipt['destination_batch_id'] for r in target.rows['movie_source_records']))
    rating=target.rows['movie_ratings_summary'][0];record=target.rows['movie_source_records'][0]
    check('rating composite FK converted together',rating['selected_source_record_pk']==record['id'] and rating['movie_id']==record['movie_id'])
    check('all target constraints preflight',not validate_rows(bundle,target.rows)[1])
    immutable=copy.deepcopy(receipt);immutable['mappings']['movies'][0]['destination_movie_id']=0
    check('returned mapping cannot mutate stored audit receipt',simulate_import(snapshot,target,bundle)['mappings']['movies'][0]['destination_movie_id']!=0)
    before=digest(target.rows);reverse=copy.deepcopy(snapshot)
    for rows in reverse.rows.values():rows.reverse()
    check('reverse order idempotent',simulate_import(reverse,target,bundle)['replayed'] and digest(target.rows)==before)
    changed=copy.deepcopy(snapshot);changed.source_hash='1'*64
    check('changed source version reviewed not overwritten',simulate_import(changed,target,bundle)['status']=='SOURCE_VERSION_REVIEW_REQUIRED' and digest(target.rows)==before)
    dirty=MemorySandbox(bundle);dirty.rows['movies']=copy.deepcopy(base['movies'])
    check('nonempty unbound target refused',simulate_import(snapshot,dirty,bundle)['status']=='TARGET_NOT_EMPTY_REVIEW_REQUIRED')
    rollback=MemorySandbox(bundle)
    try:simulate_import(snapshot,rollback,bundle,fault='BEFORE_COMMIT')
    except RuntimeError:pass
    check('precommit rollback no rows receipts mappings',not any(rollback.rows.values()) and not rollback.receipts and rollback.bound_source is None)
    check('retry after rollback',simulate_import(snapshot,rollback,bundle)['status']=='SIMULATED_COMMITTED')
    uncertain=MemorySandbox(bundle);unknown=simulate_import(snapshot,uncertain,bundle,fault='AFTER_COMMIT_UNKNOWN');prior=digest(uncertain.rows)
    check('unknown commit no blind retry',unknown['status']=='COMMIT_UNKNOWN_DO_NOT_RETRY' and simulate_import(snapshot,uncertain,bundle)['status']==unknown['status'] and digest(uncertain.rows)==prior)
    check('explicit simulated audit confirms committed',uncertain.audit_commit()['status']=='SIMULATED_AUDIT_COMMITTED' and simulate_import(snapshot,uncertain,bundle)['replayed'])
    corrupted=MemorySandbox(bundle);simulate_import(snapshot,corrupted,bundle,fault='AFTER_COMMIT_UNKNOWN');corrupted.rows['movies'][0]['title']='SYNTHETIC CORRUPTION'
    check('unknown commit inconsistent audit remains blocked',corrupted.audit_commit()['status']=='SIMULATED_AUDIT_INCONSISTENT_DO_NOT_RETRY' and simulate_import(snapshot,corrupted,bundle)['status']=='COMMIT_UNKNOWN_DO_NOT_RETRY')
    check('live always denied even self-attested proof',rejects(lambda:execute_live({'all_gates_pass':True})))
    check('arbitrary destination adapter denied',rejects(lambda:simulate_import(snapshot,object(),bundle)))
    damaged=MemorySandbox(bundle);simulate_import(snapshot,damaged,bundle)
    damaged.rows['movies'][0]['title']='SYNTHETIC TAMPER'
    check('committed target tamper blocks replay',simulate_import(snapshot,damaged,bundle)['status']=='TARGET_STATE_AUDIT_REQUIRED')
    import sqlite3
    from catalog.local_catalog_store import LocalCatalogStore
    from unittest.mock import patch
    stable=out/'stable-synthetic.sqlite3'
    LocalCatalogStore(stable,initialize=True)
    stable_hash=hashlib.sha256(stable.read_bytes()).hexdigest()
    stable_snapshot=read_snapshot(stable,stable_hash,bundle)
    check('stable standalone snapshot accepted',stable_snapshot.source_hash==stable_hash and not preflight(stable_snapshot,bundle)[1])
    for suffix in ('-wal','-shm','-journal'):
        sidecar=pathlib.Path(str(stable)+suffix);sidecar.write_bytes(b'SYNTHETIC')
        check('standalone sidecar rejected '+suffix,rejects(lambda:read_snapshot(stable,stable_hash,bundle)))
        sidecar.unlink()
    for linked in (stable,stable.parent):
        original_is_symlink=pathlib.Path.is_symlink
        with patch.object(pathlib.Path,'is_symlink',lambda p:p==linked or original_is_symlink(p)):
            check('portable injected link predicate '+linked.name,rejects(lambda:read_snapshot(stable,stable_hash,bundle)))
    probe=out/'snapshot-probe.sqlite3'
    conn=sqlite3.connect(probe);conn.execute('CREATE TABLE synthetic_only(x)');conn.commit()
    pin=hashlib.sha256(probe.read_bytes()).hexdigest()
    conn.execute('PRAGMA journal_mode=WAL');conn.execute('INSERT INTO synthetic_only VALUES (1)');conn.commit()
    wal_pin=hashlib.sha256(probe.read_bytes()).hexdigest()
    conn.execute('INSERT INTO synthetic_only VALUES (2)');conn.commit()
    check('WAL bypass main hash unchanged',hashlib.sha256(probe.read_bytes()).hexdigest()==wal_pin)
    check('WAL source rejected before parsing',rejects(lambda:read_snapshot(probe,wal_pin,bundle)))
    conn.close()
    link=out/'snapshot-link.sqlite3'
    try:
        link.symlink_to(probe)
    except OSError:
        checks.append({'case':'native symlink capability unavailable; no privilege change','pass':None,'coverage':'UNVERIFIED'})
    else:
        check('native source symlink rejected',rejects(lambda:read_snapshot(link,hashlib.sha256(probe.read_bytes()).hexdigest(),bundle)))
    ddl=simulate_ddl(bundle);check('DDL complete simulation only',ddl['status']=='SIMULATED_DDL_COMPLETE' and len(ddl['journal'])==35 and not ddl['live'])
    journal=[];partial=simulate_ddl(bundle,fail_at=3,journal=journal)
    check('partial DDL preserved without rollback/drop',partial['status']=='PARTIAL_DDL_AUDIT_REQUIRED' and len(journal)==3 and not partial['automatic_cleanup'])
    check('partial target not blindly retried',rejects(lambda:simulate_ddl(bundle,journal=journal)))
    tampered=dict(bundle,statements=copy.deepcopy(bundle['statements']));tampered['statements'][0]['sql']+=' -- tampered'
    check('DDL memory tamper rejected',rejects(lambda:simulate_ddl(tampered)))
    real_receipt=None
    if source:
        original=hashlib.sha256(pathlib.Path(source).read_bytes()).hexdigest();real=read_snapshot(source,source_hash,bundle)
        check('approved real source detailed preflight',not preflight(real,bundle)[1]);real_target=MemorySandbox(bundle,'REAL_SOURCE_OFFLINE_ONLY')
        real_receipt=simulate_import(real,real_target,bundle)
        check('two real inputs simulated, not live writes',real_receipt['counts']['movies']==2 and not real_receipt['live'])
        check('real source unchanged',hashlib.sha256(pathlib.Path(source).read_bytes()).hexdigest()==original==source_hash)
    (out/'m6-offline-results.json').write_text(json.dumps({'checks':checks,'scope':'offline value/relationship and memory protocol tests; NOT real MySQL'},ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'m6-offline-mapping-audit.json').write_text(json.dumps({'synthetic':receipt,'real_source_offline_only':real_receipt,'live_execution':False},ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'m6-ddl-manifest.json').write_text(json.dumps({'artifact_hashes':bundle['hashes'],'contract_hash':bundle['contract_hash'],'statements':bundle['statements'],'execution':'NOT EXECUTED ON DATABASE'},ensure_ascii=False,indent=2),encoding='utf-8')
    print('M6 offline checks',sum(c['pass'] is True for c in checks),'PASS;',sum(c['pass'] is None for c in checks),'UNVERIFIED; live MySQL NOT RUN')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--source');parser.add_argument('--source-sha256')
    args=parser.parse_args();main(args.output,args.source,args.source_sha256)
