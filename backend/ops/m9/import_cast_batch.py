"""Authorized isolated catalog supplement; sealed bundle only, no network or DDL."""
import hashlib,json,os,subprocess,sys,tarfile,time
from pathlib import Path

SCHEMA='mi2_m6_realcopy_20261107'
SERVER='01287100-d9e6-11f1-9713-000c29c02265'
BUNDLE=Path('/home/zhang/m9-cast-batch.tar')
# Replaced with a concrete digest only when the capture bundle has been sealed.
BUNDLE_SHA256='NOT_SEALED'

def main():
    if os.geteuid()!=0:raise RuntimeError('GUEST_SUDO_REQUIRED')
    config=Path('/etc/movieinsight-m6/profiles/real');cfg=json.loads((config/'instance.json').read_text())
    if Path('/sys/class/dmi/id/product_uuid').read_text().strip()!=cfg['vm_uuid'] or cfg['schema']!=SCHEMA:raise RuntimeError('VM_MISMATCH')
    if sys.prefix!='/opt/movieinsight-m6/venv':os.execv('/opt/movieinsight-m6/venv/bin/python',['/opt/movieinsight-m6/venv/bin/python','-B',str(Path(__file__).resolve())])
    global BUNDLE_SHA256
    seal_path=Path('/home/zhang/m9-cast-batch.ready.json')
    deadline=time.monotonic()+14400
    print('M9_CAST_BATCH_WAITING_FOR_SEALED_CAPTURE',flush=True)
    while not seal_path.is_file():
        if time.monotonic()>deadline:raise RuntimeError('CAPTURE_WAIT_TIMEOUT_NO_WRITE')
        time.sleep(5)
    if seal_path.is_symlink():raise RuntimeError('SEAL_LINK_REJECTED')
    seal=json.loads(seal_path.read_text())
    BUNDLE_SHA256=seal['sha256']
    if seal.get('sealed') is not True or not isinstance(BUNDLE_SHA256,str) or len(BUNDLE_SHA256)!=64 or not 1<=seal.get('movies',0)<=1994:raise RuntimeError('INVALID_SEAL')
    if BUNDLE.is_symlink() or BUNDLE.stat().st_size>512*1024*1024 or hashlib.sha256(BUNDLE.read_bytes()).hexdigest()!=BUNDLE_SHA256:raise RuntimeError('BUNDLE_MISMATCH')
    private=Path(cfg['state'])/'appdata/m9-cast-sealed'
    if private.is_symlink():raise RuntimeError('PRIVATE_LINK_REJECTED')
    private.mkdir(mode=0o700,exist_ok=True)
    sealed_bundle=private/(BUNDLE_SHA256+'.tar')
    if not sealed_bundle.exists():
        import shutil
        shutil.copyfile(BUNDLE,sealed_bundle);sealed_bundle.chmod(0o600)
    if hashlib.sha256(sealed_bundle.read_bytes()).hexdigest()!=BUNDLE_SHA256:raise RuntimeError('PRIVATE_BUNDLE_MISMATCH')
    from datetime import datetime
    import re
    from sqlalchemy import create_engine,MetaData,Table,select,insert,func,text
    from sqlalchemy.engine import make_url
    env=dict(line.split('=',1) for line in (config/'app.env').read_text().splitlines() if line and not line.startswith('#'))
    url=make_url(env['MI2_DATABASE_URL'])
    if url.host!='127.0.0.1' or url.port!=3306 or url.database!=SCHEMA:raise RuntimeError('DATABASE_MISMATCH')
    engine=create_engine(url,hide_parameters=True,pool_pre_ping=True)
    md=MetaData()
    with engine.connect() as c:
        if c.scalar(text('SELECT @@server_uuid'))!=SERVER:raise RuntimeError('SERVER_MISMATCH')
        ts={n:Table(n,md,autoload_with=c) for n in ['movies','movie_external_ids','people','person_external_ids','movie_credits','person_assets']}
    m,e,p,pe,cr,pa=[ts[n] for n in ['movies','movie_external_ids','people','person_external_ids','movie_credits','person_assets']]
    docs=[]
    with tarfile.open(sealed_bundle) as tar:
        members=tar.getmembers()
        if len(members)>2000:raise RuntimeError('BUNDLE_LIMIT')
        for member in members:
            if not member.isfile() or not re.fullmatch(r'[1-9]\d*\.json',member.name) or member.size>8*1024*1024:raise RuntimeError('UNEXPECTED_MEMBER')
            doc=json.load(tar.extractfile(member));mid=doc['movie_id'];tid=str(doc['tmdb_id'])
            if mid!=int(member.name[:-5]) or str(doc['movie']['id'])!=tid or str(doc['credits']['id'])!=tid:raise RuntimeError('IDENTITY_MISMATCH')
            if doc['release_year'] is not None and str(doc['release_year'])!=doc['movie'].get('release_date','')[:4]:raise RuntimeError('YEAR_MISMATCH')
            entries=[('cast',x) for x in doc['credits'].get('cast',[])]+[('crew',x) for x in doc['credits'].get('crew',[]) if x.get('job')=='Director']
            for role,x in entries:
                if type(x.get('id')) is not int or x['id']<1 or not isinstance(x.get('name'),str) or not 1<=len(x['name'])<=255 or not isinstance(x.get('credit_id'),str) or len(x['credit_id'])>120:raise RuntimeError('INVALID_PERSON')
                path=x.get('profile_path')
                if path and not re.fullmatch(r'/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)',path,re.I):raise RuntimeError('INVALID_PHOTO')
            doc['entries']=entries;docs.append(doc)
    if len(docs)!=seal['movies'] or len({d['movie_id'] for d in docs})!=len(docs):raise RuntimeError('BUNDLE_COUNT_MISMATCH')
    def mapping(c,doc):
        mid=doc['movie_id'];row=c.execute(select(m.c.release_year,m.c.catalog_status).where(m.c.movie_id==mid).with_for_update()).one()
        if row.release_year!=doc['release_year'] or row.catalog_status!='active':raise RuntimeError('MOVIE_CHANGED')
        mapped=c.scalar(select(e.c.movie_id).where(e.c.source=='tmdb',e.c.external_id==str(doc['tmdb_id'])))
        if mapped!=mid:raise RuntimeError('MAPPING_CHANGED')
    with engine.connect() as c:
        for doc in docs:mapping(c,doc)
    backup=subprocess.run(['/usr/local/sbin/mi2-m6-operations','backup'],capture_output=True)
    if backup.returncode:raise RuntimeError('BACKUP_FAILED_NO_WRITE')
    print(json.dumps({'backup_created':True,'movies':len(docs),'source_sha256':BUNDLE_SHA256}),flush=True)
    result=[];audit=Path(cfg['state'])/'appdata/m9-cast-batch-audit.json'
    def apply(doc):
        mid=doc['movie_id'];counts={'people':0,'photos':0,'credits':0}
        with engine.begin() as c:
            mapping(c,doc)
            legacy=[dict(r) for r in c.execute(select(cr).where(cr.c.movie_id==mid,cr.c.source!='tmdb').order_by(cr.c.credit_id)).mappings()]
            for role,x in doc['entries']:
                pid=c.scalar(select(pe.c.person_id).where(pe.c.source=='tmdb',pe.c.external_id==str(x['id'])).with_for_update());now=datetime.utcnow()
                if pid is None:
                    pid=c.execute(insert(p).values(name=x['name'],original_name=x.get('original_name'),created_at=now,updated_at=now)).inserted_primary_key[0]
                    c.execute(insert(pe).values(person_id=pid,source='tmdb',external_id=str(x['id']),verified_method='tmdb_credits',verified_at=now));counts['people']+=1
                path=x.get('profile_path')
                if path and c.scalar(select(pa.c.asset_id).where(pa.c.person_id==pid,pa.c.source=='tmdb',pa.c.source_path==path)) is None:
                    primary=c.scalar(select(pa.c.asset_id).where(pa.c.person_id==pid,pa.c.is_primary==True).limit(1))
                    c.execute(insert(pa).values(person_id=pid,source='tmdb',source_path=path,is_primary=primary is None,license_status='unknown',updated_at=now));counts['photos']+=1
                key=role+':'+x['credit_id'];found=c.scalar(select(cr.c.person_id).where(cr.c.movie_id==mid,cr.c.source=='tmdb',cr.c.source_credit_key==key))
                if found is not None:
                    if found!=pid:raise RuntimeError('CREDIT_IDENTITY_CONFLICT')
                else:
                    c.execute(insert(cr).values(movie_id=mid,person_id=pid,source='tmdb',source_credit_key=key,role_type=role,job='Director' if role=='crew' else None,character_name=(x.get('character') or None) if role=='cast' else None,sort_order=int(x.get('order') or 0) if role=='cast' else 0));counts['credits']+=1
            after=[dict(r) for r in c.execute(select(cr).where(cr.c.movie_id==mid,cr.c.source!='tmdb').order_by(cr.c.credit_id)).mappings()]
            if after!=legacy:raise RuntimeError('LEGACY_CHANGED')
        return counts
    for doc in docs:
        row={'movie_id':doc['movie_id']}
        try:
            row['inserted']=apply(doc);replay=apply(doc)
            if any(replay.values()):raise RuntimeError('REPLAY_ADDED_ROWS')
            row['passed']=True
        except Exception as error:row.update(passed=False,error_type=type(error).__name__)
        result.append(row)
        audit.write_text(json.dumps({'bundle_sha256':BUNDLE_SHA256,'results':result},ensure_ascii=False),encoding='utf-8');audit.chmod(0o600)
        if len(result)%100==0:print(json.dumps({'checked':len(result),'passed':sum(r['passed'] for r in result)}),flush=True)
    passed=sum(r['passed'] for r in result)
    print(json.dumps({'movies':len(result),'passed':passed,'failed':len(result)-passed,'audit_file':str(audit),'secrets_printed':False}),flush=True)
    if passed!=len(result):raise RuntimeError('BATCH_HAS_FAILURES')
    print('M9_CAST_BATCH_SUCCESS_NO_SECRETS_PRINTED',flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'passed':False,'error_type':type(error).__name__,'automatic_retry':False,'secrets_printed':False}),flush=True);raise SystemExit(1)
