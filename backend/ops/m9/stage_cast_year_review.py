"""Capture corroborated regional-year differences without changing catalog metadata."""
import concurrent.futures,hashlib,json,re,subprocess,tarfile,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
WORK=ROOT/'项目工作日志/06-开发与验证/M9-全库演职员补充-20261112'
OUT=WORK/'regional-year-capture';OUT.mkdir(exist_ok=True)
token=re.search(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+',(ROOT/'敏感信息调用/TMDB.txt').read_text(encoding='utf-8-sig'))[0]
def normalize(v):return re.sub(r'\W+','',str(v)).casefold()
def get(url,official=False):
    for host in (['api.themoviedb.org','api.tmdb.org'] if official else [None]):
        try:
            target=url.replace('api.themoviedb.org',host) if official else url
            request=urllib.request.Request(target,headers={'Authorization':'Bearer '+token} if official else {})
            with urllib.request.urlopen(request,timeout=15) as response:return json.load(response)
        except Exception:
            if host is None or host=='api.tmdb.org':raise
def capture(row):
    mid=row['movie_id'];dest=OUT/(str(mid)+'.json')
    if dest.exists():return {'movie_id':mid,'status':'STAGED'}
    try:
        d=get('http://127.0.0.1:5174/api/catalog/movies/'+str(mid))['data']
        tid=next(x['external_id'] for x in d['external_ids'] if x['source']=='tmdb')
        meta=get('https://api.themoviedb.org/3/movie/'+str(tid)+'?language=zh-CN&append_to_response=credits,release_dates',True)
        if str(meta.get('id'))!=str(tid) or normalize(meta.get('title'))!=normalize(d['title']):return {'movie_id':mid,'status':'TITLE_REVIEW_REQUIRED'}
        year=d.get('release_year');years=[int(x['release_date'][:4]) for region in meta.get('release_dates',{}).get('results',[]) for x in region.get('release_dates',[]) if re.match(r'^\d{4}-',x.get('release_date',''))]
        if year not in years:return {'movie_id':mid,'status':'REGIONAL_YEAR_NOT_CORROBORATED'}
        credits=meta.pop('credits');credits.setdefault('id',meta['id'])
        doc={'movie_id':mid,'tmdb_id':str(tid),'title':d['title'],'release_year':year,'movie':meta,'credits':credits,'identity_exception':'existing_tmdb_exact_title_official_regional_year'}
        dest.write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8')
        return {'movie_id':mid,'status':'STAGED'}
    except Exception as error:return {'movie_id':mid,'status':'FAILED','error_type':type(error).__name__}
    finally:time.sleep(.5)
if __name__=='__main__':
    while not (WORK/'sealed-bundle.json').exists():time.sleep(5)
    rows=[r for r in json.loads((WORK/'capture-manifest.json').read_text(encoding='utf-8')) if r['status']=='IDENTITY_REVIEW']
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for row in pool.map(capture,rows):
            results.append(row)
            if len(results)%20==0:print(json.dumps({'regional_checked':len(results)}),flush=True)
    (OUT/'manifest.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    valid=[r for r in results if r['status']=='STAGED'];bundle=WORK/'m9-cast-regional.tar'
    with tarfile.open(bundle,'w') as tar:
        for row in valid:tar.add(OUT/(str(row['movie_id'])+'.json'),arcname=str(row['movie_id'])+'.json')
    seal={'sealed':True,'movies':len(valid),'sha256':hashlib.sha256(bundle.read_bytes()).hexdigest(),'bytes':bundle.stat().st_size}
    (WORK/'regional-sealed.json').write_text(json.dumps(seal,indent=2),encoding='utf-8')
    print(json.dumps({'regional_verified':len(valid),'unresolved':len(results)-len(valid)}),flush=True)
