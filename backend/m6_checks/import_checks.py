"""Controlled synthetic SQLite -> actual MySQL adapter verification."""
import copy
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import os
from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import DBAPIError
from catalog.m6_contract import authority, digest
from catalog.m6_offline_check import fixture, synthetic_snapshot
from catalog.local_catalog_store import LocalCatalogStore
from catalog.m6_offline import read_snapshot, rows_hash
from catalog.m6_mysql import MySQLImporter, AuditRequired, checked_rows


def synthetic_file(directory):
    bundle=authority();rows=fixture(bundle)
    # A second film shares its genre/batch, has a distinct internal ID and an
    # IMDb identity, so a film-ID-as-batch-ID bug cannot pass unnoticed.
    for table in ['movies','movie_external_ids','movie_source_records','movie_aliases','movie_genres','movie_ratings_summary']:
        row=copy.deepcopy(rows[table][0]);row['movie_id']=1002
        if table=='movies':row.update(title='SYNTHETIC FILM TWO',original_title='Alias Two')
        elif table=='movie_external_ids':row.update(id=5002,external_id='900002')
        elif table=='movie_source_records':
            raw=row['metadata_json'];raw.update(id=900002,title='SYNTHETIC FILM TWO',original_title='Alias Two',imdb_id='tt900002')
            row.update(id=6002,source_record_id='900002',payload_hash=digest(raw))
        elif table=='movie_aliases':row.update(id=7002,title='Alias Two',title_normalized='alias two')
        elif table=='movie_ratings_summary':row['selected_source_record_pk']=6002
        rows[table].append(row)
    imdb=copy.deepcopy(rows['movie_external_ids'][-1]);imdb.update(id=5003,source='imdb',external_id='tt900002')
    rows['movie_external_ids'].append(imdb)
    snapshot=synthetic_snapshot(bundle,rows)
    path=directory/'synthetic-m5.sqlite3'
    LocalCatalogStore(path,initialize=True)
    def value(item):
        if isinstance(item,(dict,list)):return json.dumps(item,ensure_ascii=False)
        if isinstance(item,(date,datetime)):return item.isoformat()
        if isinstance(item,Decimal):return str(item)
        return item
    with sqlite3.connect(path) as db:
        for name,items in snapshot.rows.items():
            for row in items:
                columns=list(row)
                db.execute('INSERT INTO "'+name+'" ('+','.join('"'+n+'"' for n in columns)+') VALUES ('+','.join('?' for _ in columns)+')',[value(row[n]) for n in columns])
        for row in snapshot.ledger:
            columns=list(row)
            db.execute('INSERT INTO m5_records ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',[value(row[n]) for n in columns])
    return path,hashlib.sha256(path.read_bytes()).hexdigest()


def run_import_checks(app,check):
    data_root=Path('/var/lib/movieinsight-m6/appdata')
    registry=data_root/'synthetic-source.json'
    first=not registry.exists()
    if first:
        directory=Path(tempfile.mkdtemp(prefix='m6-synthetic-',dir=data_root))
        source,sha=synthetic_file(directory)
        registry.write_text(json.dumps({'path':str(source),'sha256':sha,'state':str(directory)}))
        registry.chmod(0o600)
    else:
        saved=json.loads(registry.read_text());source=Path(saved['path']);sha=saved['sha256'];directory=Path(saved['state'])
    config=dict(app.config,M6_SYNTHETIC_TEST=True)
    audit_engine=create_engine(os.environ['MI2_AUDIT_DATABASE_URL'],pool_pre_ping=True,hide_parameters=True)
    importer=MySQLImporter(app.extensions['auth_engine'],audit_engine,config,directory)
    with audit_engine.connect() as connection:
        from sqlalchemy import text
        check('independent_readonly_database_identity',connection.execute(text('SELECT CURRENT_USER()')).scalar_one().split('@')[0]==config['M6_AUDIT_USER'] and config['M6_AUDIT_USER']!=config['M6_APP_USER'])
        try:
            connection.execute(update(importer.receipts).where(importer.receipts.c.receipt_key=='NONEXISTENT_SYNTHETIC').values(source_hash='0'*64))
        except DBAPIError as error:check('audit_account_write_denied',error.orig.args[0]==1142)
        else:check('audit_account_write_denied',False)
    source_snapshot=read_snapshot(source,sha,importer.bundle)
    check('synthetic_sqlite_source_seal',source_snapshot.source_hash==sha)
    if first:
        try:importer.import_file(source,sha,source_kind='synthetic',fault='BEFORE_COMMIT')
        except RuntimeError as error:check('adapter_precommit_failure_injected',str(error)=='INJECTED_PRECOMMIT_FAILURE')
        else:check('adapter_precommit_failure_injected',False)
        with audit_engine.connect() as connection:
            check('adapter_precommit_full_rollback',not any(checked_rows(connection,importer.bundle).values()))
        try:importer.import_file(source,sha,source_kind='synthetic',fault='AFTER_COMMIT_UNKNOWN')
        except AuditRequired as error:check('adapter_commit_unknown',str(error)=='COMMIT_UNKNOWN_DO_NOT_RETRY')
        else:check('adapter_commit_unknown',False)
        try:importer.import_file(source,sha,source_kind='synthetic')
        except AuditRequired as error:check('adapter_blind_retry_denied',str(error)=='COMMIT_UNKNOWN_DO_NOT_RETRY')
        else:check('adapter_blind_retry_denied',False)
        receipt=importer.audit(sha,source_snapshot.seal)
        check('adapter_independent_readonly_audit',receipt['live'] and receipt['status']=='MYSQL_COMMITTED')
    receipt=importer.import_file(source,sha,source_kind='synthetic')
    check('adapter_idempotent_replay',receipt['replayed'] and receipt['counts']['movies']==2)
    check('adapter_tmdb_imdb_mapping',any(m['external_ids'].get('imdb')=='tt900002' for m in receipt['mappings']['movies']))
    check('adapter_internal_source_ids_independent',all(m['source_movie_id']!=m['destination_movie_id'] for m in receipt['mappings']['movies']))
    with audit_engine.connect() as connection:
        sources=connection.execute(select(importer.bundle['metadata'].tables['movie_source_records'])).mappings().all()
        check('adapter_batch_mapping_not_movie_id',all(row['ingest_batch_id']==receipt['destination_batch_id'] for row in sources) and any(row['movie_id']!=row['ingest_batch_id'] for row in sources))
    movies=importer.bundle['metadata'].tables['movies']
    mapping=receipt['mappings']['movies'][0]
    mid=mapping['destination_movie_id']
    with app.extensions['auth_engine'].begin() as connection:
        original=connection.execute(select(movies.c.title).where(movies.c.movie_id==mid)).scalar_one()
        connection.execute(update(movies).where(movies.c.movie_id==mid).values(title='SYNTHETIC TARGET DRIFT'))
    try:
        try:importer.import_file(source,sha,source_kind='synthetic')
        except AuditRequired as error:check('adapter_target_drift_blocks_replay',str(error)=='TARGET_STATE_AUDIT_REQUIRED')
        else:check('adapter_target_drift_blocks_replay',False)
    finally:
        with app.extensions['auth_engine'].begin() as connection:
            connection.execute(update(movies).where(movies.c.movie_id==mid).values(title=original))
    check('adapter_controlled_repair_reaudited',importer.import_file(source,sha,source_kind='synthetic')['replayed'])
    try:importer.import_file(source,sha,source_kind='approved_static_copy')
    except PermissionError:check('unapproved_real_source_denied',True)
    else:check('unapproved_real_source_denied',False)
    check('source_bytes_unchanged',hashlib.sha256(source.read_bytes()).hexdigest()==sha)
    audit_engine.dispose()
    return receipt


def approved_copy_checks(app,check):
    from app.m6_activity import activity_tables
    table=activity_tables(app.config['M6_ACTIVITY_SCHEMA'],app.config['M6_SCHEMA'])['receipts']
    bundle=authority()
    with app.extensions['auth_engine'].connect() as connection:
        record=connection.execute(select(table)).mappings().one()
        receipt=record['receipt']
        check('approved_copy_receipt_live',receipt['live'] and receipt['source_kind']=='approved_static_copy')
        check('approved_copy_contract_pinned',receipt['contract_hash']==bundle['contract_hash']==record['contract_hash'])
        rows=checked_rows(connection,bundle)
        check('approved_copy_full_target_hash',rows_hash(rows)==record['target_rows_hash'])
        check('approved_copy_all_table_counts',{n:len(values) for n,values in rows.items()}==receipt['counts'])
        check('approved_copy_source_hash_binding',record['source_hash']==receipt['source_dataset_hash'])
        by_movie={row['movie_id']:row for row in rows['movies']}
        external={(row['source'],row['external_id']):row['movie_id'] for row in rows['movie_external_ids']}
        check('approved_copy_external_id_traceability',all(mapping['destination_movie_id'] in by_movie and all(external[(source,eid)]==mapping['destination_movie_id'] for source,eid in mapping['external_ids'].items()) for mapping in receipt['mappings']['movies']))
        sources={row['id']:row for row in rows['movie_source_records']}
        check('approved_copy_source_and_batch_traceability',all(sources[mapping['destination_source_record_pk']]['movie_id']==mapping['destination_movie_id'] and sources[mapping['destination_source_record_pk']]['ingest_batch_id']==mapping['destination_batch_id']==receipt['destination_batch_id'] for mapping in receipt['mappings']['movie_source_records']))
    # Actual MySQL lock contention: a still-live writer prevents any audit
    # outcome and must not clear a pending commit-intent file.
    audit=create_engine(os.environ['MI2_AUDIT_DATABASE_URL'],pool_pre_ping=True,hide_parameters=True)
    with tempfile.TemporaryDirectory(prefix='m6-audit-lock-') as directory:
        importer=MySQLImporter(app.extensions['auth_engine'],audit,app.config,Path(directory))
        importer._write_intent(record['source_hash'])
        with app.extensions['auth_engine'].connect() as writer:
            from sqlalchemy import text
            name='m6import:'+app.config['M6_SCHEMA']
            check('audit_lock_probe_acquired',writer.execute(text('SELECT GET_LOCK(:name,1)'),{'name':name}).scalar()==1)
            writer.commit()
            try:
                try:importer.audit(record['source_hash'],record['source_seal'],lock_timeout=0)
                except AuditRequired as error:check('audit_live_writer_blocks_outcome',str(error)=='COMMIT_OUTCOME_STILL_IN_PROGRESS' and importer._state(record['source_hash']).exists())
                else:check('audit_live_writer_blocks_outcome',False)
            finally:
                writer.execute(text('SELECT RELEASE_LOCK(:name)'),{'name':name});writer.commit()
        result=importer.audit(record['source_hash'],record['source_seal'])
        check('readonly_audit_after_writer_finished',result['status']=='MYSQL_COMMITTED' and not importer._state(record['source_hash']).exists())
    audit.dispose()
    return receipt
