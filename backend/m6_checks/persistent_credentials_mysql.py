"""Real isolated MySQL lifecycle regression; new synthetic accounts/keys only."""
import json,secrets,hashlib,os
from sqlalchemy import select,func
from app import create_app
from app.models import User,AuthSession
from assistant.credential_store import CredentialStore,CredentialError
checks=[]
def check(name,value):
    checks.append({'name':name,'passed':bool(value)})
    if not value:raise AssertionError(name)
def main():
    calls=[]
    def no_paid_calls(*args):calls.append(1);raise AssertionError('No provider dispatch allowed')
    def app():return create_app({'TESTING':True,'CREDENTIALS_TEST_PERSISTENCE':True,'M7_TEST_PROVIDER_FACTORY':no_paid_calls,'TMDB_TEST_TRANSPORT':lambda *args:{'images':{}}})
    a=app();origin=a.config['ALLOWED_ORIGINS'][0];store=a.extensions['credential_store']
    def client(application):
        c=application.test_client();c.environ_base['REMOTE_ADDR']='198.19.'+str(secrets.randbelow(250))+'.'+str(secrets.randbelow(250)+1);return c
    def csrf(c):return {'Origin':origin,'X-CSRF-Token':c.get('/api/v1/auth/csrf').json['data']['csrf_token']}
    def account(c):
        name='persist_'+secrets.token_hex(7);password=secrets.token_urlsafe(24)
        check('register_synthetic',c.post('/api/v1/auth/register',json={'username':name,'password':password},headers=csrf(c)).status_code==201)
        check('login_synthetic',c.post('/api/v1/auth/login',json={'username':name,'password':password},headers=csrf(c)).status_code==200)
        user=c.get('/api/v1/auth/me').json['data']['id'];return user,name,password
    def status(c,path):return c.get(path).json['data']
    c=client(a);u,name,password=account(c);other=client(a);other_id,other_name,other_password=account(other)
    key='SYNTHETIC_PERSIST_DEEPSEEK_'+secrets.token_hex(12);token='SYNTHETIC_PERSIST_TMDB_'+secrets.token_hex(12)
    b={'api_key':key,'consent':True,'daily_calls':None}
    check('save_deepseek_encrypted',c.post('/api/v1/assistant/config',json=b,headers=csrf(c)).json['data']['persistent'])
    check('save_tmdb_encrypted',c.post('/api/v1/tmdb/config',json={'access_token':token,'consent':True},headers=csrf(c)).json['data']['persistent'])
    check('other_account_no_configuration',not status(other,'/api/v1/assistant/status')['configured'] and not status(other,'/api/v1/tmdb/status')['configured'])
    with store.engine.connect() as conn:
        rows=conn.execute(select(store.table).where(store.table.c.user_id==u)).mappings().all()
        check('independent_ciphertext_audit',len(rows)==2 and all(key.encode() not in row['ciphertext'] and token.encode() not in row['ciphertext'] for row in rows))
    check('no_key_echo',key not in c.get('/api/v1/assistant/status').get_data(as_text=True) and token not in c.get('/api/v1/tmdb/status').get_data(as_text=True))
    # A new application/vault/cipher instance recovers from SQL + protected master.
    second=app();fresh=client(second)
    for cookie in c._cookies.values():fresh.set_cookie(cookie.key,cookie.value,domain=cookie.domain,path=cookie.path)
    check('new_process_state_restored',status(fresh,'/api/v1/assistant/status')['enabled'] and status(fresh,'/api/v1/tmdb/status')['enabled'])
    wrong=CredentialStore(store.engine,store.auth_engine,os.urandom(32));cookie=next(x for x in c._cookies.values() if x.key==a.config['AUTH_COOKIE_NAME']);binding=hashlib.sha256(cookie.value.encode()).hexdigest()
    try:wrong.load(u,binding,'deepseek');bad=False
    except CredentialError:bad=True
    check('wrong_master_fails_closed',bad)
    # A cloned cookie is the same login session and must be revoked on logout.
    # Establish an actual second login before testing another-device recovery.
    fresh=client(second)
    check('independent_second_login',fresh.post('/api/v1/auth/login',json={'username':name,'password':password},headers=csrf(fresh)).status_code==200)
    check('logout_keeps_database_keys',c.post('/api/v1/auth/logout',json={},headers=csrf(c)).status_code==200)
    check('old_session_cannot_load',store.load(u,binding,'deepseek') is None)
    check('another_session_still_recovers',status(fresh,'/api/v1/assistant/status')['enabled'])
    check('relogin',c.post('/api/v1/auth/login',json={'username':name,'password':password},headers=csrf(c)).status_code==200)
    check('relogin_recovers_both',status(c,'/api/v1/assistant/status')['enabled'] and status(c,'/api/v1/tmdb/status')['enabled'])
    for path in ('/api/v1/assistant/disconnect','/api/v1/tmdb/disconnect'):
        r=c.post(path,json={},headers=csrf(c));check('pause_preserves_saved_'+path.split('/')[3],r.status_code==200 and r.json['data']['configured'] and not r.json['data']['enabled'])
    third=app();again=client(third)
    for cookie in c._cookies.values():again.set_cookie(cookie.key,cookie.value,domain=cookie.domain,path=cookie.path)
    check('pause_survives_restart',not status(again,'/api/v1/assistant/status')['enabled'] and not status(again,'/api/v1/tmdb/status')['enabled'])
    for path in ('/api/v1/assistant/enable','/api/v1/tmdb/enable'):
        check('enable_saved_without_reentry',again.post(path,json={},headers=csrf(again)).json['data']['enabled'])
    otherkey='SYNTHETIC_OTHER_ACCOUNT_KEY_'+secrets.token_hex(8);other.post('/api/v1/assistant/config',json={'api_key':otherkey,'consent':True},headers=csrf(other))
    body={'password':password,'confirmation':name,'understand':True}
    check('close_missing_csrf_rejected',again.post('/api/v1/account/close',json=body).status_code==403)
    check('wrong_password_keeps_keys',again.post('/api/v1/account/close',json=dict(body,password='wrong password'),headers=csrf(again)).status_code==403 and status(again,'/api/v1/tmdb/status')['configured'])
    check('wrong_confirmation_rejected',again.post('/api/v1/account/close',json=dict(body,confirmation='wrong'),headers=csrf(again)).status_code==400)
    r=again.post('/api/v1/account/close',json=body,headers=csrf(again));check('synthetic_account_closed',r.status_code==200 and r.json['data']['credentials_removed'])
    with store.engine.connect() as conn:check('closure_independent_zero_credentials',conn.execute(select(func.count()).select_from(store.table).where(store.table.c.user_id==u)).scalar()==0)
    with store.auth_engine.connect() as conn:
        check('closure_identity_disabled',conn.execute(select(User.status).where(User.id==u)).scalar()=='closed')
        check('closure_all_sessions_revoked',conn.execute(select(func.count()).select_from(AuthSession).where(AuthSession.user_id==u,AuthSession.revoked_at.is_(None))).scalar()==0)
    check('closed_identity_login_rejected',again.post('/api/v1/auth/login',json={'username':name,'password':password},headers=csrf(again)).status_code==401)
    check('other_identity_preserved',status(other,'/api/v1/assistant/status')['enabled'])
    # Close only this runner's second freshly-created identity; retain audit history.
    check('second_synthetic_cleanup',other.post('/api/v1/account/close',json={'password':other_password,'confirmation':other_name,'understand':True},headers=csrf(other)).status_code==200)
    check('zero_model_dispatch',not calls)
    check('frozen_database_ready',a.test_client().get('/api/v1/ready').status_code==200)

if __name__=='__main__':
    try:main();print(json.dumps({'all_pass':True,'checks':checks}))
    except Exception as e:print(json.dumps({'all_pass':False,'checks':checks,'error_type':type(e).__name__}));raise SystemExit(1)
