"""SYNTHETIC demo behavior and deterministic zero-network recommendation."""
import json,math,threading
from datetime import datetime,timezone
USERS=('demo-1','demo-2','demo-3')
KINDS=('rating','favorite','want','watched','not_interested')
class Conflict(ValueError):pass
def validate_event(event,visible,*,source='SYNTHETIC_DEMO'):
    if type(event) is not dict or set(event)-{'event_id','movie_id','kind','action','value','time','source'}:raise ValueError('Unknown event fields')
    eid=event.get('event_id');mid=event.get('movie_id');kind=event.get('kind');action=event.get('action')
    if type(eid) is not str or not 8<=len(eid)<=100 or type(mid) is not int or mid not in visible:raise ValueError('Invalid event or movie')
    if kind not in KINDS or action not in ('set','undo') or event.get('source')!=source:raise ValueError('Invalid event contract')
    try:
        timestamp=datetime.fromisoformat(event.get('time'))
        if timestamp.tzinfo is None:raise ValueError()
    except (TypeError,ValueError):raise ValueError('Timezone-aware event time required') from None
    value=event.get('value')
    if kind=='rating' and action=='set':
        if type(value) not in (int,float) or not math.isfinite(value) or not .5<=value<=5 or value*2!=int(value*2):raise ValueError('Rating .5 to 5 in .5 steps')
    elif value is not None:raise ValueError('Value only for rating set')
    return json.dumps(event,sort_keys=True,separators=(',',':'))

def transition(state,event):
    kind=event['kind'];action=event['action'];value=event.get('value')
    if action=='set':
        if kind in ('rating','favorite','want') and state.get('not_interested'):raise Conflict('Undo not interested before positive actions')
        if kind=='want' and state.get('watched'):raise Conflict('Undo watched before want')
        if kind=='not_interested':
            for k in ('rating','favorite','want'):state.pop(k,None)
        if kind=='watched':state.pop('want',None)
        state[kind]=value if kind=='rating' else True
    else:state.pop(kind,None)

class BehaviorStore:
    def __init__(self):self.lock=threading.RLock();self.states={u:{} for u in USERS};self.events={};self.version=0
    def user(self,user):
        if user not in USERS:raise ValueError('Unknown demo user')
    def snapshot(self,user):
        self.user(user)
        with self.lock:return json.loads(json.dumps(self.states[user]))
    def apply(self,user,event,visible):
        self.user(user)
        signature=validate_event(event,visible)
        eid=event.get('event_id');mid=event.get('movie_id');kind=event.get('kind');action=event.get('action')
        with self.lock:
            if eid in self.events:
                if self.events[eid]!=(user,signature):raise Conflict('Event id conflict')
                return {'duplicate':True,'version':self.version}
            state=self.states[user].setdefault(str(mid),{})
            transition(state,event)
            self.events[eid]=(user,signature);self.version+=1
            return {'duplicate':False,'version':self.version}

def recommend(movies,state,*,limit=12,weights=None,baseline=False,baseline_field='demo_popularity',mode='SYNTHETIC_RECOMMENDATION',cold_start_reason='合成热门冷启动基线',profile_movies=None):
    if type(limit) is not int or not 1<=limit<=50:raise ValueError('Invalid recommendation limit')
    weights={'favorite':3.0,'want':2.0,'rating':1.0} if weights is None else weights
    if set(weights)!={'favorite','want','rating'} or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=10 for v in weights.values()):raise ValueError('Invalid weights')
    active={m['movie_id']:m for m in movies if m.get('catalog_status')=='active'};profile=[]
    profile_catalog=active if profile_movies is None else {m['movie_id']:m for m in profile_movies if m.get('catalog_status')=='active'}
    for key,event in state.items():
        movie=profile_catalog.get(int(key))
        if movie:
            w=weights['favorite']*bool(event.get('favorite'))+weights['want']*bool(event.get('want'))+weights['rating']*(event.get('rating',3)-3 if 'rating' in event else 0)
            if w:profile.append((movie,w))
    scored=[]
    for mid,movie in active.items():
        event=state.get(str(mid),{})
        if event.get('watched') or event.get('not_interested'):continue
        # Synthetic nonpersonal baseline, independent of source rating facts.
        popular=float(movie.get(baseline_field,0));content=0.;reasons=[]
        if not baseline:
            for seed,w in profile:
                g=set(movie.get('genres',[]));sg=set(seed.get('genres',[]));genre=len(g&sg)/max(1,len(g|sg))
                year=max(0,1-abs((movie.get('release_year') or 0)-(seed.get('release_year') or 0))/30) if movie.get('release_year') and seed.get('release_year') else 0
                language=float(bool(movie.get('original_language')) and movie.get('original_language')==seed.get('original_language'))
                similarity=.7*genre+.2*year+.1*language;content+=w*similarity
                if w>0 and genre:reasons.append('与你偏好的类型相似')
                elif w>0 and year>.7:reasons.append('与你偏好的上映年代接近')
                elif w>0 and language:reasons.append('与你偏好的电影语言相同')
            if event.get('want'):content+=.3;reasons.append('来自你的想看清单')
        score=.2*popular+content
        scored.append({'movie_id':mid,'title':movie['title'],'score':round(score,8),'components':{'baseline':round(.2*popular,8),'preference':round(content,8)},'reasons':sorted(set(reasons)) or [cold_start_reason],'genres':movie.get('genres',[])})
    # Greedy diversity with a bounded repeat-genre penalty, deterministic tie break.
    result=[];counts={}
    while scored and len(result)<limit:
        best=min(scored,key=lambda x:(-(x['score']-(0 if baseline else 1.0*sum(counts.get(g,0) for g in x['genres']))),x['movie_id']))
        scored.remove(best);best=dict(best);best['diversity_penalty']=0 if baseline else round(1.0*sum(counts.get(g,0) for g in best['genres']),8)
        for g in best['genres']:counts[g]=counts.get(g,0)+1
        result.append(best)
    return {'items':result,'candidate_count':len(active)-sum(bool(state.get(str(mid),{}).get('watched') or state.get(str(mid),{}).get('not_interested')) for mid in active),'mode':mode,'paid_model_tokens':0}
