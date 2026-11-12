"""Read-only evidence for ambiguous identity/year records. Never changes mappings."""
import concurrent.futures,json,re,time,urllib.request,urllib.parse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
WORK=ROOT/'项目工作日志/06-开发与验证/M9-全库演职员补充-20261112'
token=re.search(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',(ROOT/'敏感信息调用/TMDB.txt').read_text(encoding='utf-8-sig'))[0]
def get(url,official=False):
    urls=[url,url.replace('api.themoviedb.org','api.tmdb.org')] if official else [url]
    for candidate in urls:
        try:
            req=urllib.request.Request(candidate,headers={'Authorization':'Bearer '+token} if official else {})
            with urllib.request.urlopen(req,timeout=15) as response:return json.load(response)
        except Exception:
            if candidate==urls[-1]:raise
def review(row):
    dest=WORK/'identity-review'/(str(row['movie_id'])+'.json')
    if dest.exists():
        old=json.loads(dest.read_text(encoding='utf-8'))
        if old.get('reviewed_read_only'):return old
    result=dict(row)
    try:
        d=get('http://127.0.0.1:5174/api/catalog/movies/'+str(row['movie_id']))['data']
        result['catalog']={k:d.get(k) for k in ['title','original_title','release_year','release_date','external_ids']}
        tid=next((x['external_id'] for x in d['external_ids'] if x['source']=='tmdb'),None)
        if tid:
            meta=get('https://api.themoviedb.org/3/movie/'+str(tid)+'?language=zh-CN',True)
            result['official']={k:meta.get(k) for k in ['id','title','original_title','release_date','imdb_id']}
        else:
            query=urllib.parse.urlencode({'query':d['title'],'language':'zh-CN','include_adult':'false'})
            meta=get('https://api.themoviedb.org/3/search/movie?'+query,True)
            result['candidates']=[{k:x.get(k) for k in ['id','title','original_title','release_date']} for x in meta.get('results',[])[:5]]
        result['reviewed_read_only']=True
    except Exception as error:result['review_error_type']=type(error).__name__
    dest.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');time.sleep(1)
    return result
if __name__=='__main__':
    (WORK/'identity-review').mkdir(exist_ok=True)
    rows=json.loads((WORK/'capture-partial-manifest.json').read_text(encoding='utf-8'))
    rows=[r for r in rows if r['status'] in ('IDENTITY_REVIEW','NEEDS_IDENTITY_MATCH')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for n,result in enumerate(pool.map(review,rows),1):
            if n%25==0:print(json.dumps({'identity_evidence_collected':n}),flush=True)
