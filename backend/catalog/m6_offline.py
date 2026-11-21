"""M6 independent importer/DDL protocol, OFFLINE ONLY.

Reads a pinned M5 SQLite file with mode=ro. Destination and DDL execution are
in-memory simulations only. execute_live is permanently denied in this build.
"""
import ast
import copy
import hashlib
import pathlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from .m6_contract import ROOT, verify_bundle, digest, validate_rows, full_row, _json_object

MAPPING_VERSION = 'M6-D-M6-02-v1'


class LiveIntegrationBlocked(RuntimeError):
    pass


def execute_live(*args, **kwargs):
    raise LiveIntegrationBlocked('RUNTIME_AND_CREDENTIAL_ISOLATION_NOT_RELEASED')


def projection_declaration():
    tree=ast.parse((ROOT/'local_catalog_store.py').read_text(encoding='utf-8'))
    values={n.targets[0].id:ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
            and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('TABLES','MARKER')}
    return values


def check_source_schema(bundle, schema):
    from sqlalchemy import Integer, Boolean
    expected=projection_declaration()['TABLES'];issues=[]
    if set(schema)!=set(expected):
        return [{'code':'SOURCE_TABLE_SET'}]
    for name in expected:
        table=bundle['metadata'].tables[name];columns=schema[name]['columns']
        if set(columns)!=set(table.c.keys()):
            issues.append({'code':'SOURCE_COLUMN_SET','table':name});continue
        for col in table.c:
            typ='INTEGER' if isinstance(col.type,(Integer,Boolean)) else 'TEXT'
            pk=1 if len(table.primary_key.columns)==1 and col.primary_key else 0
            if columns[col.name] != {'type':typ,'notnull':0,'pk':pk}:
                issues.append({'code':'SOURCE_COLUMN_DDL','table':name,'column':col.name})
    indexes={'movie_external_ids':('source','external_id'),'genres':('tmdb_genre_id',),'movie_genres':('movie_id','genre_id')}
    for table, cols in indexes.items():
        if list(cols) not in schema[table]['unique_indexes']:
            issues.append({'code':'SOURCE_UNIQUE_INDEX','table':table})
    return issues


def content_hash(rows, schema, ledger):
    return digest({'rows':{n:sorted(digest(r) for r in rs) for n,rs in rows.items()},'schema':schema,'ledger':sorted(digest(r) for r in ledger)})


def rows_hash(rows):
    return digest({n:sorted(digest(r) for r in rs) for n,rs in rows.items()})


@dataclass
class Snapshot:
    rows: dict
    schema: dict
    ledger: list
    source_hash: str
    seal: str
    origin: str


def read_snapshot(path, expected_sha256, bundle):
    bundle=verify_bundle(bundle)
    path=pathlib.Path(path)
    if not path.is_absolute() or path.drive.upper()=='C:' or not re.fullmatch('[0-9a-f]{64}',expected_sha256):
        raise ValueError('EXPLICIT_NON_C_SOURCE_AND_EXPECTED_HASH_REQUIRED')
    if any(p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&1024 for p in (path,*path.parents)) or path.stat().st_nlink!=1:
        raise ValueError('LINKED_SOURCE_REJECTED')
    def stationary():
        if any(p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&1024 for p in (path,*path.parents)) or path.stat().st_nlink!=1:
            raise ValueError('LINKED_SOURCE_REJECTED')
        if any(pathlib.Path(str(path)+suffix).exists() for suffix in ('-wal','-shm','-journal')):
            raise ValueError('SOURCE_SIDECAR_REJECTED')
        return path.stat()
    initial=stationary()
    frozen=path.read_bytes()
    if len(frozen)<100 or frozen[:16]!=b'SQLite format 3\x00' or frozen[18:20]!=b'\x01\x01':
        raise ValueError('STANDALONE_ROLLBACK_FORMAT_REQUIRED')
    before=hashlib.sha256(frozen).hexdigest()
    if before!=expected_sha256:
        raise ValueError('SOURCE_HASH_MISMATCH')
    stationary()
    # Parse only the approved immutable bytes, never SQLite's live WAL view.
    db=sqlite3.connect(':memory:');db.deserialize(frozen);db.row_factory=sqlite3.Row
    rows,schema={},{}
    def quote(n):return '"'+n.replace('"','""')+'"'
    try:
        db.execute('PRAGMA query_only=ON');decl=projection_declaration()
        if [r[0] for r in db.execute('SELECT marker FROM m5_metadata')]!=[decl['MARKER']]:
            raise ValueError('SOURCE_OWNER_MISMATCH')
        names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if names-set(decl['TABLES'])-{'m5_metadata','m5_records','m5_batches','sqlite_sequence'}:
            raise ValueError('UNEXPECTED_SOURCE_TABLES')
        for name in decl['TABLES']:
            columns={r[1]:{'type':r[2].upper(),'notnull':r[3],'pk':r[5]} for r in db.execute('PRAGMA table_info('+quote(name)+')')}
            unique=[]
            for index in db.execute('PRAGMA index_list('+quote(name)+')').fetchall():
                if index[2] and not index[4]:
                    unique.append([r[2] for r in db.execute('PRAGMA index_info('+quote(index[1])+')')])
            schema[name]={'columns':columns,'unique_indexes':sorted(unique)}
            rows[name]=[dict(r) for r in db.execute('SELECT * FROM '+quote(name))]
        ledger=[dict(r) for r in db.execute('SELECT * FROM m5_records')]
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('SOURCE_INTEGRITY')
    finally:
        db.close()
    final=stationary()
    if (initial.st_dev,initial.st_ino,initial.st_size,initial.st_mtime_ns,initial.st_ctime_ns)!=(final.st_dev,final.st_ino,final.st_size,final.st_mtime_ns,final.st_ctime_ns) or hashlib.sha256(path.read_bytes()).hexdigest()!=before:
        raise ValueError('SOURCE_CHANGED_DURING_READ')
    return Snapshot(rows,schema,ledger,before,content_hash(rows,schema,ledger),'LOCAL_M5_READONLY')


def preflight(snapshot,bundle):
    bundle=verify_bundle(bundle)
    issues=check_source_schema(bundle,snapshot.schema)
    if snapshot.seal!=content_hash(snapshot.rows,snapshot.schema,snapshot.ledger):
        issues.append({'code':'SOURCE_MEMORY_MUTATION'})
    if set(snapshot.rows)!=set(projection_declaration()['TABLES']):
        issues.append({'code':'SOURCE_ROW_TABLE_SET'})
    typed,values=validate_rows(bundle,snapshot.rows,source_projection=True);issues.extend(values)
    if issues:
        return typed,issues
    records={(r['source'],r['source_record_id']):r for r in typed['movie_source_records']}
    ledgers={(r.get('source'),r.get('source_id')):r for r in snapshot.ledger}
    if set(records)!=set(ledgers) or len(ledgers)!=len(snapshot.ledger):
        issues.append({'code':'SOURCE_LEDGER_KEYS'})
    external={(r['source'],r['external_id']):r['movie_id'] for r in typed['movie_external_ids']}
    movies={r['movie_id']:r for r in typed['movies']}
    # Stored JSON is not rebranded as original captured bytes. Reuse the sealed
    # M2 normalizer on extracted facts solely to check normalized value equality.
    from .data_pipeline import normalize
    from .m6_contract import scalar
    for key,row in records.items():
        old=ledgers.get(key)
        try:
            raw=_json_object(old['payload']);evidence=_json_object(old['evidence'])
            good=(row['source']=='tmdb' and row['license_status']=='allowed' and row['ingest_batch_id'] is None
                  and row['movie_id'] is not None and type(raw.get('id')) is int and raw['id']>0
                  and old['movie_id']==row['movie_id'] and old['raw_hash']==row['payload_hash']==evidence['raw_sha256']
                  and digest(raw)==digest(row['metadata_json']) and str(raw['id'])==row['source_record_id']
                  and ('tmdb',row['source_record_id']) in external and external[('tmdb',row['source_record_id'])]==row['movie_id']
                  and evidence['license_ref']=='https://developer.themoviedb.org/docs/faq'
                  and evidence['source_url']==f'https://api.themoviedb.org/3/movie/{row["source_record_id"]}?language=zh-CN')
            if raw.get('imdb_id'):
                good=good and external.get(('imdb',raw['imdb_id']))==row['movie_id']
            expected_external={('tmdb',str(raw['id']))}
            if raw.get('imdb_id'):expected_external.add(('imdb',raw['imdb_id']))
            good=good and {(r['source'],r['external_id']) for r in typed['movie_external_ids'] if r['movie_id']==row['movie_id']}==expected_external
            source_movie=movies[row['movie_id']]
            record=dict(source_record_id=str(raw['id']),external_ids={'tmdb':str(raw['id'])},title=raw.get('title'),original_title=raw.get('original_title') or None,
                        release_date=raw.get('release_date') or None,runtime_minutes=raw.get('runtime'),language=raw.get('original_language') or None,
                        overview=raw.get('overview') or None,genres=[g['name'] for g in raw.get('genres',[])],
                        aliases=[raw['original_title']] if raw.get('original_title') and raw['original_title']!=raw.get('title') else [])
            expected=normalize(record,{'source':'tmdb','license_status':'allowed'})
            for field in ('title','original_title','release_date','release_year','runtime_minutes','original_language','overview'):
                good=good and source_movie[field]==scalar(bundle['metadata'].tables['movies'].c[field],expected[field])
            good=good and type(raw.get('adult')) is bool and source_movie['is_adult']==raw['adult']
            good=good and {(r['title'],r['title_normalized'],r['language'],r['source']) for r in typed['movie_aliases'] if r['movie_id']==row['movie_id']}=={(a,a.casefold(),expected['original_language'],'tmdb') for a in expected['aliases']}
            good=good and row['fetched_at']==scalar(bundle['metadata'].tables['movie_source_records'].c.fetched_at,evidence['observed_at'],source=True)
            genre_map={g['genre_id']:g for g in typed['genres']}
            linked=[genre_map[g['genre_id']] for g in typed['movie_genres'] if g['movie_id']==row['movie_id']]
            good=good and {(g['tmdb_genre_id'],g['name_zh']) for g in linked}=={(str(g['id']),g['name']) for g in raw.get('genres',[])}
            ratings=[r for r in typed['movie_ratings_summary'] if r['movie_id']==row['movie_id']]
            if raw.get('vote_average') is None:
                good=good and not ratings
            else:
                score=Decimal(str(raw['vote_average'])).quantize(Decimal('.01'),rounding=ROUND_HALF_UP) if raw.get('vote_count') else None
                good=good and len(ratings)==1 and ratings[0]['source']=='tmdb' and ratings[0]['score_value']==score and ratings[0]['score_scale']==10 and ratings[0]['vote_count']==raw.get('vote_count') and ratings[0]['selected_source_record_pk']==row['id'] and ratings[0]['observed_at']==row['fetched_at']
        except (KeyError,TypeError,ValueError,ArithmeticError):good=False
        if not good:issues.append({'code':'SOURCE_PROVENANCE_BINDING','source_record_id':key[1]})
    for movie in typed['movies']:
        if sum(r['movie_id']==movie['movie_id'] for r in typed['movie_source_records'])!=1:
            issues.append({'code':'MOVIE_SOURCE_NOT_UNAMBIGUOUS','source_movie_id':movie['movie_id']})
    return typed,issues


class MemorySandbox:
    """Test-only target. IDs deliberately differ by table to expose BID misuse."""
    def __init__(self,bundle,namespace='M6_OFFLINE'):
        if not re.fullmatch('[A-Za-z0-9_-]{1,64}',namespace):raise ValueError('NOT_A_CONNECTION_STRING')
        self.namespace=namespace;self.bundle=bundle;self.rows={n:[] for n in bundle['metadata'].tables}
        self.next_ids={n:(i+1)*100000 for i,n in enumerate(sorted(self.rows))}
        self.receipts={};self.bound_source=None;self.uncertain=False

    def insert(self,table,values):
        obj=self.bundle['metadata'].tables[table];row=full_row(self.bundle,table,copy.deepcopy(values))
        if len(obj.primary_key.columns)==1:
            col=next(iter(obj.primary_key.columns))
            if row[col.name] is None:
                self.next_ids[table]+=1;row[col.name]=self.next_ids[table]
        self.rows[table].append(row)
        return row

    def audit_commit(self):
        if not self.uncertain:raise ValueError('NO_UNKNOWN_COMMIT')
        verify_bundle(self.bundle)
        if (len(self.receipts)!=1 or validate_rows(self.bundle,self.rows)[1]
                or next(iter(self.receipts.values()))['target_rows_hash']!=rows_hash(self.rows)):
            return {'status':'SIMULATED_AUDIT_INCONSISTENT_DO_NOT_RETRY'}
        self.uncertain=False
        return {'status':'SIMULATED_AUDIT_COMMITTED','receipts':copy.deepcopy(self.receipts)}


def simulate_import(snapshot,target,bundle,*,fault=None,now=None):
    if type(target) is not MemorySandbox:raise LiveIntegrationBlocked('MEMORY_TARGET_ONLY')
    verify_bundle(target.bundle)
    if target.bundle['contract_hash']!=bundle['contract_hash']:raise ValueError('TARGET_CONTRACT_MISMATCH')
    if fault not in (None,'BEFORE_COMMIT','AFTER_COMMIT_UNKNOWN'):raise ValueError('UNKNOWN_FAULT')
    if target.uncertain:return {'status':'COMMIT_UNKNOWN_DO_NOT_RETRY','live':False}
    if target.bound_source is None and (any(target.rows.values()) or target.receipts):
        return {'status':'TARGET_NOT_EMPTY_REVIEW_REQUIRED','live':False}
    rows,issues=preflight(snapshot,bundle)
    if issues:return {'status':'BLOCKED_PREFLIGHT','issues':issues,'live':False}
    identity=(snapshot.source_hash,snapshot.seal,bundle['contract_hash'])
    if target.bound_source and target.bound_source!=identity:
        return {'status':'SOURCE_VERSION_REVIEW_REQUIRED','live':False}
    key=digest({'source':identity,'target':target.namespace,'mapping_version':MAPPING_VERSION})
    if key in target.receipts:
        if validate_rows(bundle,target.rows)[1] or rows_hash(target.rows)!=target.receipts[key]['target_rows_hash']:
            return {'status':'TARGET_STATE_AUDIT_REQUIRED','live':False}
        return dict(copy.deepcopy(target.receipts[key]),replayed=True)
    previous=copy.deepcopy((target.rows,target.receipts,target.bound_source))
    timestamp=(now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
    mappings={'movies':[],'genres':[],'movie_source_records':[]};movie_map={};genre_map={};source_map={}
    try:
        batch=target.insert('ingest_batches',dict(source='tmdb',mode='dry-run',status='running',started_at=timestamp,
                            source_snapshot_ref='m6-sha256:'+snapshot.source_hash,counts_json={}))
        destination_batch_id=batch['batch_id']
        for old in sorted(rows['movies'],key=lambda r:r['movie_id']):
            values={k:v for k,v in old.items() if k!='movie_id'};new=target.insert('movies',values);movie_map[old['movie_id']]=new['movie_id']
            provenance=next(r for r in rows['movie_source_records'] if r['movie_id']==old['movie_id'])
            mappings['movies'].append(dict(source_dataset_hash=snapshot.source_hash,source_movie_id=old['movie_id'],source=provenance['source'],
                source_record_id=provenance['source_record_id'],external_id=provenance['source_record_id'],destination_movie_id=new['movie_id'],
                mapping_version=MAPPING_VERSION,target_namespace=target.namespace,simulation=True))
        for old in sorted(rows['genres'],key=lambda r:r['genre_id']):
            new=target.insert('genres',{k:v for k,v in old.items() if k!='genre_id'});genre_map[old['genre_id']]=new['genre_id']
            mappings['genres'].append(dict(source_genre_id=old['genre_id'],tmdb_genre_id=old['tmdb_genre_id'],destination_genre_id=new['genre_id'],source_dataset_hash=snapshot.source_hash,mapping_version=MAPPING_VERSION))
        for old in sorted(rows['movie_source_records'],key=lambda r:r['id']):
            values={k:v for k,v in old.items() if k not in ('id','movie_id','ingest_batch_id')}
            values.update(movie_id=movie_map[old['movie_id']],ingest_batch_id=destination_batch_id)
            new=target.insert('movie_source_records',values);source_map[old['id']]=new['id']
            mappings['movie_source_records'].append(dict(source_record_pk=old['id'],destination_source_record_pk=new['id'],destination_movie_id=new['movie_id'],destination_batch_id=destination_batch_id,source_dataset_hash=snapshot.source_hash,mapping_version=MAPPING_VERSION))
            target.insert('ingest_items',dict(batch_id=destination_batch_id,source_record_id=old['source_record_id'],status='inserted',movie_id=new['movie_id'],payload_hash=old['payload_hash'],attempt_count=1,updated_at=timestamp))
        for name in ('movie_external_ids','movie_aliases','movie_genres','movie_ratings_summary'):
            for old in rows[name]:
                values={k:v for k,v in old.items() if k!='id' and bundle['metadata'].tables[name].c[k].computed is None}
                values['movie_id']=movie_map[old['movie_id']]
                if name=='movie_genres':values['genre_id']=genre_map[old['genre_id']]
                if name=='movie_ratings_summary':values['selected_source_record_pk']=source_map[old['selected_source_record_pk']]
                target.insert(name,values)
        batch.update(status='succeeded',finished_at=timestamp,counts_json={'movies':len(movie_map),'source_records':len(source_map)})
        typed,problems=validate_rows(bundle,target.rows)
        if problems:raise ValueError('TARGET_OFFLINE_VALIDATION:'+digest(problems))
        if fault=='BEFORE_COMMIT':raise RuntimeError('SIMULATED_PRECOMMIT_INTERRUPTION')
        for group in mappings.values():
            for entry in group:
                entry.update(simulation=True,target_namespace=target.namespace,target_version='OFFLINE:'+bundle['contract_hash'])
        receipt=dict(status='SIMULATED_COMMITTED',live=False,replayed=False,source_origin=snapshot.origin,source_dataset_hash=snapshot.source_hash,
                     content_hash=snapshot.seal,contract_hash=bundle['contract_hash'],mapping_version=MAPPING_VERSION,
                     destination_batch_id=destination_batch_id,mappings=mappings,counts={n:len(r) for n,r in typed.items()},target_rows_hash=rows_hash(typed))
        target.rows=typed;target.receipts[key]=copy.deepcopy(receipt);target.bound_source=identity
    except BaseException:
        target.rows,target.receipts,target.bound_source=previous
        raise
    # An unknown outcome is not falsely described as a rollback.
    if fault=='AFTER_COMMIT_UNKNOWN':
        target.uncertain=True;return {'status':'COMMIT_UNKNOWN_DO_NOT_RETRY','live':False}
    return copy.deepcopy(receipt)


def simulate_ddl(bundle,*,fail_at=None,journal=None):
    bundle=verify_bundle(bundle)
    if fail_at is not None and (type(fail_at) is not int or not 0<=fail_at<len(bundle['statements'])):
        raise ValueError('INVALID_FAILURE_POINT')
    if journal is None:journal=[]
    if type(journal) is not list:raise ValueError('MEMORY_JOURNAL_ONLY')
    if journal:raise ValueError('NONEMPTY_TARGET_REQUIRES_DDL_AUDIT')
    for i,statement in enumerate(bundle['statements']):
        if fail_at==i:
            return {'status':'PARTIAL_DDL_AUDIT_REQUIRED','live':False,'journal':copy.deepcopy(journal),'automatic_cleanup':False}
        journal.append({k:statement[k] for k in ('kind','object','sha256')})
    return {'status':'SIMULATED_DDL_COMPLETE','live':False,'journal':copy.deepcopy(journal),'automatic_cleanup':False}
