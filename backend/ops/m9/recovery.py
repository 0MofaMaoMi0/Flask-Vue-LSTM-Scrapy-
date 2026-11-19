"""Fixed dedicated-guest recovery. Private dumps; detached schemas; no network providers."""
import base64, hashlib, json, os, re, shlex, shutil, stat, subprocess, sys
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))

ROOT=Path('/opt/movieinsight-m6')
PROFILE=Path('/etc/movieinsight-m6/profiles/real')
OPS=Path('/var/lib/movieinsight-m6-operations')
MASTER=Path('/etc/movieinsight-credentials/master.key')
KEYS=Path('/etc/movieinsight-credentials/recovery-keys')
SERVER_UUID='01287100-d9e6-11f1-9713-000c29c02265'
VM_UUID='3f614d56-7436-7a96-c8ea-a786bcc02265'
SCHEMAS=('mi2_m6_realcopy_20261107','mi2_m6_realactivity_20261107','mi2_m7_assistant_20261107')
FINGERPRINT_FORMAT='mysql-canonical-v3'
STAGE='PREFLIGHT'
FAILURE_INFO={}

def progress(stage):
    global STAGE
    STAGE=stage

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def private(path):
    s=path.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_mode&0o077:raise RuntimeError('PRIVATE_FILE_REQUIRED')
    return path

def envfile(path):
    private(path)
    return {k:shlex.split(v)[0] if shlex.split(v) else '' for line in path.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith('#') for k,sep,v in [line.partition('=')] if sep}

def connect():
    import pymysql
    return pymysql.connect(user='root',unix_socket='/var/run/mysqld/mysqld.sock',charset='utf8mb4',autocommit=True,connect_timeout=10)

def query(c,sql,args=()):
    with c.cursor() as cur:cur.execute(sql,args);return cur.fetchall()

def scalar(c,sql,args=()):return query(c,sql,args)[0][0]

def identity(c):
    if os.geteuid()!=0 or Path('/sys/class/dmi/id/product_uuid').read_text().strip()!=VM_UUID:raise RuntimeError('VM_IDENTITY_MISMATCH')
    if private(Path('/etc/movieinsight-m6/active-profile')).read_text().strip()!='real':raise RuntimeError('REAL_PROFILE_REQUIRED')
    cfg=json.loads(private(PROFILE/'instance.json').read_text())
    if cfg['schema']!=SCHEMAS[0] or cfg['activity_schema']!=SCHEMAS[1] or cfg['server_uuid']!=SERVER_UUID:raise RuntimeError('PROFILE_MISMATCH')
    if str(scalar(c,'SELECT @@server_uuid'))!=SERVER_UUID or scalar(c,'SELECT @@bind_address')!='127.0.0.1':raise RuntimeError('MYSQL_IDENTITY_MISMATCH')
    if Path(cfg['state'])!=Path('/var/lib/movieinsight-m6-realcopy'):raise RuntimeError('STATE_SCOPE_MISMATCH')
    return cfg

@contextmanager
def maintenance():
    import fcntl
    if OPS.is_symlink():raise RuntimeError('UNSAFE_OPERATIONS_DIRECTORY')
    OPS.mkdir(mode=0o700,exist_ok=True)
    if OPS.stat().st_uid!=0 or OPS.stat().st_mode&0o077:raise RuntimeError('PRIVATE_OPERATIONS_DIRECTORY_REQUIRED')
    fd=os.open(OPS/'maintenance.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:os.close(fd)

def contracts():
    from app.models import Base
    from app.m6_activity import activity_tables
    from catalog.models import CatalogBase
    from catalog.read_queries import _PERSON_ASSETS
    from catalog.recommendation_feedback import tables as feedback
    from assistant.store import tables as ledger
    from assistant.credential_store import credential_table
    from assistant.personal_features import tables as personal
    groups=[list(Base.metadata.tables.values())+list(CatalogBase.metadata.tables.values())+[_PERSON_ASSETS],
            list(activity_tables(SCHEMAS[1],SCHEMAS[0]).values())+list(feedback(SCHEMAS[1])),
            list(ledger().tables.values())+[credential_table()]+list(personal().values())]
    expected={s:{t.name:list(t.c.keys()) for t in tables} for s,tables in zip(SCHEMAS,groups)}
    expected[SCHEMAS[0]]['alembic_version']=['version_num']
    return expected

def validate_contracts(c):
    from sqlalchemy.engine import make_url
    u=make_url(envfile(Path('/etc/movieinsight-m7/app.env'))['MI2_M7_DATABASE_URL'])
    if u.drivername!='mysql+pymysql' or u.host!='127.0.0.1' or u.port!=3306 or u.database!=SCHEMAS[2] or not re.fullmatch(r'mi2m7[a-z0-9_]{1,24}',u.username or '') or not u.password or u.query:raise RuntimeError('M7_ENDPOINT_MISMATCH')
    expected=contracts()
    for schema,tables in expected.items():
        actual=query(c,'SELECT TABLE_NAME,TABLE_TYPE,ENGINE FROM information_schema.tables WHERE table_schema=%s',(schema,))
        if {r[0] for r in actual}!=set(tables) or any(r[1]!='BASE TABLE' or r[2]!='InnoDB' for r in actual):raise RuntimeError('TABLE_CONTRACT_MISMATCH')
        for table,columns in tables.items():
            found=[r[0] for r in query(c,'SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ORDINAL_POSITION',(schema,table))]
            if found!=columns:raise RuntimeError('COLUMN_CONTRACT_MISMATCH')
        for kind in ('TRIGGERS','EVENTS','ROUTINES'):
            column={'TRIGGERS':'TRIGGER_SCHEMA','EVENTS':'EVENT_SCHEMA','ROUTINES':'ROUTINE_SCHEMA'}[kind]
            if scalar(c,'SELECT COUNT(*) FROM information_schema.'+kind+' WHERE '+column+'=%s',(schema,)):raise RuntimeError('UNSUPPORTED_STORED_OBJECT')
    for row in query(c,'SHOW GRANTS FOR %s@%s',(u.username,'127.0.0.1')):
        validate_grant(row[0],SCHEMAS[2],set(expected[SCHEMAS[2]]))
    return expected

def validate_grant(line,schema,tables):
    m=re.fullmatch(r'GRANT (.+?) ON (.+?) TO .+',line)
    if not m or 'WITH GRANT OPTION' in line:raise RuntimeError('APPLICATION_GRANT_MISMATCH')
    rights={x.strip() for x in m[1].split(',')};scope=m[2]
    if scope=='*.*' and rights=={'USAGE'}:return
    allowed={'`'+schema+'`.*'}|{'`'+schema+'`.`'+t+'`' for t in tables}
    if scope not in allowed or not rights<= {'SELECT','INSERT','UPDATE','DELETE'}:raise RuntimeError('APPLICATION_GRANT_MISMATCH')

def canonical(value):
    if isinstance(value,bytes):return {'binary':base64.b64encode(value).decode()}
    if isinstance(value,(date,Decimal)):return {'type':type(value).__name__,'value':str(value)}
    return value

def normalize_ddl(ddl,schemas):
    for i,schema in enumerate(schemas):ddl=re.sub(r'(?<=REFERENCES )`'+re.escape(schema)+r'`\.', '`schema_'+str(i)+'`.',ddl)
    # mysqldump may make a column's inherited table charset explicit. Keep its
    # collation and every real charset difference; remove only that redundancy.
    match=re.search(r'\) ENGINE=\w+ .*?DEFAULT CHARSET=([a-z0-9_]+)\b',ddl)
    if match:
        prefix=r'^(\s*`[^`]+`\s+(?:(?:var)?char\(\d+\)|(?:tiny|medium|long)?text))'
        ddl='\n'.join(re.sub(prefix+r' CHARACTER SET '+re.escape(match[1])+r'(?= COLLATE )',r'\1',line) for line in ddl.split('\n'))
    return ddl

def table_digest(c,schema,table,schema_names):
    import pymysql
    if not re.fullmatch(r'mi2_(?:m6|m7)_[a-z0-9_]+',schema) or not re.fullmatch(r'[a-z0-9_]+',table):raise RuntimeError('IDENTIFIER_REJECTED')
    ddl=query(c,'SHOW CREATE TABLE `'+schema+'`.`'+table+'`')[0][1]
    order=[r[0] for r in query(c,"SELECT COLUMN_NAME FROM information_schema.key_column_usage WHERE table_schema=%s AND table_name=%s AND constraint_name='PRIMARY' ORDER BY ORDINAL_POSITION",(schema,table))]
    if not order:raise RuntimeError('PRIMARY_KEY_REQUIRED')
    h=hashlib.sha256();count=0
    with c.cursor(pymysql.cursors.SSCursor) as cur:
        cur.execute('SELECT * FROM `'+schema+'`.`'+table+'` ORDER BY '+','.join('`'+col+'`' for col in order))
        for row in cur:
            h.update(json.dumps([canonical(v) for v in row],ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()+b'\n');count+=1
    checksum=query(c,'CHECKSUM TABLE `'+schema+'`.`'+table+'` EXTENDED')[0][1]
    if checksum is None:raise RuntimeError('MYSQL_CHECKSUM_UNAVAILABLE')
    return dict(rows=count,mysql_checksum=int(checksum),content_sha256=h.hexdigest(),structure_sha256=hashlib.sha256(normalize_ddl(ddl,schema_names).encode()).hexdigest())

def rewrite_fk_dump(payload,mapping):
    lines=payload.splitlines(keepends=True)
    for i,line in enumerate(lines):
        if re.match(r'^\s*(?:CONSTRAINT\s+`[^`]+`\s+)?FOREIGN KEY\b',line):
            for old,new in mapping.items():line=line.replace('REFERENCES `'+old+'`.','REFERENCES `'+new+'`.')
            lines[i]=line
        elif re.match(r'^\s*(?:USE|CREATE DATABASE|DROP DATABASE)\b',line,re.I):raise RuntimeError('DUMP_SCHEMA_SWITCH_REJECTED')
    return ''.join(lines)

def tree_manifest(root):
    result={}
    def walk(folder):
        for p in sorted(folder.iterdir()):
            if p.is_symlink():raise RuntimeError('SNAPSHOT_LINK_REJECTED')
            if p.is_dir():walk(p)
            elif p.is_file():result[p.relative_to(root).as_posix()]=dict(bytes=p.stat().st_size,sha256=digest(p))
            else:raise RuntimeError('SNAPSHOT_SPECIAL_FILE_REJECTED')
    walk(root)
    return result

def protected_copy(source,target):
    if source.is_symlink():raise RuntimeError('SOURCE_LINK_REJECTED')
    with source.open('rb') as src,target.open('xb') as dst:
        target.chmod(0o600);shutil.copyfileobj(src,dst);dst.flush();os.fsync(dst.fileno())

def snapshot_files(appdata,destination):
    files=tree_manifest(appdata)
    if sum(v['bytes'] for v in files.values())>5_000_000_000:raise RuntimeError('FILE_SNAPSHOT_LIMIT')
    destination.mkdir(mode=0o700)
    for name in files:
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        protected_copy(appdata/name,target)
    if tree_manifest(destination)!=files:raise RuntimeError('FILE_SNAPSHOT_MISMATCH')
    return files

def backup():
    with maintenance(),connect() as c:
        cfg=identity(c);expected=validate_contracts(c);state=Path(cfg['state'])
        if shutil.disk_usage(state).free<3_000_000_000:raise RuntimeError('INSUFFICIENT_HEADROOM')
        backup_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        destination=state/'backups'/backup_id;destination.mkdir(mode=0o700)
        manifest=dict(version=2,fingerprint_format=FINGERPRINT_FORMAT,backup_id=backup_id,server_uuid=SERVER_UUID,schemas={},files={},config={})
        was_running=subprocess.run(['systemctl','is-active','--quiet','movieinsight-m6']).returncode==0
        if was_running:subprocess.run(['systemctl','stop','movieinsight-m6'],check=True,capture_output=True)
        locked=False
        try:
            query(c,'FLUSH TABLES WITH READ LOCK');locked=True
            for schema,tables in expected.items():
                progress('DUMP_'+schema)
                path=destination/(schema+'.sql')
                with path.open('xb') as f:
                    path.chmod(0o600)
                    p=subprocess.run(['/usr/bin/mysqldump','--protocol=socket','--single-transaction','--skip-add-drop-table','--no-tablespaces','--hex-blob',schema],stdout=f,stderr=subprocess.PIPE,timeout=300)
                    f.flush();os.fsync(f.fileno())
                if p.returncode:raise RuntimeError('DUMP_FAILED_PARTIAL_PRESERVED')
                fingerprints={}
                for table in sorted(tables):
                    progress('FINGERPRINT_'+schema+'_'+table);fingerprints[table]=table_digest(c,schema,table,SCHEMAS)
                manifest['schemas'][schema]=dict(sha256=digest(path),bytes=path.stat().st_size,tables=fingerprints)
            outside=query(c,'SELECT DISTINCT UNIQUE_CONSTRAINT_SCHEMA FROM information_schema.referential_constraints WHERE CONSTRAINT_SCHEMA IN (%s,%s,%s)',SCHEMAS)
            if any(r[0] not in SCHEMAS for r in outside):raise RuntimeError('SOURCE_FK_OUTSIDE_SCOPE')
            progress('SNAPSHOT_APPLICATION_FILES');manifest['files']=snapshot_files(state/'appdata',destination/'appdata')
            conf=destination/'configuration';conf.mkdir(mode=0o700)
            for name,path in [('m6-app.env',PROFILE/'app.env'),('m7-app.env',Path('/etc/movieinsight-m7/app.env')),('instance.json',PROFILE/'instance.json')]:
                protected_copy(private(path),conf/name);manifest['config'][name]=digest(conf/name)
            progress('SNAPSHOT_MASTER');private(MASTER)
            if len(MASTER.read_bytes())!=32 or KEYS.is_symlink():raise RuntimeError('MASTER_INVALID')
            KEYS.mkdir(mode=0o700,exist_ok=True)
            if KEYS.stat().st_uid!=0 or KEYS.stat().st_mode&0o077:raise RuntimeError('RECOVERY_KEYS_DIRECTORY_UNSAFE')
            keydir=KEYS/backup_id;keydir.mkdir(mode=0o700)
            protected_copy(MASTER,keydir/'master.key');(keydir/'master.key').chmod(0o400)
            manifest['master_sha256']=digest(keydir/'master.key')
            path=destination/'manifest.json';path.write_text(json.dumps(manifest,indent=2));path.chmod(0o600)
            marker=state/'latest-backup';tmp=marker.with_suffix('.new');tmp.write_text(str(destination));tmp.chmod(0o600);os.replace(tmp,marker)
        finally:
            try:
                if locked:query(c,'UNLOCK TABLES')
            finally:
                if was_running:subprocess.run(['systemctl','start','movieinsight-m6'],check=True,capture_output=True)
        return dict(backup_created=True,backup_id=backup_id,schemas=list(expected),tables=sum(map(len,expected.values())),application_files=len(manifest['files']),consistent_read_lock=True,separate_key_backup=True,secrets_printed=False)

def check_snapshot(destination,manifest):
    if manifest.get('version')!=2 or manifest.get('fingerprint_format')!=FINGERPRINT_FORMAT or tuple(manifest['schemas'])!=SCHEMAS or manifest['server_uuid']!=SERVER_UUID:raise RuntimeError('MANIFEST_SCOPE_MISMATCH')
    for s,info in manifest['schemas'].items():
        if digest(private(destination/(s+'.sql')))!=info['sha256']:raise RuntimeError('DUMP_HASH_MISMATCH')
    if tree_manifest(destination/'appdata')!=manifest['files']:raise RuntimeError('APPLICATION_FILES_MISMATCH')
    for name,h in manifest['config'].items():
        if name not in ('m6-app.env','m7-app.env','instance.json') or digest(private(destination/'configuration'/name))!=h:raise RuntimeError('CONFIGURATION_HASH_MISMATCH')
    key=private(KEYS/destination.name/'master.key')
    if digest(key)!=manifest['master_sha256'] or len(key.read_bytes())!=32:raise RuntimeError('RECOVERY_KEY_MISMATCH')
    return key

def verify_credentials(c,schema,key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    from assistant.credential_store import CredentialStore
    cipher=AESGCM(key.read_bytes());rows=query(c,'SELECT user_id,provider,key_version,nonce,ciphertext FROM `'+schema+'`.provider_credentials');count=0
    for uid,provider,version,nonce,ciphertext in rows:
        if version!=1 or provider not in ('deepseek','tmdb'):raise RuntimeError('CREDENTIAL_FORMAT_MISMATCH')
        value=json.loads(cipher.decrypt(nonce,ciphertext,CredentialStore._aad(uid,provider)))
        if type(value) is not dict:raise RuntimeError('CREDENTIAL_PAYLOAD_MISMATCH')
        count+=1
    if rows:
        uid,provider,_,nonce,ciphertext=rows[0]
        try:AESGCM(os.urandom(32)).decrypt(nonce,ciphertext,CredentialStore._aad(uid,provider))
        except InvalidTag:pass
        else:raise RuntimeError('WRONG_KEY_NOT_REJECTED')
    return dict(restored_credentials_decryptable=count,wrong_key_rejected=bool(rows),provider_calls=0)

def retention_probe(c,target):
    if not re.fullmatch(r'mi2_m6_restore_[a-z0-9_]+',target):raise RuntimeError('RESTORE_ONLY_RETENTION_TARGET_REQUIRED')
    from sqlalchemy import create_engine,insert,select
    from catalog.recommendation_feedback import tables
    from ops.m9.purge_recommendation_feedback import purge
    from datetime import timedelta
    import uuid
    engine=create_engine('mysql+pymysql://root@localhost/'+target+'?unix_socket=/var/run/mysqld/mysqld.sock',hide_parameters=True)
    imp,clicks=tables(target);now=datetime.utcnow();old=now-timedelta(days=181);recent=now-timedelta(days=179)
    ids=[str(uuid.uuid4()) for _ in range(4)];uid=900000000000000001
    try:
        with engine.begin() as con:
            for i,rid in enumerate(ids):
                con.execute(insert(imp).values(request_id=rid,user_id=uid,channel='recovery_probe',period='daily',board_version='probe',context='visible-v2',position=1,movie_id=None,tmdb_id=1,shown_at=recent if i==2 else old))
            for i,when in [(0,old),(1,recent)]:con.execute(insert(clicks).values(request_id=ids[i],position=1,user_id=uid,channel='recovery_probe',movie_id=None,tmdb_id=1,clicked_at=when))
        preview=purge(engine,target,now=now,dry_run=True)
        with engine.connect() as con:
            if len(con.execute(select(imp.c.request_id).where(imp.c.request_id.in_(ids))).all())!=4:raise RuntimeError('RETENTION_DRY_RUN_MUTATED_DATA')
        result=purge(engine,target,now=now)
        with engine.connect() as con:
            left={r[0] for r in con.execute(select(imp.c.request_id).where(imp.c.request_id.in_(ids)))}
            clicked={r[0] for r in con.execute(select(clicks.c.request_id).where(clicks.c.request_id.in_(ids)))}
        if left!={ids[1],ids[2]} or clicked!={ids[1]} or preview['clicks_eligible']!=result['clicks_deleted'] or preview['impressions_eligible']!=result['impressions_deleted']:raise RuntimeError('RETENTION_SEMANTICS_FAILED')
        return dict(all_pass=True,dry_run_no_writes=True,old_exposure_with_recent_click_preserved=True,only_restore_target_written=True)
    finally:engine.dispose()

def restore():
    with maintenance(),connect() as c:
        cfg=identity(c);state=Path(cfg['state']);destination=Path(private(state/'latest-backup').read_text().strip())
        if destination.parent!=state/'backups' or destination.is_symlink() or not re.fullmatch(r'\d{8}T\d{12}Z',destination.name):raise RuntimeError('BACKUP_PATH_MISMATCH')
        manifest=json.loads(private(destination/'manifest.json').read_text());key=check_snapshot(destination,manifest)
        expected=contracts()
        if any(set(manifest['schemas'][s]['tables'])!=set(expected[s]) for s in SCHEMAS):raise RuntimeError('RESTORE_TABLE_CONTRACT_MISMATCH')
        suffix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ').lower()
        mapping={s:('mi2_m7_restore_' if s==SCHEMAS[2] else 'mi2_m6_restore_')+suffix+'_'+str(i) for i,s in enumerate(SCHEMAS)}
        for target in mapping.values():
            if scalar(c,'SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=%s',(target,)):raise RuntimeError('RESTORE_EXISTS_PRESERVED')
        for target in mapping.values():query(c,'CREATE DATABASE `'+target+'` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci')
        results=[]
        for source,target in mapping.items():
            progress('RESTORE_'+source)
            payload=rewrite_fk_dump((destination/(source+'.sql')).read_text(),mapping)
            p=subprocess.run(['/usr/bin/mysql','--protocol=socket',target],input=payload.encode(),capture_output=True,timeout=300)
            if p.returncode:raise RuntimeError('RESTORE_FAILED_TARGET_PRESERVED')
            for table,reference in manifest['schemas'][source]['tables'].items():
                progress('VERIFY_'+source+'_'+table)
                actual=table_digest(c,target,table,tuple(mapping.values()))
                if actual!=reference:
                    import difflib
                    source_ddl=normalize_ddl(query(c,'SHOW CREATE TABLE `'+source+'`.`'+table+'`')[0][1],SCHEMAS)
                    target_ddl=normalize_ddl(query(c,'SHOW CREATE TABLE `'+target+'`.`'+table+'`')[0][1],tuple(mapping.values()))
                    FAILURE_INFO.update(table=table,rows_match=actual['rows']==reference['rows'],content_match=actual['content_sha256']==reference['content_sha256'],structure_match=actual['structure_sha256']==reference['structure_sha256'],ddl_diff=[re.sub(r"'(?:[^'\\]|\\.)*'","'<literal>'",line) for line in list(difflib.unified_diff(source_ddl.splitlines(),target_ddl.splitlines()))[:14]])
                    raise RuntimeError('RESTORED_STRUCTURE_OR_CONTENT_MISMATCH')
            results.append(dict(source_schema=source,restored_schema=target,tables=len(expected[source]),all_rows_and_structure_hashes_match=True))
        deps=query(c,'SELECT DISTINCT UNIQUE_CONSTRAINT_SCHEMA FROM information_schema.referential_constraints WHERE CONSTRAINT_SCHEMA IN (%s,%s,%s)',tuple(mapping.values()))
        if any(r[0] not in mapping.values() for r in deps):raise RuntimeError('RESTORED_FK_OUTSIDE_TARGETS')
        restored=state/'recovery-verification'/suffix;restored.mkdir(parents=True,mode=0o700)
        snapshot_files(destination/'appdata',restored/'appdata');protected_copy(key,restored/'master.key');(restored/'master.key').chmod(0o400)
        progress('VERIFY_CREDENTIALS');credentials=verify_credentials(c,mapping[SCHEMAS[2]],restored/'master.key')
        from ops.m6.m7_reliability import mock_restore_probe
        progress('VERIFY_MOCK_LEDGER');probes=mock_restore_probe(mapping[SCHEMAS[2]])
        progress('VERIFY_RETENTION_IN_RESTORE');retention=retention_probe(c,mapping[SCHEMAS[1]])
        result=dict(restore_test_passed=True,backup_id=destination.name,results=results,external_fk_dependencies=0,application_files=len(manifest['files']),credentials=credentials,m7_mock_probes=probes,retention_probe=retention,original_schemas_written=False,secrets_printed=False)
        report=restored/'result.json';report.write_text(json.dumps(result,indent=2));report.chmod(0o600)
        return result

if __name__=='__main__':
    try:
        if sys.argv[1:] not in (['backup'],['restore-test']):raise RuntimeError('FIXED_ACTION_REQUIRED')
        result=backup() if sys.argv[1]=='backup' else restore()
        result['at']=datetime.now(timezone.utc).isoformat()
        p=OPS/'last-recovery-result.json';p.write_text(json.dumps(result));p.chmod(0o600)
        print(json.dumps(result))
    except Exception as e:
        report=dict(passed=False,stage=STAGE,error_type=type(e).__name__,error_number=e.args[0] if e.args and type(e.args[0]) is int else None,error_code=str(e) if type(e) is RuntimeError and re.fullmatch('[A-Z_]+',str(e)) else 'RECOVERY_STOPPED_DETAILS_PRIVATE',diagnostic=FAILURE_INFO,secrets_printed=False)
        if os.geteuid()==0 and OPS.is_dir() and not OPS.is_symlink():
            p=OPS/'last-recovery-error.json';p.write_text(json.dumps(report));p.chmod(0o600)
        print(json.dumps(report))
        sys.exit(1)
