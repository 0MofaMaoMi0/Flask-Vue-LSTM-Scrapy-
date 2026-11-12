"""Synthetic M3 behavior, recommendation evaluation and API checks."""
import pathlib,sys,json,random,math,time,copy
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from catalog.recommendation import BehaviorStore,Conflict,recommend
from catalog.recommendation_api import create_recommendation_blueprint
from catalog.data_pipeline import normalize
from flask import Flask

def main(output):
    out=pathlib.Path(output).resolve()
    if not out.is_dir() or out.drive.upper()=='C:':raise ValueError('Non-C output required')
    rng=random.Random(20261105);movies=[{'movie_id':i,'title':'SYNTHETIC '+str(i),'catalog_status':'active','genres':[i%4],'release_year':2000+i%20,'original_language':'zh' if i%2 else 'en','demo_popularity':rng.random()} for i in range(1,81)]
    results=[]
    def check(name,ok):
        results.append({'case':name,'pass':bool(ok)})
        if not ok:raise ValueError(name)
    def rejected(fn):
        try:fn()
        except ValueError:return True
        return False
    store=BehaviorStore();visible={m['movie_id'] for m in movies}
    counter=0
    def event(mid=1,kind='favorite',action='set',value=None):
        nonlocal counter
        counter+=1;return {'event_id':'SYNTHETIC-'+str(counter),'movie_id':mid,'kind':kind,'action':action,'value':value,'time':'2026-11-05T00:00:00+00:00','source':'SYNTHETIC_DEMO'}
    e=event();store.apply('demo-1',e,visible);check('event idempotent',store.apply('demo-1',e,visible)['duplicate'])
    check('cross user event id conflict',rejected(lambda:store.apply('demo-2',e,visible)));check('cross user state isolated',store.snapshot('demo-2')=={})
    store.apply('demo-1',event(kind='favorite',action='undo'),visible);check('undo favorite',not store.snapshot('demo-1')['1'].get('favorite'))
    store.apply('demo-1',event(kind='rating',value=4.5),visible);check('rating update',store.snapshot('demo-1')['1']['rating']==4.5)
    store.apply('demo-1',event(kind='rating',value=2),visible);check('rating replace',store.snapshot('demo-1')['1']['rating']==2)
    for bad in [0,5.5,True,float('nan'),2.2]:check('invalid rating '+str(bad),rejected(lambda bad=bad:store.apply('demo-1',event(kind='rating',value=bad),visible)))
    store.apply('demo-1',event(kind='want'),visible);store.apply('demo-1',event(kind='watched'),visible);check('watched clears want',not store.snapshot('demo-1')['1'].get('want'));check('want watched conflict',rejected(lambda:store.apply('demo-1',event(kind='want'),visible)))
    store.apply('demo-1',event(mid=2,kind='not_interested'),visible);check('positive uninterested conflict',rejected(lambda:store.apply('demo-1',event(mid=2),visible)))
    check('invalid hidden movie',rejected(lambda:store.apply('demo-1',event(mid=999),visible)));check('invalid user',rejected(lambda:store.snapshot('other')))
    check('spoofed user field',rejected(lambda:store.apply('demo-1',dict(event(),user='demo-2'),visible)))
    state=store.snapshot('demo-1');items=recommend(movies,state)['items'];check('watched negative excluded',not {1,2}&{x['movie_id'] for x in items});check('stable output',recommend(movies,state)==recommend(movies,state));check('zero tokens',recommend(movies,state)['paid_model_tokens']==0)
    check('no duplicate recommendations',len({x['movie_id'] for x in items})==len(items));check('no candidates',recommend(movies,{str(i):{'not_interested':True} for i in visible})['items']==[]);check('cold start',bool(recommend(movies,{})['items']));check('sparse user',bool(recommend(movies,{'3':{'want':True}})['items']))
    check('hidden candidate excluded',999 not in [x['movie_id'] for x in recommend(movies+[dict(movies[0],movie_id=999,catalog_status='hidden')],{})['items']]);check('invalid weights',rejected(lambda:recommend(movies,{},weights={'favorite':99})))
    # Fixed disjoint per-user train and held-out positives; no test interactions in profile.
    evaluation={};split=[]
    for method in ('hybrid','popular'):
        recall=[];hit=[];ndcg=[];diversity=[];covered=set();candidate_counts=[];start=time.perf_counter()
        for user in range(40):
            local=random.Random(20261105+user);preferred=user%4;pool=[m['movie_id'] for m in movies if m['genres']==[preferred]];chosen=local.sample(pool,4);train=chosen[:2];test=set(chosen[2:]);negative=next(m['movie_id'] for m in movies if m['genres']!=[preferred])
            profile={str(mid):{'favorite':True,'rating':4.5,'watched':True} for mid in train};profile[str(negative)]={'not_interested':True}
            if method=='hybrid':split.append({'user':'SYNTHETIC-'+str(user),'train':train,'heldout':sorted(test),'negative':negative})
            rec=recommend(movies,profile,limit=10,baseline=method=='popular');ids=[x['movie_id'] for x in rec['items']];covered.update(ids);candidate_counts.append(rec['candidate_count']);hits=[int(mid in test) for mid in ids];recall.append(sum(hits)/len(test));hit.append(float(any(hits)));dcg=sum(v/math.log2(i+2) for i,v in enumerate(hits));ideal=sum(1/math.log2(i+2) for i in range(min(10,len(test))));ndcg.append(dcg/ideal)
            pairs=[float(set(rec['items'][i]['genres']).isdisjoint(rec['items'][j]['genres'])) for i in range(len(ids)) for j in range(i)];diversity.append(sum(pairs)/len(pairs) if pairs else 0)
        evaluation[method]={'Recall@10':sum(recall)/40,'HitRate@10':sum(hit)/40,'NDCG@10':sum(ndcg)/40,'coverage':len(covered)/len(movies),'intra_list_genre_diversity':sum(diversity)/40,'mean_candidates':sum(candidate_counts)/40,'seconds':time.perf_counter()-start}
    check('train heldout disjoint',all(not set(x['train'])&set(x['heldout']) for x in split))
    app=Flask('SYNTHETIC-M3');app.register_blueprint(create_recommendation_blueprint(lambda:movies));client=app.test_client();headers={'X-Demo-User':'demo-1'}
    e=event(mid=3);check('HTTP behavior',client.post('/api/catalog/behavior',headers=headers,json=e).status_code==200);check('HTTP repeat event',client.post('/api/catalog/behavior',headers=headers,json=e).json['data']['duplicate']);check('HTTP recommendation',client.get('/api/catalog/recommendations',headers=headers).status_code==200)
    check('HTTP no user',client.get('/api/catalog/recommendations').status_code==400);check('HTTP cross body spoof',client.post('/api/catalog/behavior',headers=headers,json=dict(event(),user='demo-2')).status_code==400)
    check('HTTP wrong origin',client.post('/api/catalog/behavior',headers=dict(headers,Origin='https://invalid.example'),json=event()).status_code==400)
    # A newly rebuilt store deterministically replays the same event stream.
    replay=BehaviorStore();stream=[event(mid=7,kind='want'),event(mid=7,kind='watched'),event(mid=8,kind='favorite')]
    for item in stream:replay.apply('demo-1',item,visible)
    rebuilt=BehaviorStore()
    for item in stream:rebuilt.apply('demo-1',item,visible)
    check('replay equivalent state',replay.snapshot('demo-1')==rebuilt.snapshot('demo-1'))
    check('conflicting same event payload',rejected(lambda:replay.apply('demo-1',dict(stream[0],kind='favorite'),visible)))
    replay.apply('demo-1',event(mid=7,kind='watched',action='undo'),visible)
    check('undo watched',not replay.snapshot('demo-1')['7'].get('watched'))
    replay.apply('demo-1',event(mid=8,kind='rating',value=5),visible);replay.apply('demo-1',event(mid=8,kind='rating',action='undo'),visible)
    check('undo rating',not replay.snapshot('demo-1')['8'].get('rating'))
    check('out of range recommendation limit',rejected(lambda:recommend(movies,{},limit=51)))
    check('empty weights rejected',rejected(lambda:recommend(movies,{},weights={})))
    check('CSV blank optional regression',normalize({'source_record_id':'synthetic','external_ids':{'tmdb':'synthetic'},'title':'SYNTHETIC','media_type':'','legacy_table':''},{'source':'tmdb','license_status':'allowed'})['legacy'] is None)
    (out/'recommendation-results.json').write_text(json.dumps({'checks':results,'seed':20261105,'synthetic':True,'paid_model_tokens':0},ensure_ascii=False,indent=2),encoding='utf-8');(out/'algorithm-evaluation.json').write_text(json.dumps({'version':'M3-SYNTHETIC-v1','seed':20261105,'users':40,'movies':80,'K':10,'protocol':'2 train positives + 2 disjoint heldout positives; one explicit negative; same candidates and split for both methods','results':evaluation,'splits':split,'online_effect':'NOT PROVEN'},indent=2),encoding='utf-8');print('M3 checks',len(results),'PASS',evaluation)
if __name__=='__main__':main(sys.argv[1])
