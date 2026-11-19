"""Encrypted account-level provider credentials in the isolated M7 auxiliary DB."""
import json,os,secrets,stat,threading
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager
from sqlalchemy import MetaData,Table,Column,BigInteger,String,LargeBinary,Integer,DateTime,select,insert,update,delete
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from app.models import User,AuthSession

PROVIDERS={'deepseek','tmdb'}
class CredentialError(RuntimeError):pass
class CredentialAccessDenied(PermissionError):pass

def credential_table():
    return Table('provider_credentials',MetaData(),Column('user_id',BigInteger,primary_key=True),Column('provider',String(24),primary_key=True),Column('key_version',Integer,nullable=False),Column('nonce',LargeBinary(12),nullable=False),Column('ciphertext',LargeBinary,nullable=False),Column('generation',String(32),nullable=False),Column('verification',String(32),nullable=False),Column('created_at',DateTime,nullable=False),Column('updated_at',DateTime,nullable=False))

def read_master(path):
    p=Path(path)
    if not p.is_absolute() or p.is_symlink():raise CredentialError('Protected master key required')
    s=p.stat()
    if not stat.S_ISREG(s.st_mode) or s.st_size!=32:raise CredentialError('Invalid master key file')
    if os.name!='nt' and (stat.S_IMODE(s.st_mode)&0o077 or s.st_uid not in (0,os.geteuid())):raise CredentialError('Unsafe master key permissions')
    return p.read_bytes()

class CredentialStore:
    def __init__(self,engine,auth_engine,master):
        if type(master) is not bytes or len(master)!=32:raise CredentialError('Master key unavailable')
        self.engine=engine;self.auth_engine=auth_engine;self.table=credential_table();self._cipher=AESGCM(master)
        # DDL is an explicit installer action, never an application startup side effect.
        with engine.connect() as c:c.execute(select(self.table).limit(0))
    def _authorized(self,c,user,binding,*,lock=False):
        if type(user) is not int or user<1 or type(binding) is not str:return False
        stmt=select(User.status).where(User.id==user)
        if lock:stmt=stmt.with_for_update()
        if c.execute(stmt).scalar_one_or_none()!='active':return False
        now=datetime.utcnow()
        stmt=select(AuthSession.id).where(AuthSession.user_id==user,AuthSession.sid_hash==binding,AuthSession.revoked_at.is_(None),AuthSession.idle_expires_at>now,AuthSession.absolute_expires_at>now)
        if lock:stmt=stmt.with_for_update()
        return c.execute(stmt).scalar_one_or_none() is not None
    @contextmanager
    def _owner(self,user,binding):
        # Lock the existing account row before all writes, also used by closure.
        with self.auth_engine.begin() as c:
            if not self._authorized(c,user,binding,lock=True):raise CredentialAccessDenied('Authentication required')
            yield c
    @staticmethod
    def _aad(user,provider):return f'movieinsight:provider_credentials:v1:{user}:{provider}'.encode()
    def _encode(self,user,provider,payload):
        if provider not in PROVIDERS or type(payload) is not dict:raise ValueError('Provider configuration')
        raw=json.dumps(payload,separators=(',',':')).encode()
        if len(raw)>16384:raise ValueError('Configuration too long')
        nonce=os.urandom(12)
        return nonce,self._cipher.encrypt(nonce,raw,self._aad(user,provider))
    def _decode(self,row,user,provider):
        if row['key_version']!=1:raise CredentialError('Unsupported encryption version')
        try:
            value=json.loads(self._cipher.decrypt(row['nonce'],row['ciphertext'],self._aad(user,provider)))
            if type(value) is not dict:raise ValueError()
            return dict(payload=value,generation=row['generation'],verification=row['verification'])
        except (InvalidTag,ValueError,TypeError):raise CredentialError('Credential decryption unavailable') from None
    def load(self,user,binding,provider):
        if provider not in PROVIDERS:raise ValueError('Provider')
        with self.auth_engine.connect() as auth:
            if not self._authorized(auth,user,binding):return None
            with self.engine.connect() as c:row=c.execute(select(self.table).where(self.table.c.user_id==user,self.table.c.provider==provider)).mappings().first()
            return self._decode(row,user,provider) if row else None
    def save(self,user,binding,provider,payload):
        nonce,ciphertext=self._encode(user,provider,payload);generation=secrets.token_hex(16);now=datetime.utcnow();t=self.table
        with self._owner(user,binding),self.engine.begin() as c:
            exists=c.execute(select(t.c.user_id).where(t.c.user_id==user,t.c.provider==provider).with_for_update()).first()
            values=dict(key_version=1,nonce=nonce,ciphertext=ciphertext,generation=generation,verification='UNVERIFIED',updated_at=now)
            if exists:c.execute(update(t).where(t.c.user_id==user,t.c.provider==provider).values(**values))
            else:c.execute(insert(t).values(user_id=user,provider=provider,created_at=now,**values))
        return generation
    def enabled(self,user,binding,provider,value):
        if type(value) is not bool or provider not in PROVIDERS:raise ValueError('Enabled')
        t=self.table
        with self._owner(user,binding),self.engine.begin() as c:
            row=c.execute(select(t).where(t.c.user_id==user,t.c.provider==provider).with_for_update()).mappings().first()
            if not row:raise ValueError('No saved configuration')
            record=self._decode(row,user,provider);payload=record['payload'];payload['enabled']=value
            nonce,ciphertext=self._encode(user,provider,payload)
            c.execute(update(t).where(t.c.user_id==user,t.c.provider==provider).values(nonce=nonce,ciphertext=ciphertext,generation=secrets.token_hex(16),updated_at=datetime.utcnow()))
    def verified(self,user,binding,provider,generation,value):
        if provider not in PROVIDERS or value not in {'UNVERIFIED','VERIFIED','MODEL_UNAVAILABLE','KEY_REJECTED','CHECK_FAILED'}:raise ValueError('Verification')
        try:
            with self._owner(user,binding),self.engine.begin() as c:
                c.execute(update(self.table).where(self.table.c.user_id==user,self.table.c.provider==provider,self.table.c.generation==generation).values(verification=value,updated_at=datetime.utcnow()))
        except CredentialAccessDenied:pass
    def close_account(self,user,binding,password,confirmation):
        from app.auth import ph
        from argon2.exceptions import VerifyMismatchError,VerificationError
        if type(password) is not str or not 1<=len(password)<=512 or type(confirmation) is not str:raise ValueError('Confirmation')
        with self._owner(user,binding) as auth:
            row=auth.execute(select(User).where(User.id==user)).mappings().one()
            if confirmation!=row['username']:raise ValueError('Confirmation')
            try:ph.verify(row['password_hash'],password)
            except (VerifyMismatchError,VerificationError):raise CredentialAccessDenied('Current password rejected') from None
            # Erase first. If the later auth commit fails, the account remains usable
            # with no saved keys; it is never closed with surviving live credentials.
            with self.engine.begin() as c:
                c.execute(delete(self.table).where(self.table.c.user_id==user))
                for table in getattr(self,'personal_tables',{}).values():c.execute(delete(table).where(table.c.user_id==user))
            auth.execute(update(User).where(User.id==user).values(status='closed',password_hash=ph.hash(secrets.token_urlsafe(32)),updated_at=datetime.utcnow()))
            auth.execute(update(AuthSession).where(AuthSession.user_id==user,AuthSession.revoked_at.is_(None)).values(revoked_at=datetime.utcnow()))

def install(app):
    path=app.config.get('CREDENTIALS_MASTER_FILE') or os.environ.get('MI2_CREDENTIALS_MASTER_FILE','')
    if app.config.get('TESTING') and not app.config.get('CREDENTIALS_TEST_PERSISTENCE'):return
    if not path:raise CredentialError('Persistent credentials require the protected installer')
    from .store import connect_store
    engine=connect_store(app.config['M7_DATABASE_URL'],app.config['M6_SERVER_UUID'])
    app.extensions['credential_store']=CredentialStore(engine,app.extensions['auth_engine'],read_master(path))
    app.extensions['m7_engine']=engine
