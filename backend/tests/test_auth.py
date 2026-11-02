import os
import uuid
from datetime import datetime,timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select,update,text
from app import create_app
from app.models import User,AuthSession
from app.auth import hashed,ph,normalized
ORIGIN='http://127.0.0.1:5174'
PASSWORD='test-only-long-password'
@pytest.fixture
def app():
    return create_app({'TESTING':True,'DATABASE_URL':os.environ['MI2_TEST_DATABASE_URL']})
@pytest.fixture
def client(app):
    c=app.test_client();c.environ_base['REMOTE_ADDR']='test-'+uuid.uuid4().hex
    return c

def token(c):return c.get('/api/v1/auth/csrf').get_json()['data']['csrf_token']
def post(c,action,data,t=None,origin=ORIGIN):
    return c.post('/api/v1/auth/'+action,json=data,headers={'Origin':origin,'X-CSRF-Token':t or token(c)})
def account(c):
    name='u'+uuid.uuid4().hex[:20]
    assert post(c,'register',{'username':name,'password':PASSWORD}).status_code==201
    return name
def login(c,name):return post(c,'login',{'username':name,'password':PASSWORD})

def test_contract_and_hash(client,app):
    name=account(client)
    assert client.get('/api/v1/auth/me').status_code==401
    with app.extensions['auth_db']() as db:
        u=db.scalar(select(User).where(User.username_normalized==name))
        assert u.password_hash.startswith('$argon2id$') and ph.verify(u.password_hash,PASSWORD)
    assert set(client.get('/api/v1/health').get_json())=={'code','message','data'}

def test_registration_conflict_normalization(client):
    name=account(client)
    assert post(client,'register',{'username':name.upper(),'password':PASSWORD}).status_code==409
    fullwidth=''.join(chr(ord(x)+0xFEE0) if 33<=ord(x)<=126 else x for x in name)
    assert post(client,'register',{'username':fullwidth,'password':PASSWORD}).status_code==409

@pytest.mark.parametrize('data',[{}, {'username':'ab','password':PASSWORD},{'username':'abc','password':'short'},{'username':'abc','password':'x'*129},{'username':'abc','password':PASSWORD,'uid':2},{'username':'<script>','password':PASSWORD}])
def test_invalid_inputs(client,data):assert post(client,'register',data).status_code==400

@pytest.mark.parametrize('origin',['https://evil.example',''])
def test_origin(client,origin):
    assert post(client,'register',{'username':'abc','password':PASSWORD},origin=origin).status_code==403

def test_csrf_missing_wrong_and_content_type(client):
    t=token(client)
    assert client.post('/api/v1/auth/login',json={},headers={'Origin':ORIGIN}).status_code==403
    assert post(client,'login',{},t='wrong').status_code==403
    assert client.post('/api/v1/auth/login',data='x',headers={'Origin':ORIGIN,'X-CSRF-Token':t}).status_code==400

def test_uniform_wrong_credentials(client):
    name=account(client)
    a=post(client,'login',{'username':name,'password':PASSWORD+'wrong'})
    b=post(client,'login',{'username':'absent'+uuid.uuid4().hex[:10],'password':PASSWORD})
    assert a.status_code==b.status_code==401 and a.get_json()==b.get_json()

def test_rotation_logout_replay(client,app):
    name=account(client);old=client.get_cookie('mi2_sid',path='/api/v1').value;old_csrf=token(client)
    response=post(client,'login',{'username':name,'password':PASSWORD},t=old_csrf)
    assert response.status_code==200
    new=client.get_cookie('mi2_sid',path='/api/v1').value
    assert old!=new
    cookie=response.headers['Set-Cookie'];assert 'HttpOnly' in cookie and 'SameSite=Lax' in cookie and 'Path=/api/v1' in cookie
    other=app.test_client();other.set_cookie('mi2_sid',old,path='/api/v1')
    assert other.get('/api/v1/auth/me').status_code==401
    assert post(client,'logout',{},t=old_csrf).status_code==403
    assert client.get('/api/v1/auth/me').status_code==200
    assert post(client,'logout',{}).status_code==200
    other.set_cookie('mi2_sid',new,path='/api/v1')
    assert other.get('/api/v1/auth/me').status_code==401

@pytest.mark.parametrize('field',['idle_expires_at','absolute_expires_at','revoked_at'])
def test_expiry_revocation(client,app,field):
    name=account(client);assert login(client,name).status_code==200
    sid=client.get_cookie('mi2_sid',path='/api/v1').value
    with app.extensions['auth_db'].begin() as db:
        db.execute(update(AuthSession).where(AuthSession.sid_hash==hashed(sid)).values(**{field:datetime.utcnow()-timedelta(seconds=1)}))
    assert client.get('/api/v1/auth/me').status_code==401

def test_disabled_user(client,app):
    name=account(client);assert login(client,name).status_code==200
    with app.extensions['auth_db'].begin() as db:db.execute(update(User).where(User.username_normalized==name).values(status='disabled'))
    assert client.get('/api/v1/auth/me').status_code==401

def test_user_isolation(client,app):
    a=account(client);b=account(client);assert login(client,a).status_code==200
    first=client.get('/api/v1/auth/me').get_json()['data']
    other=app.test_client();other.environ_base['REMOTE_ADDR']='test-'+uuid.uuid4().hex;assert login(other,b).status_code==200
    second=other.get('/api/v1/auth/me').get_json()['data']
    assert first['id']!=second['id']
    assert client.get('/api/v1/auth/me?uid='+str(second['id'])).get_json()['data']==first

def test_rate_limit_shared(app):
    ip='limit-'+uuid.uuid4().hex;clients=[app.test_client(),create_app({'TESTING':True,'DATABASE_URL':os.environ['MI2_TEST_DATABASE_URL']}).test_client()]
    t=[]
    for c in clients:c.environ_base['REMOTE_ADDR']=ip;t.append(token(c))
    for i in range(11):
        response=post(clients[i%2],'login',{'username':'missinguser','password':PASSWORD},t=t[i%2])
    assert response.status_code==429 and int(response.headers['Retry-After'])>0

def test_csrf_creation_rate(client,app):
    app.config['CSRF_RATE_LIMIT']=2
    assert [client.get('/api/v1/auth/csrf').status_code for _ in range(3)]==[200,200,429]

def test_runtime_denied_legacy(app):
    with app.extensions['auth_engine'].connect() as db:
        with pytest.raises(Exception):db.execute(text('SELECT COUNT(*) FROM flask_douban_comment.tb_user'))

def test_wrong_database_refused():
    with pytest.raises(RuntimeError):create_app({'TESTING':True,'DATABASE_URL':'mysql+pymysql://x:x@127.0.0.1/flask_douban_comment'})

def test_rehash_support(client,app):
    from argon2 import PasswordHasher
    name=account(client)
    with app.extensions['auth_db'].begin() as db:db.execute(update(User).where(User.username_normalized==name).values(password_hash=PasswordHasher(time_cost=1,memory_cost=8192,parallelism=1).hash(PASSWORD)))
    assert login(client,name).status_code==200
    with app.extensions['auth_db']() as db:assert not ph.check_needs_rehash(db.scalar(select(User.password_hash).where(User.username_normalized==name)))

def test_concurrent_registration(app):
    name='race'+uuid.uuid4().hex[:16]
    def register(_):
        c=app.test_client();c.environ_base['REMOTE_ADDR']='race-'+uuid.uuid4().hex
        return post(c,'register',{'username':name,'password':PASSWORD}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(register,range(2)))==[201,409]


def test_csrf_and_sid_only_hashes_persisted(client,app):
    t=token(client);sid=client.get_cookie('mi2_sid',path='/api/v1').value
    with app.extensions['auth_db']() as db:
        row=db.scalar(select(AuthSession).where(AuthSession.sid_hash==hashed(sid)))
        assert row and row.sid_hash!=sid and row.csrf_token_hash!=t and row.csrf_token_hash==hashed(t)

def test_unicode_and_space_password(client):
    name='用户'+uuid.uuid4().hex[:12];password=' unicode 密码 with spaces '
    assert post(client,'register',{'username':name,'password':password}).status_code==201
    assert post(client,'login',{'username':name,'password':password}).status_code==200

def test_runtime_denied_restore_and_ddl(app):
    with app.extensions['auth_engine'].connect() as db:
        with pytest.raises(Exception):db.execute(text('SELECT COUNT(*) FROM mi_restore_20261102_221832.tb_user'))
        with pytest.raises(Exception):db.execute(text('CREATE TABLE mi2_auth_test.unapproved_ddl_test (id INT)'))

def test_wrong_method_and_large_body(client):
    assert client.get('/api/v1/auth/login').status_code==405
    t=token(client)
    assert client.post('/api/v1/auth/login',data='x'*9000,content_type='application/json',headers={'Origin':ORIGIN,'X-CSRF-Token':t}).status_code==413


def test_throttle_window_rollover(client,app):
    import time
    from app.models import Throttle
    assert client.get('/api/v1/auth/csrf').status_code==200
    key=hashed('csrf:ip:'+client.environ_base['REMOTE_ADDR'])
    with app.extensions['auth_db'].begin() as db:db.execute(update(Throttle).where(Throttle.key_hash==key).values(window=int(time.time())//app.config['RATE_WINDOW']-1,count=9999))
    assert client.get('/api/v1/auth/csrf').status_code==200
    with app.extensions['auth_db']() as db:assert db.get(Throttle,key).count==1


@pytest.mark.parametrize('length,expected',[(5,400),(6,201),(128,201),(129,400)])
def test_password_length_boundaries(client,length,expected):
    name='length'+uuid.uuid4().hex[:16]
    password='x'*length
    assert post(client,'register',{'username':name,'password':password}).status_code==expected
    if expected==201:assert post(client,'login',{'username':name,'password':password}).status_code==200
