"""Export approved archived features and capture bounded official public boards. No DB writes."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,re,time,urllib.request,urllib.error,os

ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'项目源码/backend/catalog/data'
WORK=ROOT/'项目工作日志/06-开发与验证/M9-八类推荐频道-20261113'
ARCHIVE=ROOT/'项目工作日志/06-开发与验证/M9-全库演职员补充-20261112'
def save(path,doc):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(doc,ensure_ascii=False,separators=(',',':')),encoding='utf-8');os.replace(tmp,path)
def export():
    rows={}
    for folder in [ARCHIVE,ARCHIVE/'regional-year-capture',ARCHIVE/'new-identity-capture']:
        for file in folder.glob('*.json'):
            if not file.stem.isdecimal():continue
            doc=json.loads(file.read_text(encoding='utf-8'));movie=doc.get('movie',{})
            if str(movie.get('id'))!=str(doc.get('tmdb_id')):raise ValueError('ARCHIVE_ID_MISMATCH')
            row={k:movie.get(k) for k in ['id','title','release_date','vote_average','vote_count','popularity','revenue','budget','runtime']}
            row.update(movie_id=doc['movie_id'],archive_date='2026-11-12',provenance='APPROVED_STATIC_TMDB_CAPTURE',payload_sha256=hashlib.sha256(file.read_bytes()).hexdigest())
            rows[str(movie['id'])]=row
    save(OUT/'archived_features.json',{'schema':1,'archive_date':'2026-11-12','rows':list(rows.values())})
    print(json.dumps({'archived_features':len(rows),'database_writes':0}),flush=True)
def capture():
    match=re.search(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',(ROOT/'敏感信息调用/TMDB.txt').read_text(encoding='utf-8-sig'))
    if not match:raise RuntimeError('LOCAL_TMDB_CREDENTIAL_REQUIRED')
    def get(endpoint):
        for host in ['api.themoviedb.org','api.tmdb.org']:
            try:
                req=urllib.request.Request('https://'+host+'/3/'+endpoint,headers={'Authorization':'Bearer '+match[0],'Accept':'application/json'})
                with urllib.request.urlopen(req,timeout=20) as r:return json.load(r)
            except urllib.error.HTTPError as error:
                if error.code==429:raise RuntimeError('TMDB_RATE_LIMIT_STOPPED') from None
                if host=='api.tmdb.org':raise RuntimeError('TMDB_HTTP_'+str(error.code)) from None
            except Exception:
                if host=='api.tmdb.org':raise RuntimeError('TMDB_UNAVAILABLE') from None
    genres=get('genre/movie/list?language=zh-CN')['genres'];time.sleep(.5)
    paths={'popular':'movie/popular','trending_day':'trending/movie/day','trending_week':'trending/movie/week'}
    reports=[]
    for channel,endpoint in paths.items():
        rows=[];seen=set()
        for page in range(1,4):
            payload=get(endpoint+'?language=zh-CN&include_adult=false&page='+str(page))
            for offset,row in enumerate(payload.get('results',[])):
                if type(row.get('id')) is not int or row['id'] in seen:continue
                seen.add(row['id']);rows.append(dict(row,source_rank=(page-1)*20+offset+1))
            time.sleep(.6)
        doc={'schema':1,'channel':channel,'endpoint':endpoint,'captured_at':datetime.now(timezone.utc).isoformat(),'genres':genres,'items':rows,'pages':3}
        save(OUT/(channel+'.json'),doc);reports.append({'channel':channel,'items':len(rows),'captured_at':doc['captured_at']})
        print(json.dumps(reports[-1]),flush=True)
    save(WORK/'官方榜单采集.json',{'reports':reports,'database_writes':0,'secrets_printed':False})
if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--boards-only',action='store_true',help='Refresh only bounded official boards; retain approved archived features.')
    args=parser.parse_args()
    if not args.boards_only:export()
    capture()
