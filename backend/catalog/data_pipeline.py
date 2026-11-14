"""Local-only metadata pipeline. No networking, credentials or database writes."""
import argparse,csv,hashlib,json,os,pathlib,re,tempfile,unicodedata
from datetime import date,datetime
from decimal import Decimal,InvalidOperation

CHANNELS={'tmdb':'tmdb','legacy_movies':'douban','legacy_movies2':'douban'}
FIELDS={'source_record_id','external_ids','legacy_table','legacy_pk','title','original_title','aliases','release_date','release_year','runtime_minutes','language','genres','people','overview','ratings','media_type'}
def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(x):return hashlib.sha256(canonical(x).encode()).hexdigest()
def text(x,maximum):
    if not isinstance(x,str):raise ValueError('invalid text')
    s=re.sub(r'\s+',' ',unicodedata.normalize('NFKC',x)).strip()
    if not s or len(s)>maximum:raise ValueError('invalid text length')
    return s
def number(x,low,high):
    if type(x) is str and x.isdecimal():x=int(x)
    if type(x) is not int or not low<=x<=high:raise ValueError('invalid numeric range')
    return x

def normalize(r,meta):
    if type(r) is not dict or set(r)-FIELDS:raise ValueError('unknown/private fields rejected')
    if (r.get('media_type') or 'movie')!='movie':raise ValueError('excluded non-movie')
    title=text(r.get('title'),255);rid=text(r.get('source_record_id'),128)
    ids=r.get('external_ids') or {}
    if not isinstance(ids,dict) or not ids:raise ValueError('external identity required')
    if any(type(v) is not str or not v.strip() or len(v.strip())>128 for v in ids.values()):raise ValueError('invalid external id')
    ids={k:v.strip() for k,v in ids.items()}
    if any(k not in ('douban','tmdb','imdb','wikidata') for k in ids):raise ValueError('unknown identity source')
    year=r.get('release_year');year=number(year,1800,2199) if year not in (None,'',0,'0') else None
    day=r.get('release_date') or None
    if day:
        day=date.fromisoformat(day).isoformat()
        if year and int(day[:4])!=year:raise ValueError('date/year conflict')
        year=number(int(day[:4]),1800,2199)
    runtime=r.get('runtime_minutes');runtime=number(runtime,1,1500) if runtime not in (None,'',0,'0') else None
    def names(field,maxlen=64):
        values=r.get(field,[])
        if not isinstance(values,list):raise ValueError('list expected')
        return sorted(set(text(x,maxlen) for x in values))
    facts=[]
    for rating in r.get('ratings',[]):
        if type(rating) is not dict or set(rating)-{'source','value','scale','votes','observed_at'}:raise ValueError('invalid rating fields')
        platform=text(rating.get('source'),16)
        if platform not in ('douban','tmdb','imdb'):raise ValueError('unknown rating source')
        scale=Decimal(str(rating.get('scale')));value=Decimal(str(rating['value'])) if rating.get('value') is not None else None
        votes=rating.get('votes');votes=number(votes,0,9223372036854775807) if votes is not None else None
        if not scale.is_finite() or scale<=0 or scale>32767 or scale!=scale.to_integral_value() or (value is not None and (not value.is_finite() or not 0<=value<=min(scale,Decimal('999.99')) or value.as_tuple().exponent < -2)):raise ValueError('rating range')
        if votes==0 and value is not None:raise ValueError('zero votes requires null score')
        observed=rating.get('observed_at');datetime.fromisoformat(observed)
        facts.append({'source':platform,'score_value':str(value) if value is not None else None,'score_scale':str(scale),'vote_count':votes,'observed_at':observed})
    people=r.get('people',[])
    if not isinstance(people,list):raise ValueError('invalid people')
    clean_people=[]
    for person in people:
        if not isinstance(person,dict) or set(person)-{'source','external_id','name','role'}:raise ValueError('invalid person fields')
        if person.get('source') not in ('douban','tmdb','imdb'):raise ValueError('person source')
        if person.get('role') not in ('cast','crew'):raise ValueError('person role')
        clean_people.append({k:text(person[k],255 if k=='name' else 128) for k in ('source','external_id','name','role')})
    legacy=None
    if r.get('legacy_table') not in (None,''):
        if r['legacy_table'] not in ('movies','movies2') or 'douban' not in ids:raise ValueError('invalid legacy mapping')
        legacy={'legacy_table':r['legacy_table'],'legacy_pk':number(r.get('legacy_pk'),1,9223372036854775807),'legacy_douban_id':ids['douban']}
    return {'source_record_id':rid,'source':meta['source'],'payload_hash':digest(r),'title':title,'title_normalized':title.casefold(),'original_title':text(r['original_title'],255) if r.get('original_title') else None,'aliases':names('aliases',255),'release_date':day,'release_year':year,'runtime_minutes':runtime,'original_language':text(r['language'],16) if r.get('language') else None,'genres':names('genres'),'people':clean_people,'overview':text(r['overview'],10000) if r.get('overview') else None,'external_ids':ids,'rating_facts':facts,'legacy':legacy,'catalog_status':'draft','license_status':meta['license_status']}

def build(envelope):
    if type(envelope) is not dict or set(envelope)!={'metadata','records'}:raise ValueError('input envelope')
    meta=envelope['metadata']
    if type(meta) is not dict or set(meta)!={'source','mode','license_status','license_ref','imported_at'}:raise ValueError('metadata fields')
    if meta['source'] not in CHANNELS or meta['mode'] not in ('SYNTHETIC','AUTHORIZED_LOCAL'):raise ValueError('source/mode')
    if meta['license_status'] not in ('allowed','unknown','restricted'):raise ValueError('license status')
    text(meta['license_ref'],255);datetime.fromisoformat(meta['imported_at'])
    if type(envelope['records']) is not list or len(envelope['records'])>100000:raise ValueError('record count limit')
    rows=[];issues=[]
    for raw in envelope['records']:
        try:rows.append(normalize(raw,meta))
        except (ValueError,TypeError,KeyError,InvalidOperation,OverflowError):issues.append({'payload_hash':digest(raw),'status':'rejected','reason':'invalid_record'})
    rows.sort(key=lambda x:x['payload_hash']);owners={}
    for row in rows:
        identity=(row['title_normalized'],row['release_year'],row['runtime_minutes'],row['release_date'],row['original_language'])
        owners.setdefault(('__record__',row['source']+':'+row['source_record_id']),set()).add(row['payload_hash'])
        for k,v in row['external_ids'].items():owners.setdefault((k,v),set()).add(identity)
    conflicts={k for k,v in owners.items() if len(v)>1};accepted=[];reviews=[];index={};candidates={};seen=set();duplicates=0
    for row in rows:
        if ('__record__',row['source']+':'+row['source_record_id']) in conflicts or any(k in conflicts for k in row['external_ids'].items()):issues.append({'payload_hash':row['payload_hash'],'status':'quarantined','reason':'external_id_identity_conflict'});continue
        if meta['license_status']!='allowed':issues.append({'payload_hash':row['payload_hash'],'status':'quarantined','reason':'license_not_allowed'});continue
        if row['payload_hash'] in seen:duplicates+=1;continue
        seen.add(row['payload_hash']);links={index[k] for k in row['external_ids'].items() if k in index}
        if len(links)>1:reviews.append({'payload_hash':row['payload_hash'],'reason':'identity_bridge_requires_review'});continue
        key=(row['title_normalized'],row['release_year'])
        if not links and key in candidates:reviews.append({'payload_hash':row['payload_hash'],'reason':'title_year_candidate_only','candidate_movie_id':accepted[candidates[key]]['movie_id']});continue
        if links:
            idx=next(iter(links));movie=accepted[idx];duplicates+=1
            for k,v in row['external_ids'].items():
                if k in movie['external_ids'] and movie['external_ids'][k]!=v:reviews.append({'payload_hash':row['payload_hash'],'reason':'second_core_identity'});break
            else:
                movie['external_ids'].update(row['external_ids']);movie['provenance'].append(row)
                for k in row['external_ids'].items():index[k]=idx
                continue
            continue
        idx=len(accepted);mid=idx+1;movie=dict(row,movie_id=mid,provenance=[row]);accepted.append(movie);candidates[key]=idx
        for k in row['external_ids'].items():index[k]=idx
    for movie in accepted:
        facts={canonical(f):f for p in movie['provenance'] for f in p['rating_facts']};movie['rating_facts']=[facts[k] for k in sorted(facts)]
        # Preserve all raw facts; no D02/D03 score winner is invented.
    issues.sort(key=canonical);reviews.sort(key=canonical)
    result={'version':'MI2-M2-v1','mode':meta['mode'],'metadata':meta,'batch_id':digest({'metadata':meta,'records':sorted(digest(x) for x in envelope['records'])}),'movies':accepted,'reviews':reviews,'issues':issues,'quality':{'input':len(envelope['records']),'accepted_movies':len(accepted),'rejected':sum(x['status']=='rejected' for x in issues),'quarantined':sum(x['status']=='quarantined' for x in issues),'duplicates':duplicates,'review':len(reviews),'missing_year':sum(x['release_year'] is None for x in accepted)}}
    result['content_hash']=digest(result);return result

def read_input(path,metadata=None):
    path=pathlib.Path(path)
    if path.suffix.lower()=='.json':return json.loads(path.read_text(encoding='utf-8-sig'))
    if path.suffix.lower()!='.csv' or metadata is None:raise ValueError('CSV requires explicit metadata JSON')
    with path.open(encoding='utf-8-sig',newline='') as f:
        records=[]
        for row in csv.DictReader(f):
            for key in ('external_ids','aliases','genres','people','ratings'):
                if key in row:row[key]=json.loads(row[key]) if row.get(key) else ({} if key=='external_ids' else [])
            records.append(row)
    return {'metadata':metadata,'records':records}

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output-dir',required=True);p.add_argument('--metadata');a=p.parse_args();out=pathlib.Path(a.output_dir).resolve()
    if out.drive.upper()=='C:' or not out.is_dir():raise ValueError('Existing approved non-C output required')
    metadata=json.loads(pathlib.Path(a.metadata).read_text(encoding='utf-8')) if a.metadata else None
    result=build(read_input(a.input,metadata));target=out/'dataset.json';encoded=(canonical(result)+'\n').encode()
    if target.exists():
        if target.read_bytes()!=encoded:raise ValueError('Different existing batch; use a fresh output directory')
    else:
        with tempfile.NamedTemporaryFile(dir=out,delete=False) as f:name=f.name;f.write(encoded);f.flush();os.fsync(f.fileno())
        try:os.link(name,target)
        finally:os.unlink(name)
    print('batch',result['batch_id'],'quality',canonical(result['quality']))
if __name__=='__main__':main()
