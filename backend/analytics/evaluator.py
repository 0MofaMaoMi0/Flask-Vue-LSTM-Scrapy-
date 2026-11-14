"""Deterministic calculators, independent of persistence and production identities."""
import math,hashlib,json
from catalog.recommendation import recommend

def ranking_metrics(predictions, truth, candidates, k):
    if type(k) is not int or k<1: raise ValueError('positive K required')
    if not candidates or len(candidates)!=len(set(candidates)):raise ValueError('unique candidates required')
    universe=set(candidates);precision=[];recall=[];ndcg=[];seen=set()
    for user,relevant in truth.items():
        relevant=set(relevant)
        if not relevant or not relevant<=universe:raise ValueError('eligible ground truth required')
        ranked=predictions[user][:k]
        if len(set(ranked))!=len(ranked) or not set(ranked)<=universe:raise ValueError('invalid ranking')
        hits=len(set(ranked)&relevant);precision.append(hits/k);recall.append(hits/len(relevant));seen.update(ranked)
        dcg=sum(1/math.log2(i+2) for i,item in enumerate(ranked) if item in relevant)
        ideal=sum(1/math.log2(i+2) for i in range(min(k,len(relevant))))
        ndcg.append(dcg/ideal)
    if not precision:raise ValueError('test users required')
    return dict(precision=sum(precision)/len(precision),recall=sum(recall)/len(recall),ndcg=sum(ndcg)/len(ndcg),coverage=len(seen)/len(universe))

def temporal_split(interactions):
    grouped={}
    for user,movie,stamp in sorted(set(interactions),key=lambda x:(x[2],str(x[0]),x[1])):
        grouped.setdefault(user,{})[movie]=stamp
    train={};truth={}
    for user,items in grouped.items():
        ordered=sorted(items,key=lambda m:(items[m],m))
        if len(ordered)>=2:train[user]=ordered[:-1];truth[user]={ordered[-1]}
    return train,truth

def experiment(movies,interactions,k=2,source='DEMO'):
    if source not in ('DEMO','OFFLINE_RESEARCH'):raise ValueError('research isolation required')
    train,truth=temporal_split(interactions);candidates=[m['movie_id'] for m in movies];counts={m:0 for m in candidates}
    for mids in train.values():
        for mid in mids:counts[mid]+=1
    records=[dict(m,training_popularity=counts[m['movie_id']]) for m in movies];outputs={}
    for name,baseline in [('Popularity',True),('MovieInsightRules',False)]:
        predictions={}
        for user,mids in train.items():
            state={str(mid):{'favorite':True,'watched':True} for mid in mids}
            result=recommend(records,state,limit=k,baseline=baseline,baseline_field='training_popularity',mode=source)
            predictions[user]=[x['movie_id'] for x in result['items']]
        outputs[name]=ranking_metrics(predictions,truth,candidates,k)
    signature=hashlib.sha256(json.dumps([movies,interactions,k],sort_keys=True,default=str).encode()).hexdigest()
    return dict(scope=source,k=k,split='per-user latest distinct item; training-only popularity; seen excluded',seed=0,users=len(truth),candidate_count=len(candidates),signature=signature,algorithms=outputs)
