"""Real desktop HTTP flow using fresh synthetic users; no provider keys/calls."""
import argparse,json,secrets,uuid,time,urllib.request,urllib.error,urllib.parse,http.cookiejar
from datetime import datetime,timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

class Client:
 def __init__(self,base):
  self.base=base;self.token='';self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
 def call(self,path,data=None,expected=200):
  if data is not None and not self.token:self.token=self.call('/api/v1/auth/csrf')['csrf_token']
  headers={'Origin':self.base,'Accept':'application/json'}
  if data is not None:headers.update({'Content-Type':'application/json','X-CSRF-Token':self.token})
  req=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
  try:r=self.opener.open(req,timeout=110)
  except urllib.error.HTTPError as e:r=e
  status=r.status;body=json.load(r)
  if status!=expected:raise AssertionError(f'HTTP {status} expected {expected} at {path.split("?")[0]} code={body.get("code")}')
  return body.get('data')
 def login(self,name,password):
  self.token='';r=self.call('/api/v1/auth/login',dict(username=name,password=password));self.token='';return r
 def event(self,mid,kind,action='set',value=None,eid=None,expected=200):
  return self.call('/api/catalog/behavior',dict(event_id=eid or str(uuid.uuid4()),movie_id=mid,kind=kind,action=action,value=value,time=datetime.now(timezone.utc).isoformat(),source='USER_ACTION'),expected)

def run(base,out):
 checks=[];accounts=[]
 def check(name,ok,**details):
  checks.append(dict(name=name,passed=bool(ok),**details))
  if not ok:raise AssertionError(name)
 def state(c,mid):return c.call('/api/catalog/behavior')['state'].get(str(mid),{})
 try:
  ready=False
  for _ in range(40):
   try:
    with urllib.request.urlopen(base+'/api/v1/ready',timeout=3) as response:ready=response.status==200
   except Exception:pass
   if ready:break
   time.sleep(.25)
  check('service_ready',ready)
  anonymous=Client(base);anonymous.call('/api/admin/imports',expected=401);anonymous.call('/api/catalog/recommendations',expected=401);check('anonymous_access_denied',True)
  for _ in range(2):
   name='m9flow_'+secrets.token_hex(8);password=secrets.token_urlsafe(30);c=Client(base)
   c.call('/api/v1/auth/register',dict(username=name,password=password),201);c.login(name,password);accounts.append((c,name,password))
  a,name,password=accounts[0];b=accounts[1][0]
  check('synthetic_accounts_created',len(accounts)==2)
  a.call('/api/admin/imports',expected=403);a.call('/api/admin/imports/preview',dict(kind='tmdb_catalog',tmdb_ids=[687163]),403);check('normal_user_admin_import_denied',True)
  status=a.call('/api/v1/assistant/status');check('synthetic_has_no_model_key',not status.get('configured') and not status.get('enabled'))
  movie=a.call('/api/catalog/lookup?source=tmdb&external_id=687163');mid=movie['movie_id'];detail=a.call('/api/catalog/movies/'+str(mid));check('imported_identity_matches_details',detail['movie_id']==mid, movie_id=mid)
  search=a.call('/api/catalog/movies?keyword='+urllib.parse.quote(detail['title']));entry=next(m for m in search['items'] if m['movie_id']==mid)
  check('imported_movie_visible_in_search',bool(entry['poster']))
  check('list_batch_genres_ratings_match_detail',entry['genres']==detail['genres'] and entry['ratings']==detail['ratings'])
  loaded=[];offset=0
  while True:
   page=a.call('/api/catalog/movies?sort=id&limit=100&offset='+str(offset));loaded.extend(page['items'])
   if len(page['items'])<page['limit']:break
   offset+=page['limit']
  check('full_catalog_pages_include_recent_import',len({m['movie_id'] for m in loaded})==len(loaded) and next(i for i,m in enumerate(loaded) if m['movie_id']==mid)>=100,catalog_movies=len(loaded))
  for art in detail['assets']:
   if art['asset_type'] not in ('poster','backdrop'):continue
   with urllib.request.urlopen(base+art['url'],timeout=40) as r:data=r.read();check('actual_'+art['asset_type']+'_loaded',r.status==200 and data.startswith((b'\xff\xd8',b'\x89PNG',b'RIFF')))
  check('new_user_behavior_empty',not a.call('/api/catalog/behavior')['state'])
  a.event(mid,'favorite');a.event(mid,'want');a.event(mid,'rating',value=4.5)
  s=state(a,mid);check('favorite_want_rating_independent',s.get('favorite') and s.get('want') and s.get('rating')==4.5 and not s.get('watched'))
  stamp=datetime.now(timezone.utc).isoformat();payload=dict(event_id=str(uuid.uuid4()),movie_id=mid,kind='favorite',action='set',value=None,time=stamp,source='USER_ACTION')
  first=a.call('/api/catalog/behavior',payload);second=a.call('/api/catalog/behavior',payload)
  check('behavior_duplicate_idempotent',second['duplicate'] and first['version']==second['version'])
  a.call('/api/catalog/behavior',dict(payload,kind='want'),409);check('behavior_id_conflict_denied',True)
  a.call('/api/catalog/behavior',dict(payload,user_id=12345),400);check('caller_cannot_select_other_user',True)
  check('other_user_behavior_isolated',not b.call('/api/catalog/behavior')['state'])
  channels=a.call('/api/catalog/recommendation-channels')['items']
  for channel in channels:
   board=a.call('/api/catalog/recommendations?channel='+channel['id']+'&limit=12')
   check('channel_'+channel['id'],isinstance(board.get('items'),list))
   for m in board['items']:
    if m.get('in_catalog'):check('card_id_'+channel['id']+'_'+str(m['movie_id']),a.call('/api/catalog/movies/'+str(m['movie_id']))['movie_id']==m['movie_id'])
  watch=a.call('/api/catalog/recommendations?channel=watchlist&limit=50')['items'];check('watchlist_contains_want',any(m['movie_id']==mid for m in watch))
  distant=a.call('/api/catalog/lookup?source=tmdb&external_id=13')['movie_id']
  a.event(distant,'want')
  filtered=a.call('/api/catalog/movies?state=want&sort=id&limit=1&offset=0')['items']
  check('personal_filter_before_pagination',len(filtered)==1 and filtered[0]['movie_id']==min(mid,distant))
  check('personal_filter_other_user_empty',not b.call('/api/catalog/movies?state=want&limit=1')['items'])
  a.event(distant,'want','undo')
  a.event(mid,'watched');s=state(a,mid);check('watched_clears_want_preserves_rating_favorite',s.get('watched') and s.get('watched_at') and not s.get('want') and s.get('favorite') and s.get('rating')==4.5)
  a.event(mid,'want',expected=409);check('want_after_watched_rejected',True)
  check('watched_not_in_watchlist',all(m['movie_id']!=mid for m in a.call('/api/catalog/recommendations?channel=watchlist&limit=50')['items']))
  a.event(mid,'not_interested');s=state(a,mid);check('negative_feedback_clears_positive_only',s.get('not_interested') and s.get('watched') and not any(k in s for k in ('rating','favorite','want')))
  check('negative_feedback_excludes_personal',all(m['movie_id']!=mid for m in a.call('/api/catalog/recommendations?channel=personal&limit=50')['items']))
  for kind in ('not_interested','watched'):a.event(mid,kind,'undo')
  a.event(mid,'want');a2=Client(base);a2.login(name,password)
  with ThreadPoolExecutor(max_workers=2) as pool:
   tasks=[pool.submit(a.event,mid,'rating',value=3.5),pool.submit(a2.event,mid,'favorite')]
   for t in tasks:t.result()
  s=state(a,mid);check('concurrent_sessions_preserve_fields',s.get('want') and s.get('favorite') and s.get('rating')==3.5)
  rid='desktop_'+uuid.uuid4().hex
  message=dict(message='从我的想看清单推荐电影',request_id=rid,stream=False)
  answer=a.call('/api/v1/assistant/query',message)
  check('ai_fallback_zero_provider_calls',answer.get('source')=='LOCAL' and answer.get('cost',{}).get('provider_calls',0)==0)
  check('ai_new_movie_card_has_live_poster',next(m for m in answer.get('items',[]) if m['movie_id']==mid).get('poster')==entry['poster'])
  check('ai_watchlist_reads_only_own_wants',{m['movie_id'] for m in answer.get('items',[])}=={mid})
  for item in answer.get('items',[]):check('ai_card_links_actual_id',a.call('/api/catalog/movies/'+str(item['movie_id']))['movie_id']==item['movie_id'])
  replay=a.call('/api/v1/assistant/query',message);check('ai_same_request_replayed',replay.get('replayed') and replay.get('new_provider_calls')==0)
  b.call('/api/v1/assistant/requests/'+rid,expected=404);check('ai_request_cross_user_denied',True)
  before=state(a,mid);a.call('/api/v1/auth/logout',{});a.token='';a.call('/api/catalog/behavior',expected=401)
  check('other_session_survives_logout',state(a2,mid)==before)
  a.login(name,password);check('behavior_survives_relogin',state(a,mid)==before)
  check('other_user_still_unmodified',not b.call('/api/catalog/behavior')['state'])
 except Exception as e:
  checks.append(dict(name='flow_failure',passed=False,error_type=type(e).__name__,detail=str(e) if isinstance(e,AssertionError) else 'No exception payload recorded'))
 finally:
  for client,name,password in accounts:
   try:
    client.call('/api/v1/account/close',dict(password=password,confirmation=name,understand=True));check('close_only_new_synthetic_account',True)
   except Exception as e:checks.append(dict(name='synthetic_cleanup_failed',passed=False,error_type=type(e).__name__))
  report=dict(all_pass=all(r['passed'] for r in checks),checks=checks,paid_api_tests=False)
  out.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8');print(json.dumps(dict(all_pass=report['all_pass'],checks=len(checks),failed=[r for r in checks if not r['passed']]),ensure_ascii=True));return report['all_pass']
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--base',default='http://127.0.0.1:5174');p.add_argument('--output',required=True);args=p.parse_args()
 if args.base!='http://127.0.0.1:5174':raise SystemExit('Dedicated local proxy required')
 raise SystemExit(0 if run(args.base,Path(args.output)) else 1)
