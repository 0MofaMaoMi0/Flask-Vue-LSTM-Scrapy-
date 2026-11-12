"""Read the complete current authorized catalog credit inventory, without DB writes."""
import concurrent.futures,importlib.util,json,time
from pathlib import Path
spec=importlib.util.spec_from_file_location('audit',Path(__file__).with_name('verify_cast_batch.py'));audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
WORK=audit.WORK
def inspect(row):
    mid=row['movie_id']
    try:
        items=[];offset=0
        while True:
            page=audit.get('movies/'+str(mid)+'/credits?limit=100&offset='+str(offset));items.extend(page['items'])
            if not page['has_more']:break
            offset=page['next_offset']
        cast=[x for x in items if x['role_type']=='cast'];tmdb=[x for x in items if x['source']=='tmdb']
        return {'movie_id':mid,'passed':True,'credits':len(items),'cast':len(cast),'cast_with_photo':sum(bool(x.get('profile_path')) for x in cast),'tmdb_credits':len(tmdb),'people_with_photo':list({x['person_id'] for x in items if x.get('profile_path')}),'people':list({x['person_id'] for x in items})}
    except Exception as error:return {'movie_id':mid,'passed':False,'error_type':type(error).__name__}
    finally:time.sleep(.1)
rows=json.loads((WORK/'capture-manifest.json').read_text(encoding='utf-8'));results=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    for row in pool.map(inspect,rows):
        results.append(row)
        if len(results)%500==0:print(json.dumps({'inventory_checked':len(results)}),flush=True)
# Preserve the first outcome and retry only read failures once.
first=[x for x in results if not x['passed']]
if first:
    (WORK/'全库覆盖盘点-首次网络失败.json').write_text(json.dumps(first,indent=2),encoding='utf-8')
    replacements={x['movie_id']:inspect(x) for x in first};results=[replacements.get(x['movie_id'],x) for x in results]
valid=[x for x in results if x['passed']]
summary={'movies':len(rows),'audited':len(valid),'failed':len(rows)-len(valid),'movies_with_cast':sum(x['cast']>0 for x in valid),'movies_with_cast_photo':sum(x['cast_with_photo']>0 for x in valid),'movies_with_tmdb_credits':sum(x['tmdb_credits']>0 for x in valid),'cast_credit_rows':sum(x['cast'] for x in valid),'cast_credit_rows_with_photo':sum(x['cast_with_photo'] for x in valid),'distinct_people':len({pid for x in valid for pid in x['people']}),'distinct_people_with_photo':len({pid for x in valid for pid in x['people_with_photo']}),'database_writes':0}
(WORK/'全库覆盖盘点.json').write_text(json.dumps({'summary':summary,'results':results},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary),flush=True)
