"""Explicit append-only static capture import. No DDL or legacy DB access."""
import json,os
from pathlib import Path
from datetime import datetime,timezone
from sqlalchemy import select,insert,update,text,create_engine
from app import create_app
from app.m6_database import verify_m6_connection,validate_m6_url
from .m6_contract import authority,digest
from .m6_offline import read_snapshot,preflight,rows_hash


def genre_plan(source,target):
    existing={str(r['tmdb_genre_id']):r for r in target if r['tmdb_genre_id'] is not None}
    result={}
    for row in source:
        old=existing.get(str(row['tmdb_genre_id']))
        if old and any(old.get(k)!=row.get(k) for k in ('name_zh','name_en')):raise ValueError('GENRE_METADATA_CONFLICT')
        result[row['genre_id']]=old['genre_id'] if old else None
    return result


def collision_check(source,target):
    owned={(r['source'],r['external_id']) for r in target}
    if any((r['source'],r['external_id']) in owned for r in source):raise ValueError('EXTERNAL_ID_ALREADY_OWNED')


def run(path,sha,state_dir,expected_count=20,dry_run=False):
    app=create_app();cfg=app.config;engine=app.extensions['auth_engine']
    if cfg['M6_SCHEMA']!='mi2_m6_realcopy_20261107':raise ValueError('TARGET_SCOPE_MISMATCH')
    bundle=authority();md=bundle['metadata'];snap=read_snapshot(Path(path),sha,bundle);rows,issues=preflight(snap,bundle)
    if issues or len(rows['movies'])!=expected_count:raise ValueError('SOURCE_PREFLIGHT_REJECTED')
    audit=create_engine(os.environ['MI2_AUDIT_DATABASE_URL'],hide_parameters=True,pool_pre_ping=True);validate_m6_url(audit.url,cfg,audit=True)
    ref='m9-append-sha256:'+sha;lock='m6import:'+cfg['M6_SCHEMA'];intent=Path(state_dir)/('m9-append-'+sha+'.json');c=engine.connect();locked=False
    def fetch(con,name):return [dict(r) for r in con.execute(select(md.tables[name])).mappings()]
    def verify(con,receipt):
        batch=con.execute(select(md.tables['ingest_batches']).where(md.tables['ingest_batches'].c.source_snapshot_ref==ref)).mappings().all()
        if len(batch)!=1 or batch[0]['status']!='succeeded' or batch[0]['counts_json']!=receipt:raise ValueError('RECEIPT_AUDIT_FAILED')
        for item in receipt['mapping']:
            found=con.execute(select(md.tables['movie_external_ids']).where(md.tables['movie_external_ids'].c.source=='tmdb',md.tables['movie_external_ids'].c.external_id==item['tmdb_id'])).mappings().one()
            if found['movie_id']!=item['movie_id']:raise ValueError('IDENTITY_AUDIT_FAILED')
        # Recheck every captured catalog row from the transaction, not only IDs.
        actual={}
        for name,keys in receipt['inserted_keys'].items():
            table=md.tables[name];pk=list(table.primary_key.columns)
            from sqlalchemy import and_,or_
            stmt=select(table).where(or_(*(and_(*(col==values[i] for i,col in enumerate(pk))) for values in keys)))
            actual[name]=[dict(r) for r in con.execute(stmt).mappings()]
        if rows_hash(actual)!=receipt['inserted_rows_hash']:raise ValueError('CONTENT_AUDIT_FAILED')
    try:
        verify_m6_connection(c,cfg);c.commit()
        if c.execute(text('SELECT GET_LOCK(:n,10)'),{'n':lock}).scalar()!=1:raise ValueError('CONCURRENT_IMPORT_BUSY')
        locked=True;c.commit()
        existing=c.execute(select(md.tables['ingest_batches']).where(md.tables['ingest_batches'].c.source_snapshot_ref==ref)).mappings().all()
        if existing:
            if len(existing)!=1:raise ValueError('AMBIGUOUS_RECEIPT')
            receipt=existing[0]['counts_json'];verify(c,receipt);c.rollback()
            with audit.connect() as ac:verify_m6_connection(ac,cfg,audit=True);verify(ac,receipt)
            if intent.exists():intent.rename(intent.with_suffix('.audited.json'))
            return dict(inserted=0,replayed=True,mapping=receipt['mapping'],audit_passed=True)
        if intent.exists():raise ValueError('PRIOR_COMMIT_UNKNOWN_MANUAL_AUDIT_REQUIRED')
        c.rollback();tx=c.begin();before={n:fetch(c,n) for n in md.tables};collision_check(rows['movie_external_ids'],before['movie_external_ids']);genres=genre_plan(rows['genres'],before['genres'])
        if dry_run:tx.rollback();return {'dry_run':True,'source_movies':expected_count,'existing_movies':len(before['movies']),'collisions':0}
        stamp=datetime.now(timezone.utc).replace(tzinfo=None)
        def put(name,value):return c.execute(insert(md.tables[name]).values(**value)).inserted_primary_key[0]
        batch=put('ingest_batches',dict(source='tmdb',mode='write',status='running',started_at=stamp,source_snapshot_ref=ref,counts_json={}))
        movie_map={};source_map={};mapping=[]
        for row in rows['genres']:
            if genres[row['genre_id']] is None:genres[row['genre_id']]=put('genres',{k:v for k,v in row.items() if k!='genre_id'})
        for row in rows['movies']:
            mid=put('movies',{k:v for k,v in row.items() if k!='movie_id'});movie_map[row['movie_id']]=mid
            external=next(x['external_id'] for x in rows['movie_external_ids'] if x['movie_id']==row['movie_id'] and x['source']=='tmdb');mapping.append({'tmdb_id':external,'movie_id':mid})
        for row in rows['movie_source_records']:
            values={k:v for k,v in row.items() if k not in ('id','movie_id','ingest_batch_id')};values.update(movie_id=movie_map[row['movie_id']],ingest_batch_id=batch);source_map[row['id']]=put('movie_source_records',values)
            put('ingest_items',dict(batch_id=batch,source_record_id=row['source_record_id'],status='inserted',movie_id=values['movie_id'],payload_hash=row['payload_hash'],attempt_count=1,updated_at=stamp))
        for name in ('movie_external_ids','movie_aliases','movie_genres','movie_ratings_summary'):
            for row in rows[name]:
                value={k:v for k,v in row.items() if k!='id' and md.tables[name].c[k].computed is None};value['movie_id']=movie_map[row['movie_id']]
                if name=='movie_genres':value['genre_id']=genres[row['genre_id']]
                if name=='movie_ratings_summary':value['selected_source_record_pk']=source_map[row['selected_source_record_pk']]
                c.execute(insert(md.tables[name]).values(**value))
        after={n:fetch(c,n) for n in md.tables};added={};keys={}
        for name,old in before.items():
            pk=[col.name for col in md.tables[name].primary_key.columns];identity=lambda r:tuple(r[k] for k in pk);lookup={identity(r):r for r in after[name]}
            if any(lookup.get(identity(row))!=row for row in old):raise ValueError('EXISTING_ROW_CHANGED')
            prior={identity(row) for row in old};added[name]=[row for row in after[name] if identity(row) not in prior]
        # Batch status/receipt is audited separately to avoid a self-referential hash.
        added.pop('ingest_batches',None)
        for name,values in added.items():
            if values:keys[name]=[[row[col.name] for col in md.tables[name].primary_key.columns] for row in values]
        added={n:v for n,v in added.items() if v};receipt={'movies':expected_count,'mapping':mapping,'inserted_keys':keys,'inserted_rows_hash':rows_hash(added),'prior_catalog_rows_unchanged':True}
        c.execute(update(md.tables['ingest_batches']).where(md.tables['ingest_batches'].c.batch_id==batch).values(status='succeeded',finished_at=stamp,counts_json=receipt))
        with intent.open('x',encoding='utf-8') as f:os.chmod(intent,0o600);json.dump({'source_sha256':sha,'status':'COMMIT_UNKNOWN_DO_NOT_RETRY'},f);f.flush();os.fsync(f.fileno())
        tx.commit()
        with audit.connect() as ac:verify_m6_connection(ac,cfg,audit=True);verify(ac,receipt)
        intent.rename(intent.with_suffix('.audited.json'))
        return dict(inserted=expected_count,replayed=False,mapping=mapping,audit_passed=True,existing_catalog_preserved=True)
    finally:
        if c.in_transaction():c.rollback()
        if locked:c.execute(text('SELECT RELEASE_LOCK(:n)'),{'n':lock});c.commit()
        c.close();audit.dispose()

if __name__=='__main__':
    try:
        params=json.loads(Path(os.environ['MI2_M9_APPEND_SPEC']).read_text());print(json.dumps(run(**params),ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'import_passed':False,'error_type':type(exc).__name__,'error_code':str(exc) if isinstance(exc,ValueError) else 'IMPORT_STOPPED_NO_AUTOMATIC_RETRY'}));raise SystemExit(1)
