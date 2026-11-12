"""Real isolated MySQL checks; synthetic accounts/ledger records always labelled."""
import secrets,unittest
from datetime import datetime
from sqlalchemy import insert,select,text

def run(app,check):
 from .test_metrics import MetricsTest
 result=unittest.TestResult();unittest.defaultTestLoader.loadTestsFromTestCase(MetricsTest).run(result)
 check('m8_offline_calculators',result.wasSuccessful())
 origin=app.config['ALLOWED_ORIGINS'][0]
 def account():
  c=app.test_client();c.environ_base['REMOTE_ADDR']='198.19.'+str(secrets.randbelow(250))+'.'+str(secrets.randbelow(250)+1)
  h={'Origin':origin,'X-CSRF-Token':c.get('/api/v1/auth/csrf').json['data']['csrf_token']};name='m8synthetic_'+secrets.token_hex(5);pw=secrets.token_urlsafe(24)
  check('m8_register_synthetic',c.post('/api/v1/auth/register',json={'username':name,'password':pw},headers=h).status_code==201)
  check('m8_login',c.post('/api/v1/auth/login',json={'username':name,'password':pw},headers=h).status_code==200)
  h['X-CSRF-Token']=c.get('/api/v1/auth/csrf').json['data']['csrf_token'];return c,h,c.get('/api/v1/auth/me').json['data']['id']
 a,headers,uid=account();b,_,bid=account();base='/api/v1/insights/'
 for route in ['catalog/summary','me/behavior','me/ai-usage','recommendation/evaluation']:
  response=a.get(base+route);check('m8_real_mysql_'+route,response.status_code==200)
  for m in response.json['data']['metrics']:check('m8_envelope_'+m['metric_id'],{'metric_id','status','source','scope','window','sample_size','as_of','definition_version','value'}<=m.keys())
  check('m8_unauthorized_'+route,app.test_client().get(base+route).status_code==401)
 check('m8_identity_override_rejected',a.get(base+'me/behavior?user_id='+str(bid)).status_code==400)
 check('m8_identity_override_ai_rejected',a.get(base+'me/ai-usage?user_id='+str(bid)).status_code==400)
 check('m8_invalid_dimension',a.get(base+'catalog/distributions?dimension=sql').status_code==400)
 for q in ['from=bad','from=2020-01-01&to=2026-01-01','from=2026-11-07&to=2026-11-07','from=2026-01-01&from=2026-02-01']:
  check('m8_bad_window_'+q,a.get(base+'me/behavior?'+q).status_code==400)
 catalog=a.get(base+'catalog/summary').json['data']['metrics'];count=next(m['value'] for m in catalog if m['metric_id']=='catalog.active_movie_count')
 check('m8_real_catalog_small_sample',count==2)
 evaluations=a.get(base+'recommendation/evaluation').json['data']['metrics'];check('m8_no_fake_accuracy',all(m['value'] is None and m['status']=='INSUFFICIENT_DATA' for m in evaluations))
 movies=a.get('/api/catalog/movies?limit=6&offset=0&sort=id').json['data']['items'];mid=movies[0]['movie_id'];eid=secrets.token_hex(12)
 event={'event_id':eid,'movie_id':mid,'kind':'favorite','action':'set','time':'2026-11-07T00:00:00Z','source':'USER_ACTION'}
 check('m8_behavior_set',a.post('/api/catalog/behavior',json=event,headers=headers).status_code==200)
 check('m8_behavior_idempotent',a.post('/api/catalog/behavior',json=event,headers=headers).json['data']['duplicate'])
 def value(client,key):return next(m['value'] for m in client.get(base+'me/behavior').json['data']['metrics'] if m['metric_id']==key)
 check('m8_current_state_not_event_count',value(a,'behavior.favorite')==1)
 check('m8_behavior_cross_user',value(b,'behavior.favorite')==0)
 usage=app.extensions['m7_ledger'].md.tables['usage']
 with app.extensions['m7_engine'].begin() as c:
  c.execute(insert(usage),[dict(id=secrets.token_hex(32),user_id=int(uid),model='M8_SYNTHETIC',status='DISPATCHED',tokens=None,created_at=datetime.utcnow().replace(microsecond=0)),dict(id=secrets.token_hex(32),user_id=int(uid),model='M8_SYNTHETIC',status='SUCCEEDED',tokens={'kind':'M8_SYNTHETIC','paid_request':False,'input_tokens':100,'output_tokens':10,'cached_input_tokens':40},created_at=datetime.utcnow().replace(microsecond=0))])
 data=a.get(base+'me/ai-usage').json['data']['metrics'];u=data[0]['value'];check('m8_unknown_usage',u['unknown_calls']==1 and u['known_tokens']['input_tokens']==100 and u['known_tokens']['cached_input_tokens']==40 and u['amount'] is None)
 other=b.get(base+'me/ai-usage').json['data']['metrics'][0];check('m8_ai_cross_user',other['sample_size']==0 and other['status']=='NO_DATA')
 check('m8_no_sensitive_payload',not any(k in str(data) for k in ['api_key','fingerprint','auth_binding','request_body']))
 check('m8_no_admin_route',a.get(base+'admin/users').status_code==404)
 # Parameterized EXPLAIN, read-only; no index/DDL change.
 events=app.extensions['m6_behavior'].tables['events']
 with app.extensions['auth_engine'].connect() as c:
  plan=c.execute(text('EXPLAIN SELECT user_id FROM '+str(events)+' WHERE user_id=:u'),{'u':int(uid)}).mappings().all()
 check('m8_event_user_index',bool(plan) and any(r.get('key') for r in plan))


 # Exercise database unavailability without stopping the real shared instance.
 from sqlalchemy.exc import OperationalError
 class FailedEngine:
  def connect(self):raise OperationalError('synthetic unavailable',{},Exception())
 original=app.extensions['m7_engine'];app.extensions['m7_engine']=FailedEngine()
 try:check('m8_database_unavailable_503',a.get(base+'me/ai-usage').status_code==503)
 finally:app.extensions['m7_engine']=original
 check('m8_empty_time_window',a.get(base+'me/ai-usage?from=2000-01-01&to=2000-01-02').json['data']['metrics'][0]['status']=='NO_DATA')
 import time
 latencies=[]
 for route in ['catalog/summary','me/behavior','me/ai-usage']:
  tick=time.perf_counter();response=b.get(base+route);latencies.append(round((time.perf_counter()-tick)*1000,2));check('m8_query_latency_'+route,response.status_code==200 and latencies[-1]<2000)
 check('m8_read_latency_ms_'+str(latencies),True)
 from analytics.replay import main as replay
 check('m8_demo_replay_deterministic',replay()==replay() and replay()['scope']=='DEMO')
