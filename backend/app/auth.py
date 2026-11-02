import hashlib
import secrets
import time
import unicodedata
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from flask import Blueprint, request, current_app, jsonify
from sqlalchemy import select, case
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.exc import IntegrityError
from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError, VerificationError
from .models import User, AuthSession, Throttle

bp = Blueprint('auth', __name__)
ph = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID)
DUMMY_HASH = ph.hash(secrets.token_urlsafe(32))
COOKIE='mi2_sid'
def hashed(value): return hashlib.sha256(value.encode('utf-8')).hexdigest()
def reply(code=0, message='ok', data=None, status=200):
    return jsonify(code=code, message=message, data=data), status

def normalized(value):
    if not isinstance(value,str): raise ValueError()
    name=unicodedata.normalize('NFKC',value).strip()
    if not 3 <= len(name) <= 32 or not all(c.isalnum() or c in '_-' for c in name): raise ValueError()
    return name, name.casefold()

def limiter(action, account=None):
    now=int(time.time()); period=current_app.config['RATE_WINDOW']; window=now//period
    keys=[(action+':ip:'+ (request.remote_addr or 'unknown'),current_app.config['CSRF_RATE_LIMIT'] if action=='csrf' else current_app.config['RATE_LIMIT'])]
    if account is not None: keys.append((action+':account:'+account,current_app.config['RATE_LIMIT']))
    limited=False
    # DB upsert + row lock makes the counter shared across workers and atomic.
    with current_app.extensions['auth_db'].begin() as db:
        for key,limit in keys:
            key=hashed(key)
            stmt=insert(Throttle).values(key_hash=key,window=window,count=1,expires_at=datetime.utcfromtimestamp((window+1)*period))
            stmt=stmt.on_duplicate_key_update([('count',case((Throttle.window==window,Throttle.count+1),else_=1)),('window',window),('expires_at',stmt.inserted.expires_at)])
            db.execute(stmt)
            count=db.scalar(select(Throttle.count).where(Throttle.key_hash==key).with_for_update())
            limited |= count>limit
    if limited:
        response,status=reply(42910,'too many requests',status=429)
        response.headers['Retry-After']=str(period-now%period)
        return response,status

def session_row(db, lock=False):
    sid=request.cookies.get(COOKIE,'')
    if not 32<=len(sid)<=128:return None
    stmt=select(AuthSession).where(AuthSession.sid_hash==hashed(sid))
    if lock:stmt=stmt.with_for_update()
    row=db.scalar(stmt); now=datetime.utcnow()
    if row is None or row.revoked_at or row.idle_expires_at<=now or row.absolute_expires_at<=now:return None
    if row.user_id is not None:
        user=db.get(User,row.user_id)
        if not user or user.status!='active':return None
    row.last_seen_at=now
    row.idle_expires_at=min(now+timedelta(seconds=current_app.config['IDLE_SECONDS']),row.absolute_expires_at)
    return row

def new_session(db,user_id=None):
    sid=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32);now=datetime.utcnow()
    row=AuthSession(sid_hash=hashed(sid),user_id=user_id,csrf_token_hash=hashed(csrf),created_at=now,last_seen_at=now,
        idle_expires_at=now+timedelta(seconds=current_app.config['IDLE_SECONDS']),absolute_expires_at=now+timedelta(seconds=current_app.config['ABSOLUTE_SECONDS']))
    db.add(row);return sid,csrf

def set_cookie(response,sid):
    response.set_cookie(COOKIE,sid,httponly=True,secure=current_app.config['COOKIE_SECURE'],samesite='Lax',path='/api/v1',max_age=current_app.config['ABSOLUTE_SECONDS'])

def source_ok():
    origin=request.headers.get('Origin')
    if not origin:
        ref=request.headers.get('Referer','');u=urlsplit(ref)
        origin=f'{u.scheme}://{u.netloc}' if u.scheme and u.netloc else ''
    return origin in current_app.config['ALLOWED_ORIGINS']

def csrf_ok(row):
    token=request.headers.get('X-CSRF-Token','')
    return row and source_ok() and len(token)<=128 and secrets.compare_digest(row.csrf_token_hash,hashed(token))

@bp.get('/csrf')
def csrf():
    limited=limiter('csrf')
    if limited:return limited
    with current_app.extensions['auth_db'].begin() as db:
        row=session_row(db,True)
        if row:
            sid=None;token=secrets.token_urlsafe(32);row.csrf_token_hash=hashed(token)
        else:sid,token=new_session(db)
    response,status=reply(data={'csrf_token':token})
    if sid:set_cookie(response,sid)
    return response,status

@bp.get('/me')
def me():
    with current_app.extensions['auth_db'].begin() as db:
        row=session_row(db)
        if not row or row.user_id is None:return reply(40110,'unauthenticated',status=401)
        user=db.get(User,row.user_id)
        return reply(data={'id':user.id,'username':user.username})

@bp.post('/register')
@bp.post('/login')
def credentials():
    action=request.path.rsplit('/',1)[-1]
    with current_app.extensions['auth_db'].begin() as db:
        row=session_row(db,True)
        if not csrf_ok(row):return reply(40310,'request verification failed',status=403)
        if row.user_id is not None:return reply(40010,'already authenticated',status=400)
        if not request.is_json:return reply(40010,'JSON required',status=400)
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or set(data)!={'username','password'}:return reply(40010,'invalid fields',status=400)
        try:name,key=normalized(data['username'])
        except ValueError:return reply(40010,'invalid username',status=400)
        password=data['password']
        if not isinstance(password,str) or not 6<=len(password)<=128:return reply(40010,'password must contain 6 to 128 characters',status=400)
        limited=limiter(action,key)
        if limited:return limited
        user=db.scalar(select(User).where(User.username_normalized==key))
        if action=='register':
            if user:return reply(40910,'registration conflict',status=409)
            user=User(username=name,username_normalized=key,password_hash=ph.hash(password))
            try:
                with db.begin_nested():db.add(user);db.flush()
            except IntegrityError:return reply(40910,'registration conflict',status=409)
            return reply(message='registered',data={'id':user.id,'username':user.username},status=201)
        try:valid=ph.verify(user.password_hash if user else DUMMY_HASH,password)
        except (VerifyMismatchError,VerificationError):valid=False
        if not valid or not user or user.status!='active':return reply(40111,'invalid username or password',status=401)
        if ph.check_needs_rehash(user.password_hash):user.password_hash=ph.hash(password)
        row.revoked_at=datetime.utcnow()
        sid,_=new_session(db,user.id)
        result={'user':{'id':user.id,'username':user.username}}
    response,status=reply(data=result);set_cookie(response,sid);return response,status

@bp.post('/logout')
def logout():
    with current_app.extensions['auth_db'].begin() as db:
        row=session_row(db,True)
        if not row or row.user_id is None:return reply(40110,'unauthenticated',status=401)
        if not csrf_ok(row):return reply(40310,'request verification failed',status=403)
        if not request.is_json or request.get_json(silent=True)!={}:return reply(40010,'empty JSON object required',status=400)
        row.revoked_at=datetime.utcnow()
    response,status=reply(message='logged out');response.delete_cookie(COOKIE,path='/api/v1',httponly=True,samesite='Lax',secure=current_app.config['COOKIE_SECURE']);return response,status
