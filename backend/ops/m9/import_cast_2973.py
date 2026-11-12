"""Fixed, explicitly authorized TMDB81 supplement. No network, DDL or legacy deletion."""
import hashlib,json,os,subprocess,sys
from pathlib import Path

SOURCE_HASH='9ce69be4cc8a4f9290f522ed52dd893d4c96d695e9419f75bc6841bea575d9d8'
UUID='01287100-d9e6-11f1-9713-000c29c02265'
SCHEMA='mi2_m6_realcopy_20261107'
SOURCE=Path('/home/zhang/m9-cast-2973.json')

def run():
    if os.geteuid()!=0:raise RuntimeError('GUEST_SUDO_REQUIRED')
    if Path('/etc/hostname').read_text().strip()!='zhang-VMware-Virtual-Platform':raise RuntimeError('HOST_MISMATCH')
    if SOURCE.is_symlink() or hashlib.sha256(SOURCE.read_bytes()).hexdigest()!=SOURCE_HASH:raise RuntimeError('SOURCE_MISMATCH')
    config=Path('/etc/movieinsight-m6/profiles/real')
    cfg=json.loads((config/'instance.json').read_text())
    if Path('/sys/class/dmi/id/product_uuid').read_text().strip()!=cfg['vm_uuid'] or cfg['schema']!=SCHEMA:raise RuntimeError('VM_OR_SCHEMA_MISMATCH')
    if sys.prefix!='/opt/movieinsight-m6/venv':
        os.execv('/opt/movieinsight-m6/venv/bin/python',['/opt/movieinsight-m6/venv/bin/python','-B',str(Path(__file__).resolve())])
    from datetime import datetime
    import re
    from sqlalchemy import create_engine,MetaData,Table,select,insert,func,text
    from sqlalchemy.engine import make_url
    env=dict(line.split('=',1) for line in (config/'app.env').read_text().splitlines() if line and not line.startswith('#'))
    url=make_url(env['MI2_DATABASE_URL'])
    if url.host!='127.0.0.1' or url.database!=SCHEMA or url.port!=3306:raise RuntimeError('ENDPOINT_MISMATCH')
    engine=create_engine(url,hide_parameters=True,pool_pre_ping=True)
    md=MetaData()
    with engine.connect() as c:
        if c.scalar(text('SELECT @@server_uuid'))!=UUID:raise RuntimeError('SERVER_MISMATCH')
        tables={name:Table(name,md,autoload_with=c) for name in ['movies','movie_external_ids','people','person_external_ids','movie_credits','person_assets']}
    m,e,p,pe,cr,pa=[tables[n] for n in ['movies','movie_external_ids','people','person_external_ids','movie_credits','person_assets']]
    doc=json.loads(SOURCE.read_text());movie=doc['movie'];data=doc['credits']
    if movie['id']!=81 or data['id']!=81 or movie['release_date'][:4]!='1984':raise RuntimeError('SOURCE_IDENTITY_MISMATCH')
    cast=data['cast'];crew=[x for x in data['crew'] if x.get('job')=='Director']
    if len(cast)!=26 or len(crew)!=1:raise RuntimeError('SOURCE_COUNTS_MISMATCH')
    entries=[('cast',x) for x in cast]+[('crew',x) for x in crew]
    for role,x in entries:
        if type(x.get('id')) is not int or x['id']<1 or not x.get('name') or len(x['name'])>255 or not x.get('credit_id'):raise RuntimeError('INVALID_PERSON')
        if x.get('profile_path') and not re.fullmatch(r'/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)',x['profile_path'],re.I):raise RuntimeError('INVALID_PHOTO_PATH')
    def identity(c):
        row=c.execute(select(m.c.title,m.c.release_year).where(m.c.movie_id==2973).with_for_update()).one()
        mid=c.scalar(select(e.c.movie_id).where(e.c.source=='tmdb',e.c.external_id=='81'))
        if row.release_year!=1984 or mid!=2973:raise RuntimeError('MOVIE_MAPPING_MISMATCH')
    def legacy(c):return [dict(r) for r in c.execute(select(cr).where(cr.c.movie_id==2973,cr.c.source!='tmdb').order_by(cr.c.credit_id)).mappings()]
    def other_count(c):return c.scalar(select(func.count()).select_from(cr).where(cr.c.movie_id!=2973))
    with engine.connect() as c:
        identity(c)
        before=legacy(c);other_before=other_count(c)
    # Fixed existing backup entry preserves current schemas; never print its private contents.
    backup=subprocess.run(['/usr/local/sbin/mi2-m6-operations','backup'],capture_output=True)
    if backup.returncode:raise RuntimeError('BACKUP_FAILED_NO_WRITE')
    print(json.dumps({'backup_created':True,'scope':'existing M6 backup operation'}),flush=True)
    def apply():
        counts={'people':0,'identities':0,'photos':0,'credits':0}
        with engine.begin() as c:
            identity(c)
            if c.scalar(text("SELECT GET_LOCK('mi2-m9-cast-2973',10)"))!=1:raise RuntimeError('IMPORT_BUSY')
            try:
                for role,x in entries:
                    external=str(x['id']);now=datetime.utcnow()
                    pid=c.scalar(select(pe.c.person_id).where(pe.c.source=='tmdb',pe.c.external_id==external).with_for_update())
                    if pid is None:
                        pid=c.execute(insert(p).values(name=x['name'],original_name=x.get('original_name'),created_at=now,updated_at=now)).inserted_primary_key[0];counts['people']+=1
                        c.execute(insert(pe).values(person_id=pid,source='tmdb',external_id=external,verified_method='tmdb_credits',verified_at=now));counts['identities']+=1
                    path=x.get('profile_path')
                    if path:
                        found=c.scalar(select(pa.c.asset_id).where(pa.c.person_id==pid,pa.c.source=='tmdb',pa.c.source_path==path))
                        if found is None:
                            primary=c.scalar(select(pa.c.asset_id).where(pa.c.person_id==pid,pa.c.is_primary==True).limit(1))
                            c.execute(insert(pa).values(person_id=pid,source='tmdb',source_path=path,is_primary=primary is None,license_status='unknown',updated_at=now));counts['photos']+=1
                    key=role+':'+x['credit_id']
                    found=c.execute(select(cr.c.person_id,cr.c.role_type,cr.c.character_name,cr.c.sort_order).where(cr.c.movie_id==2973,cr.c.source=='tmdb',cr.c.source_credit_key==key)).first()
                    expected=(pid,role,(x.get('character') or None) if role=='cast' else None,int(x.get('order') or 0) if role=='cast' else 0)
                    if found is not None:
                        if tuple(found)!=expected:raise RuntimeError('EXISTING_CREDIT_CONFLICT')
                    else:
                        c.execute(insert(cr).values(movie_id=2973,person_id=pid,source='tmdb',source_credit_key=key,role_type=role,job='Director' if role=='crew' else None,character_name=expected[2],sort_order=expected[3]));counts['credits']+=1
                if legacy(c)!=before or other_count(c)!=other_before:raise RuntimeError('PRESERVATION_CHECK_FAILED')
            finally:c.execute(text("SELECT RELEASE_LOCK('mi2-m9-cast-2973')"))
        return counts
    first=apply()
    second=apply()
    if any(second.values()):raise RuntimeError('REPLAY_ADDED_ROWS')
    with engine.connect() as c:
        if legacy(c)!=before or other_count(c)!=other_before:raise RuntimeError('FINAL_PRESERVATION_FAILED')
        casts=c.scalar(select(func.count()).select_from(cr).where(cr.c.movie_id==2973,cr.c.source=='tmdb',cr.c.role_type=='cast'))
        if casts!=26:raise RuntimeError('FINAL_CAST_COUNT_FAILED')
    print(json.dumps({'movie_id':2973,'tmdb_id':81,'source_sha256':SOURCE_HASH,'inserted':first,'replay_inserted':second,'cast':casts,'legacy_credits_preserved':len(before),'other_movie_credits_unchanged':True,'secrets_printed':False}),flush=True)
    print('M9_CAST_2973_SUCCESS_NO_SECRETS_PRINTED',flush=True)

if __name__=='__main__':
    try:run()
    except Exception as error:
        print(json.dumps({'passed':False,'error_type':type(error).__name__,'automatic_retry':False,'secrets_printed':False}),flush=True)
        raise SystemExit(1)
