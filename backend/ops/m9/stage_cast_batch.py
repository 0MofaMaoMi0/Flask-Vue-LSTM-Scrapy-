"""Read-only official capture. No database writes, key in headers only. Resume supported."""
import concurrent.futures,hashlib,json,re,time,urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'项目工作日志/06-开发与验证/M9-全库演职员补充-20261112'
OUT.mkdir(parents=True,exist_ok=True)
TOKEN=re.search(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',(ROOT/'敏感信息调用/TMDB.txt').read_text(encoding='utf-8-sig'))
if not TOKEN:raise RuntimeError('LOCAL_BEARER_REQUIRED')
def local(path):
    with urllib.request.urlopen('http://127.0.0.1:5174/api/catalog/'+path,timeout=25) as r:return json.load(r)['data']
def official(path):
    for host in ['api.themoviedb.org','api.tmdb.org']:
        try:
            req=urllib.request.Request('https://'+host+'/3/'+path,headers={'Authorization':'Bearer '+TOKEN[0],'Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=20) as r:return json.loads(r.read(8*1024*1024))
        except Exception:continue
    raise RuntimeError('UPSTREAM_UNAVAILABLE')
movies=[]
for offset in range(0,10001,100):
    page=local('movies?limit=100&offset='+str(offset)+'&sort=id')['items'];movies.extend(page)
    if len(page)<100:break
def capture(m):
    mid=m['movie_id'];dest=OUT/(str(mid)+'.json')
    try:
        if dest.exists():
            old=json.loads(dest.read_text(encoding='utf-8'))
            if old.get('movie_id')==mid and old.get('release_year')==m.get('release_year'):return {'movie_id':mid,'status':'STAGED','reused':True}
            return {'movie_id':mid,'status':'EXISTING_CAPTURE_CONFLICT'}
        d=local('movies/'+str(mid))
        tid=next((x['external_id'] for x in d['external_ids'] if x['source']=='tmdb'),None)
        if not tid:return {'movie_id':mid,'status':'NEEDS_IDENTITY_MATCH','title':m['title'],'year':m.get('release_year')}
        meta=official('movie/'+str(tid)+'?language=zh-CN&append_to_response=credits');time.sleep(.5)
        if str(meta.get('id'))!=str(tid) or (m.get('release_year') and str(m['release_year'])!=meta.get('release_date','')[:4]):return {'movie_id':mid,'status':'IDENTITY_REVIEW'}
        credits=meta.pop('credits',None)
        if not isinstance(credits,dict):raise ValueError('MISSING_CREDITS')
        credits.setdefault('id',meta['id'])
        if str(credits.get('id'))!=str(tid):raise ValueError('IDENTITY_MISMATCH')
        result={'movie_id':mid,'tmdb_id':str(tid),'title':m['title'],'release_year':m.get('release_year'),'movie':meta,'credits':credits}
        dest.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');time.sleep(.5)
        return {'movie_id':mid,'status':'STAGED','sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}
    except Exception as error:return {'movie_id':mid,'status':'FAILED','error_type':type(error).__name__}
rows=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    for row in pool.map(capture,movies):
        rows.append(row)
        (OUT/'capture-partial-manifest.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
        (OUT/'progress.json').write_text(json.dumps({'total':len(movies),'checked':len(rows),'staged':sum(x['status']=='STAGED' for x in rows),'database_writes':0}),encoding='utf-8')
        if len(rows)%50==0:print(json.dumps({'checked':len(rows),'total':len(movies)}),flush=True)
(OUT/'capture-manifest.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'capture_finished':True,'total':len(rows),'staged':sum(x['status']=='STAGED' for x in rows),'database_writes':0}),flush=True)
