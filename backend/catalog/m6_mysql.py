"""Authorized M6 MySQL adapter; the historical offline entry stays denied."""
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import select, text, insert, update
from app.m6_database import verify_m6_connection, validate_m6_url
from app.m6_activity import activity_tables
from .m6_contract import authority, digest, validate_rows
from .m6_offline import read_snapshot, preflight, rows_hash, MAPPING_VERSION


class AuditRequired(RuntimeError):
    pass


def target_rows(connection, bundle):
    return {name:[dict(row) for row in connection.execute(select(table)).mappings()]
            for name,table in bundle['metadata'].tables.items()}


def checked_rows(connection, bundle):
    rows, problems = validate_rows(bundle, target_rows(connection, bundle))
    if problems:
        raise AuditRequired('TARGET_CONSTRAINT_AUDIT_REQUIRED')
    return rows


class MySQLImporter:
    def __init__(self, engine, audit_engine, config, state_dir):
        if config.get('M6_ISOLATED') is not True or engine is audit_engine:
            raise RuntimeError('Explicit M6 runtime and independent audit pool required')
        self.engine, self.audit_engine, self.config = engine, audit_engine, config
        validate_m6_url(engine.url,config)
        validate_m6_url(audit_engine.url,config,audit=True)
        self.bundle = authority()
        self.receipts = activity_tables(config['M6_ACTIVITY_SCHEMA'], config['M6_SCHEMA'])['receipts']
        self.state_dir = Path(state_dir)
        if not self.state_dir.is_absolute() or not self.state_dir.is_dir() or self.state_dir.is_symlink():
            raise ValueError('Existing owned absolute import state directory required')

    def _state(self, source_hash):
        return self.state_dir/('commit-intent-'+digest({'source':source_hash,'target':self.config['M6_SCHEMA']})+'.json')

    def _write_intent(self, source_hash):
        path = self._state(source_hash)
        with path.open('x', encoding='utf-8') as handle:
            os.chmod(path, 0o600)
            json.dump({'source_hash':source_hash,'contract_hash':self.bundle['contract_hash'],'status':'COMMIT_UNKNOWN_DO_NOT_RETRY'},handle)
            handle.flush();os.fsync(handle.fileno())
        if hasattr(os, 'O_DIRECTORY'):
            descriptor=os.open(self.state_dir,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)

    def _validate_receipt(self, connection, receipt, source_hash, seal):
        if (receipt['source_hash'] != source_hash or receipt['source_seal'] != seal
                or receipt['contract_hash'] != self.bundle['contract_hash']
                or receipt['mapping_version'] != MAPPING_VERSION):
            raise AuditRequired('SOURCE_VERSION_REVIEW_REQUIRED')
        if rows_hash(checked_rows(connection,self.bundle)) != receipt['target_rows_hash']:
            raise AuditRequired('TARGET_STATE_AUDIT_REQUIRED')
        return dict(receipt['receipt'], replayed=True)

    def audit(self, source_hash, seal, *, lock_timeout=10):
        # Separate pool/connection, explicitly READ ONLY and consistent snapshot.
        if type(lock_timeout) is not int or not 0<=lock_timeout<=10:
            raise ValueError('Invalid audit wait')
        with self.audit_engine.connect() as connection:
            verify_m6_connection(connection,self.config,audit=True)
            connection.commit()
            name='m6import:'+self.config['M6_SCHEMA']
            acquired=connection.execute(text('SELECT GET_LOCK(:name,:wait)'),{'name':name,'wait':lock_timeout}).scalar()
            connection.commit()
            if acquired!=1:
                # Empty/invisible rows do not establish rollback while the
                # original session may still commit. Preserve its intent.
                raise AuditRequired('COMMIT_OUTCOME_STILL_IN_PROGRESS')
            try:
                connection.exec_driver_sql('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
                existing = connection.execute(select(self.receipts)).mappings().all()
                if len(existing)==1:
                    result=self._validate_receipt(connection,existing[0],source_hash,seal)
                elif not existing and not any(checked_rows(connection,self.bundle).values()):
                    result={'status':'AUDITED_NOT_COMMITTED','live':True}
                else:
                    raise AuditRequired('UNKNOWN_COMMIT_INCONSISTENT_DO_NOT_RETRY')
                connection.rollback()
            finally:
                try:
                    if connection.in_transaction():connection.rollback()
                    connection.execute(text('SELECT RELEASE_LOCK(:name)'),{'name':name})
                    connection.commit()
                except Exception:
                    connection.invalidate()
                    raise AuditRequired('AUDIT_CHANNEL_RECOVERY_REQUIRED') from None
        path=self._state(source_hash)
        if path.exists():
            # Preserve the intent as an audit artifact rather than erase it.
            archive=path.with_suffix('.audited-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')
            path.replace(archive)
        return result

    def import_file(self, path, expected_sha256, *, source_kind, approved_real_sources=(), fault=None):
        if source_kind not in ('synthetic','approved_static_copy'):
            raise ValueError('Explicit source class required')
        if source_kind=='synthetic' and self.config.get('M6_SYNTHETIC_TEST') is not True:
            raise PermissionError('Synthetic import requires explicit test harness mode')
        raw=Path(path)
        if source_kind=='approved_static_copy' and (str(raw),expected_sha256) not in approved_real_sources:
            raise PermissionError('Specific real static copy path and hash not authorized')
        if self._state(expected_sha256).exists():
            raise AuditRequired('COMMIT_UNKNOWN_DO_NOT_RETRY')
        snapshot=read_snapshot(raw,expected_sha256,self.bundle)
        rows,problems=preflight(snapshot,self.bundle)
        if problems:
            raise ValueError('SOURCE_PREFLIGHT_REJECTED:'+digest(problems))
        key=digest({'source':expected_sha256,'seal':snapshot.seal,'contract':self.bundle['contract_hash'],'mapping':MAPPING_VERSION})
        connection=self.engine.connect()
        lock_name='m6import:'+self.config['M6_SCHEMA']
        locked=False
        try:
            verify_m6_connection(connection,self.config)
            if connection.execute(text('SELECT GET_LOCK(:name,10)'),{'name':lock_name}).scalar()!=1:
                raise AuditRequired('CONCURRENT_IMPORT_BUSY')
            locked=True
            connection.commit()
            if self._state(expected_sha256).exists():
                raise AuditRequired('COMMIT_UNKNOWN_DO_NOT_RETRY')
            transaction=connection.begin()
            existing=connection.execute(select(self.receipts).with_for_update()).mappings().all()
            if existing:
                if len(existing)!=1:raise AuditRequired('MULTIPLE_SOURCE_VERSIONS_REVIEW_REQUIRED')
                result=self._validate_receipt(connection,existing[0],expected_sha256,snapshot.seal)
                transaction.rollback()
                return result
            if any(checked_rows(connection,self.bundle).values()):
                raise AuditRequired('TARGET_NOT_EMPTY_REVIEW_REQUIRED')
            receipt=self._transfer(connection,rows,snapshot)
            receipt.update(live=True,source_kind=source_kind,source_dataset_hash=expected_sha256,
                           content_hash=snapshot.seal,contract_hash=self.bundle['contract_hash'],
                           mapping_version=MAPPING_VERSION,replayed=False,status='MYSQL_COMMITTED')
            typed=checked_rows(connection,self.bundle)
            target_hash=rows_hash(typed)
            receipt['target_rows_hash']=target_hash
            receipt['counts']={name:len(values) for name,values in typed.items()}
            connection.execute(insert(self.receipts).values(receipt_key=key,source_hash=expected_sha256,
                source_seal=snapshot.seal,contract_hash=self.bundle['contract_hash'],mapping_version=MAPPING_VERSION,
                target_rows_hash=target_hash,receipt=receipt))
            if fault=='BEFORE_COMMIT':
                raise RuntimeError('INJECTED_PRECOMMIT_FAILURE')
            self._write_intent(expected_sha256)
            try:
                transaction.commit()
            except Exception:
                raise AuditRequired('COMMIT_UNKNOWN_DO_NOT_RETRY') from None
            if fault=='AFTER_COMMIT_UNKNOWN':
                raise AuditRequired('COMMIT_UNKNOWN_DO_NOT_RETRY')
            # The independent auditor must be able to acquire the same lock,
            # which establishes that no prior writer is still in progress.
            released=connection.execute(text('SELECT RELEASE_LOCK(:name)'),{'name':lock_name}).scalar()
            connection.commit()
            if released!=1:raise AuditRequired('COMMIT_COORDINATION_AUDIT_REQUIRED')
            locked=False
            # Even a positive acknowledgement is independently audited before
            # the durable intent is cleared and a committed receipt is returned.
            audited=self.audit(expected_sha256,snapshot.seal)
            return dict(audited,replayed=False)
        finally:
            if connection.in_transaction():
                try:connection.rollback()
                except Exception:connection.invalidate()
            if locked:
                try:
                    connection.execute(text('SELECT RELEASE_LOCK(:name)'),{'name':lock_name})
                    connection.commit()
                except Exception:
                    connection.invalidate()
            connection.close()

    def _transfer(self,connection,rows,snapshot):
        md=self.bundle['metadata'];stamp=datetime.now(timezone.utc).replace(tzinfo=None)
        def put(name,values):
            result=connection.execute(insert(md.tables[name]).values(**values))
            return result.inserted_primary_key[0] if len(md.tables[name].primary_key.columns)==1 else None
        batch=put('ingest_batches',dict(source='tmdb',mode='write',status='running',started_at=stamp,
            source_snapshot_ref='m6-sha256:'+snapshot.source_hash,counts_json={}))
        mappings={'movies':[],'genres':[],'movie_source_records':[]};movies={};genres={};sources={}
        for old in sorted(rows['movies'],key=lambda r:r['movie_id']):
            new=put('movies',{k:v for k,v in old.items() if k!='movie_id'});movies[old['movie_id']]=new
            provenance=next(r for r in rows['movie_source_records'] if r['movie_id']==old['movie_id'])
            mappings['movies'].append(dict(source_movie_id=old['movie_id'],destination_movie_id=new,
                source=provenance['source'],source_record_id=provenance['source_record_id'],
                external_ids={row['source']:row['external_id'] for row in rows['movie_external_ids'] if row['movie_id']==old['movie_id']}))
        for old in sorted(rows['genres'],key=lambda r:r['genre_id']):
            new=put('genres',{k:v for k,v in old.items() if k!='genre_id'});genres[old['genre_id']]=new
            mappings['genres'].append(dict(source_genre_id=old['genre_id'],tmdb_genre_id=old['tmdb_genre_id'],destination_genre_id=new))
        for old in sorted(rows['movie_source_records'],key=lambda r:r['id']):
            values={k:v for k,v in old.items() if k not in ('id','movie_id','ingest_batch_id')}
            values.update(movie_id=movies[old['movie_id']],ingest_batch_id=batch)
            new=put('movie_source_records',values);sources[old['id']]=new
            mappings['movie_source_records'].append(dict(source_record_pk=old['id'],destination_source_record_pk=new,
                destination_movie_id=values['movie_id'],destination_batch_id=batch))
            put('ingest_items',dict(batch_id=batch,source_record_id=old['source_record_id'],status='inserted',
                movie_id=values['movie_id'],payload_hash=old['payload_hash'],attempt_count=1,updated_at=stamp))
        for name in ('movie_external_ids','movie_aliases','movie_genres','movie_ratings_summary'):
            for old in rows[name]:
                values={k:v for k,v in old.items() if k!='id' and md.tables[name].c[k].computed is None}
                values['movie_id']=movies[old['movie_id']]
                if name=='movie_genres':values['genre_id']=genres[old['genre_id']]
                if name=='movie_ratings_summary':values['selected_source_record_pk']=sources[old['selected_source_record_pk']]
                put(name,values)
        connection.execute(update(md.tables['ingest_batches']).where(md.tables['ingest_batches'].c.batch_id==batch)
            .values(status='succeeded',finished_at=stamp,counts_json={'movies':len(movies),'source_records':len(sources)}))
        return {'destination_batch_id':batch,'mappings':mappings}
