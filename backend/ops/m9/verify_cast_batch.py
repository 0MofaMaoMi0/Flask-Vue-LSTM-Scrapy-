"""Independent read-only HTTP audit of all sealed movie credits, including pagination."""
import concurrent.futures,collections,json,time,urllib.request,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[4]
WORK=ROOT/'项目工作日志/06-开发与验证/M9-全库演职员补充-20261112'
REGIONAL=sys.argv[1:]==['regional']
NEW_MATCHES=sys.argv[1:]==['newmatches']
CAPTURE=WORK/('regional-year-capture' if REGIONAL else 'new-identity-capture') if REGIONAL or NEW_MATCHES else WORK
def get(path):
    with urllib.request.urlopen('http://127.0.0.1:5174/api/catalog/'+path,timeout=30) as response:
        payload=json.load(response)
    if payload['code']!='OK':raise RuntimeError('API_REJECTED')
    return payload['data']
def verify(row):
    mid=row['movie_id']
    try:
        doc=json.loads((CAPTURE/(str(mid)+'.json')).read_text(encoding='utf-8'))
        mapped=get('lookup?source=tmdb&external_id='+str(doc['tmdb_id']))
        if mapped['movie_id']!=mid:raise RuntimeError('MOVIE_MAPPING_MISMATCH')
        entries=[];offset=0
        while True:
            page=get('movies/'+str(mid)+'/credits?limit=100&offset='+str(offset))
            entries.extend(page['items'])
            if not page['has_more']:break
            next_offset=page['next_offset']
            if type(next_offset) is not int or next_offset<=offset:raise RuntimeError('CURSOR_INVALID')
            offset=next_offset
        ids=[x['credit_id'] for x in entries]
        if len(ids)!=len(set(ids)):raise RuntimeError('DUPLICATE_CREDIT')
        actual=[x for x in entries if x['source']=='tmdb']
        expected=collections.Counter((x.get('character') or None,int(x.get('order') or 0)) for x in doc['credits'].get('cast',[]))
        observed=collections.Counter((x.get('character_name'),x['sort_order']) for x in actual if x['role_type']=='cast')
        if expected-observed:raise RuntimeError('CAST_MISSING')
        directors=[x for x in doc['credits'].get('crew',[]) if x.get('job')=='Director']
        if sum(x['role_type']=='crew' and x['job']=='Director' for x in actual)<len(directors):raise RuntimeError('DIRECTOR_MISSING')
        with_photo=collections.Counter((x.get('character') or None,int(x.get('order') or 0)) for x in doc['credits'].get('cast',[]) if x.get('profile_path'))
        actual_photo=collections.Counter((x.get('character_name'),x['sort_order']) for x in actual if x['role_type']=='cast' and x.get('profile_path'))
        if with_photo-actual_photo:raise RuntimeError('PHOTO_MAPPING_MISSING')
        return {'movie_id':mid,'passed':True,'credits':len(entries),'tmdb_credits':len(actual),'photos':sum(bool(x.get('profile_path')) for x in actual),'multiple_pages':len(entries)>100}
    except Exception as error:return {'movie_id':mid,'passed':False,'error_type':str(error) if isinstance(error,RuntimeError) else type(error).__name__}
    finally:time.sleep(.05)
if __name__=='__main__':
    manifest=CAPTURE/'manifest.json' if REGIONAL or NEW_MATCHES else WORK/'publication-manifest.json'
    rows=[r for r in json.loads(manifest.read_text(encoding='utf-8')) if r['status']=='STAGED']
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(verify,rows):
            results.append(result)
            if len(results)%100==0:print(json.dumps({'audited':len(results),'passed':sum(x['passed'] for x in results)}),flush=True)
    report={'movies':len(results),'passed':sum(x['passed'] for x in results),'failed':sum(not x['passed'] for x in results),'results':results,'database_writes':0}
    (CAPTURE/'独立HTTP入库核验.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='results'}),flush=True)
