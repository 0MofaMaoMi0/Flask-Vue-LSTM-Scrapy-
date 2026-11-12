"""Wait for read-only capture, validate each file, seal and publish to the waiting guest job."""
from pathlib import Path
import hashlib,json,subprocess,tarfile,time

ROOT=Path(__file__).resolve().parents[4]
WORK=ROOT/'项目工作日志/06-开发与验证/M9-全库演职员补充-20261112'
ssh=['C:/Windows/System32/OpenSSH/ssh.exe','-F','none','-i','F:/M6-VM-control-20261106-183615/private/m6_ed25519','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','HostKeyAlias=192.168.147.131','-o','UserKnownHostsFile=F:/M6-VM-control-20261106-183615/private/known_hosts','zhang@192.168.147.50']
manifest=WORK/'capture-manifest.json'
while not manifest.exists():time.sleep(5)
rows=json.loads(manifest.read_text(encoding='utf-8'))
# One bounded metadata retry for transport failures; never retry database writes.
import re,urllib.request
token=re.search(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',(ROOT/'敏感信息调用/TMDB.txt').read_text(encoding='utf-8-sig'))
def read_json(url,official=False):
    req=urllib.request.Request(url,headers={'Authorization':'Bearer '+token[0]} if official else {})
    with urllib.request.urlopen(req,timeout=25) as response:return json.load(response)
for row in rows:
    if row['status']!='FAILED':continue
    mid=row['movie_id']
    try:
        detail=read_json('http://127.0.0.1:5174/api/catalog/movies/'+str(mid))['data']
        tid=next((x['external_id'] for x in detail['external_ids'] if x['source']=='tmdb'),None)
        if not tid:continue
        meta=read_json('https://api.themoviedb.org/3/movie/'+str(tid)+'?language=zh-CN&append_to_response=credits',True)
        if str(meta['id'])!=str(tid) or (detail.get('release_year') is not None and str(detail['release_year'])!=meta.get('release_date','')[:4]):continue
        credits=meta.pop('credits');credits.setdefault('id',meta['id'])
        doc={'movie_id':mid,'tmdb_id':str(tid),'title':detail['title'],'release_year':detail.get('release_year'),'movie':meta,'credits':credits}
        (WORK/(str(mid)+'.json')).write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8')
        row.update(status='STAGED',transport_retry=True)
    except Exception as error:row['retry_error_type']=type(error).__name__
    time.sleep(1)
(WORK/'publication-manifest.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
valid=[];exceptions=[]
for row in rows:
    mid=row['movie_id'];path=WORK/(str(mid)+'.json')
    if row['status']!='STAGED':exceptions.append(row);continue
    try:
        doc=json.loads(path.read_text(encoding='utf-8'))
        if doc['movie_id']!=mid or str(doc['movie']['id'])!=str(doc['tmdb_id']) or str(doc['credits']['id'])!=str(doc['tmdb_id']):raise ValueError('identity')
        if doc.get('release_year') is not None and str(doc['release_year'])!=doc['movie'].get('release_date','')[:4]:raise ValueError('year')
        valid.append(path)
    except Exception as error:exceptions.append({'movie_id':mid,'status':'SEAL_REJECTED','error_type':type(error).__name__})
if len(rows)!=1994 or len({r['movie_id'] for r in rows})!=1994:raise RuntimeError('CATALOG_SCOPE_CHANGED')
(WORK/'未封存条目.json').write_text(json.dumps(exceptions,ensure_ascii=False,indent=2),encoding='utf-8')
bundle=WORK/'m9-cast-batch.tar'
with tarfile.open(bundle,'w') as tar:
    for path in valid:tar.add(path,arcname=path.name)
seal={'sealed':True,'movies':len(valid),'sha256':hashlib.sha256(bundle.read_bytes()).hexdigest(),'bytes':bundle.stat().st_size,'unresolved':len(exceptions)}
(WORK/'sealed-bundle.json').write_text(json.dumps(seal,indent=2),encoding='utf-8')
scp=['C:/Windows/System32/OpenSSH/scp.exe',*ssh[1:-1],str(bundle),'zhang@192.168.147.50:/home/zhang/m9-cast-batch.tar.upload']
subprocess.run(scp,check=True)
code='''from pathlib import Path
import hashlib,json,os
base=Path.home();source=base/'m9-cast-batch.tar.upload'
seal=json.loads(%r)
if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest()!=seal['sha256']:raise RuntimeError('UPLOAD_MISMATCH')
source.chmod(0o600);os.replace(source,base/'m9-cast-batch.tar')
tmp=base/'m9-cast-batch.ready.upload';tmp.write_text(json.dumps(seal));tmp.chmod(0o600);os.replace(tmp,base/'m9-cast-batch.ready.json')
print('SEALED_CAPTURE_DELIVERED_NO_SECRETS_PRINTED')
'''%json.dumps(seal)
subprocess.run(ssh+['python3 -'],input=code.encode(),check=True)
print(json.dumps(seal),flush=True)
