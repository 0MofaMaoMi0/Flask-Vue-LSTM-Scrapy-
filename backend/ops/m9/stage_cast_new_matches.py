"""Stage only unique exact-title/year candidates corroborated by premiere date or director."""
import importlib.util,json,re,time,tarfile,hashlib
from pathlib import Path
spec=importlib.util.spec_from_file_location('review',Path(__file__).with_name('review_cast_exceptions.py'));review=importlib.util.module_from_spec(spec);spec.loader.exec_module(review)
WORK=review.WORK;OUT=WORK/'new-identity-capture';OUT.mkdir(exist_ok=True)
normal=lambda v:re.sub(r'\W+','',str(v)).casefold()
results=[]
for file in sorted((WORK/'identity-review').glob('*.json')):
    row=json.loads(file.read_text(encoding='utf-8'));catalog=row.get('catalog',{})
    candidates=[x for x in row.get('candidates',[]) if normal(x['title'])==normal(catalog.get('title','')) and x.get('release_date','')[:4]==str(catalog.get('release_year'))]
    if len(candidates)!=1:continue
    mid=row['movie_id'];candidate=candidates[0];tid=str(candidate['id'])
    try:
        d=review.get('http://127.0.0.1:5174/api/catalog/movies/'+str(mid))['data']
        if any(x['source']=='tmdb' for x in d['external_ids']):raise RuntimeError('MAPPING_ALREADY_PRESENT')
        try:
            review.get('http://127.0.0.1:5174/api/catalog/lookup?source=tmdb&external_id='+tid)
            raise RuntimeError('TMDB_ALREADY_MAPPED')
        except __import__('urllib.error',fromlist=['HTTPError']).HTTPError as error:
            if error.code!=404:raise
        meta=review.get('https://api.themoviedb.org/3/movie/'+tid+'?language=zh-CN&append_to_response=credits,release_dates',True)
        credits=meta.pop('credits');credits.setdefault('id',meta['id'])
        if str(meta['id'])!=tid or normal(meta['title'])!=normal(d['title']) or meta.get('release_date','')[:4]!=str(d['release_year']):raise RuntimeError('IDENTITY_CHANGED')
        directors={normal(x['name']) for x in d['credits'] if x['job']=='Director'}
        official={normal(x['name']) for x in credits.get('crew',[]) if x.get('job')=='Director'}
        dates={x.get('release_date','')[:10] for region in meta.get('release_dates',{}).get('results',[]) for x in region.get('release_dates',[])}
        dates.add(meta.get('release_date'))
        method='exact_title_release_day' if d.get('release_date') in dates else 'exact_title_year_director'
        if method=='exact_title_year_director' and not directors.intersection(official):raise RuntimeError('ADDITIONAL_PROOF_REQUIRED')
        doc={'movie_id':mid,'tmdb_id':tid,'title':d['title'],'release_year':d['release_year'],'catalog_release_date':d.get('release_date'),'movie':meta,'credits':credits,'identity_exception':method}
        (OUT/(str(mid)+'.json')).write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8')
        results.append({'movie_id':mid,'status':'STAGED','method':method})
    except Exception as error:results.append({'movie_id':mid,'status':'REVIEW_REQUIRED','error_type':str(error) if isinstance(error,RuntimeError) else type(error).__name__})
    time.sleep(1)
(OUT/'manifest.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
valid=[r for r in results if r['status']=='STAGED'];bundle=WORK/'m9-cast-newmatches.tar'
with tarfile.open(bundle,'w') as tar:
    for r in valid:tar.add(OUT/(str(r['movie_id'])+'.json'),arcname=str(r['movie_id'])+'.json')
seal={'sealed':True,'movies':len(valid),'sha256':hashlib.sha256(bundle.read_bytes()).hexdigest(),'bytes':bundle.stat().st_size}
(WORK/'newmatches-sealed.json').write_text(json.dumps(seal,indent=2),encoding='utf-8');print(json.dumps(results),flush=True)
