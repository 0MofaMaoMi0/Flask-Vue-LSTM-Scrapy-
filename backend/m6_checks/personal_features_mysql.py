"""Owned synthetic accounts only; no paid model, independent row checks."""
import json,uuid,base64,zlib,struct,binascii,os
from concurrent.futures import ThreadPoolExecutor
from assistant.personal_features import FeatureConflict
from sqlalchemy import select,func
from app import create_app
from assistant.personal_features import tables
from catalog.models import CatalogBase

def image(random=False):
 def chunk(k,d):return struct.pack('>I',len(d))+k+d+struct.pack('>I',binascii.crc32(k+d)&0xffffffff)
 raw=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',128,128,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b''.join(b'\0'+os.urandom(512) for _ in range(128)) if random else b'\0'*(128*513)))+chunk(b'IEND',b'')
 return 'data:image/png;base64,'+base64.b64encode(raw).decode()

def main():
 dispatches=[]
 def no_model(*args):dispatches.append(1);raise AssertionError('Provider forbidden')
 app=create_app({'TESTING':True,'CREDENTIALS_TEST_PERSISTENCE':True,'RATE_LIMIT':1000,'CSRF_RATE_LIMIT':1000,'M7_TEST_PROVIDER_FACTORY':no_model});results=[];users=[]
 def check(name,ok):
  results.append(dict(name=name,passed=bool(ok)))
  if not ok:raise AssertionError(name)
 class Client:
  def __init__(self):self.c=app.test_client();self.token=''
  def call(self,path,data=None,status=200,csrf=True):
   headers={'Origin':'http://127.0.0.1:5174'}
   if data is not None:
    if not self.token:self.token=self.call('/api/v1/auth/csrf')['csrf_token']
    headers['X-CSRF-Token']=self.token if csrf else ''
   r=self.c.get(path,headers=headers) if data is None else self.c.post(path,json=data,headers=headers)
   if r.status_code!=status:raise AssertionError('HTTP_'+str(r.status_code)+'_'+path)
   return r.json.get('data')
  def login(self,name,pwd):self.token='';r=self.call('/api/v1/auth/login',dict(username=name,password=pwd));self.token='';return r
 try:
  for i in range(2):
   import secrets
   c=Client();name='m9personal_'+uuid.uuid4().hex[:12];password=secrets.token_urlsafe(30)
   uid=c.call('/api/v1/auth/register',dict(username=name,password=password),201)['id'];c.login(name,password);users.append((c,name,password,uid))
  a,name,password,uid=users[0];b,_,_,other=users[1]
  Client().call('/api/v1/account/profile',status=401);check('anonymous_profile_denied',True)
  check('initial_profile_empty',a.call('/api/v1/account/profile')['avatar'] is None)
  a.call('/api/v1/account/profile',dict(avatar=image(),version=0),403,csrf=False);check('csrf_rejected',True)
  a.call('/api/v1/account/profile',dict(avatar='data:image/svg+xml;base64,PHN2Zz4=',version=0),400);check('svg_rejected',True)
  saved=a.call('/api/v1/account/profile',dict(avatar=image(),version=0));check('avatar_saved',saved['avatar']==image() and saved['version']==1)
  noise=image(True);check('large_browser_png_exceeds_login_limit',len(noise)>8192)
  check('large_avatar_api_accepts_request',b.call('/api/v1/account/profile',dict(avatar=noise,version=0))['version']==1)
  reset_b=b.call('/api/v1/account/profile',dict(avatar=None,version=1));check('other_avatar_reset',reset_b['avatar'] is None)
  check('other_account_no_avatar',b.call('/api/v1/account/profile')['avatar'] is None)
  a.call('/api/v1/account/profile',dict(avatar=image(),version=0),409);check('profile_conflict_rejected',True)
  with app.extensions['auth_engine'].connect() as c:mid=c.scalar(select(CatalogBase.metadata.tables['movies'].c.movie_id).where(CatalogBase.metadata.tables['movies'].c.catalog_status=='active').limit(1))
  note=a.call('/api/v1/account/movie-notes',dict(movie_id=mid,note='仅测试账户可见的合成备注',version=0));check('note_saved',note['version']==1)
  check('other_account_notes_empty',not b.call('/api/v1/account/movie-notes')['items'])
  def compete(i):
   try:app.extensions['personal_features'].note(other,mid,'concurrent_'+str(i),0);return 'saved'
   except FeatureConflict:return 'conflict'
  with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(compete,range(2)))
  check('concurrent_note_one_winner',sorted(outcomes)==['conflict','saved'])
  check('concurrent_note_version_one',b.call('/api/v1/account/movie-notes')['items'][str(mid)]['version']==1)
  a.call('/api/v1/account/movie-notes',dict(movie_id=mid,note='stale',version=0),409);check('note_conflict_rejected',True)
  a.call('/api/v1/account/movie-notes',dict(movie_id=mid,note='x'*201,version=1),400);check('note_limit_enforced',True)
  a.call('/api/v1/account/movie-notes',dict(movie_id=mid,note='spoof',version=1,user_id=other),400);check('user_id_spoof_rejected',True)
  rid='personal_'+uuid.uuid4().hex;r=a.call('/api/v1/assistant/query',dict(message='推荐一部电影',request_id=rid,stream=False));check('local_answer_zero_model_calls',r['source']=='LOCAL' and r['cost']['provider_calls']==0)
  a.call('/api/v1/account/assistant-feedback',dict(request_id=rid,reaction='up'));a.call('/api/v1/account/assistant-feedback',dict(request_id=rid,reaction='up'));check('feedback_readback',a.call('/api/v1/account/assistant-feedback?request_id='+rid)['reaction']=='up')
  b.call('/api/v1/account/assistant-feedback',dict(request_id=rid,reaction='down'),404);check('feedback_other_user_denied',True)
  a.call('/api/v1/account/assistant-feedback',dict(request_id=rid,reaction='READ'),400);check('internal_feedback_sentinel_rejected',True)
  a.call('/api/v1/account/assistant-feedback',dict(request_id='unknown_123456',reaction='up'),404);check('invented_answer_denied',True)
  t=tables()
  with app.extensions['m7_engine'].connect() as c:
   count=c.scalar(select(func.count()).select_from(t['assistant_feedback']).where(t['assistant_feedback'].c.user_id==uid));raw=c.scalar(select(t['user_profiles'].c.avatar_png).where(t['user_profiles'].c.user_id==uid))
  check('independent_feedback_unique_row',count==1);check('independent_avatar_png_bytes',raw.startswith(b'\x89PNG'))
  a.call('/api/v1/auth/logout',{});a.token='';a.login(name,password)
  check('profile_survives_relogin',a.call('/api/v1/account/profile')['avatar']==image());check('notes_survive_relogin',a.call('/api/v1/account/movie-notes')['items'][str(mid)]['version']==1)
  check('feedback_survives_relogin',a.call('/api/v1/account/assistant-feedback?request_id='+rid)['reaction']=='up')
  a.call('/api/v1/account/assistant-feedback',dict(request_id=rid,reaction=None));check('feedback_undo',a.call('/api/v1/account/assistant-feedback?request_id='+rid)['reaction'] is None)
  reset=a.call('/api/v1/account/profile',dict(avatar=None,version=1));check('avatar_reset',reset['avatar'] is None)
  a.call('/api/v1/account/movie-notes',dict(movie_id=mid,note='',version=1));check('note_clear_keeps_version',a.call('/api/v1/account/movie-notes')['items'][str(mid)]=={'note':'','version':2})
  fresh=create_app({'TESTING':True,'CREDENTIALS_TEST_PERSISTENCE':True});check('new_app_reads_durable_note',fresh.extensions['personal_features'].notes(uid)[str(mid)]['version']==2)
  check('zero_actual_model_dispatches',not dispatches)
 except Exception as e:
  results.append(dict(name='execution',passed=False,error_type=type(e).__name__))
 finally:
  for client,name,password,uid in users:
   try:
    client.call('/api/v1/account/close',dict(password=password,confirmation=name,understand=True));check('close_own_synthetic_account',True)
    with app.extensions['m7_engine'].connect() as c:remaining=sum(c.scalar(select(func.count()).select_from(table).where(table.c.user_id==uid)) for table in tables().values())
    check('closure_erases_own_personal_rows',remaining==0)
    client.call('/api/v1/account/profile',status=401);check('closed_session_cannot_read_profile',True)
    if uid==users[0][3] and len(users)>1:check('closing_first_retains_second_notes',str(mid) in app.extensions['personal_features'].notes(users[1][3]))
   except Exception as e:results.append(dict(name='cleanup',passed=False,error_type=type(e).__name__))
  print(json.dumps(dict(all_pass=bool(results) and all(x['passed'] for x in results),checks=results,model_dispatches=len(dispatches))))
if __name__=='__main__':
 try:main()
 except Exception:raise SystemExit(1)
