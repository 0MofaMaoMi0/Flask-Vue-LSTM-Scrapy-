"""Isolated encrypted credential lifecycle contracts; synthetic identities only."""
import os,time
from datetime import datetime,timedelta
from unittest.mock import patch
import pytest
from sqlalchemy import create_engine,event,select,update,func
from app.models import Base,User,AuthSession
from app.auth import ph
from assistant.credential_store import CredentialStore,CredentialError,CredentialAccessDenied,credential_table,read_master
from assistant.byok_vault import Vault
from catalog.tmdb_connection import TMDBConnection,TMDBError

KEY='SYNTHETIC_DEEPSEEK_NOT_A_REAL_KEY_12345'
TOKEN='SYNTHETIC_TMDB_NOT_A_REAL_TOKEN_98765'
PASSWORD='SYNTHETIC_ACCOUNT_PASSWORD_123456'
A='a'*64;B='b'*64;OTHER='c'*64

@pytest.fixture
def store():
    auth=create_engine('sqlite://',hide_parameters=True)
    @event.listens_for(auth,'connect')
    def collation(c,record):c.create_collation('utf8mb4_bin',lambda a,b:(a>b)-(a<b))
    Base.metadata.create_all(auth);db=create_engine('sqlite://',hide_parameters=True);credential_table().metadata.create_all(db);now=datetime.utcnow()
    with auth.begin() as c:
        c.execute(User.__table__.insert(),[dict(id=i,username='synthetic'+str(i),username_normalized='synthetic'+str(i),password_hash=ph.hash(PASSWORD),status='active',created_at=now,updated_at=now) for i in [1,2]])
        c.execute(AuthSession.__table__.insert(),[dict(id=i,user_id=uid,sid_hash=binding,csrf_token_hash='s'*64,created_at=now,last_seen_at=now,idle_expires_at=now+timedelta(hours=1),absolute_expires_at=now+timedelta(hours=2)) for i,uid,binding in [(1,1,A),(2,1,B),(3,2,OTHER)]])
    return CredentialStore(db,auth,os.urandom(32))

def put(vault,user=1,binding=A,key=KEY):return vault.put(user,binding,dict(api_key=key,consent=True))

def test_ciphertext_only_and_restart_restores_saved_keys(store,tmp_path):
    vault=Vault(store=store);put(vault)
    tmdb=TMDBConnection(tmp_path,store=store);tmdb.put(1,A,dict(access_token=TOKEN,consent=True))
    fresh=Vault(store=store);status=fresh.status(1,B)
    assert status['persistent'] and status['expires_in_seconds'] is None and status['configured']
    assert fresh.key(1,B,fresh.config(1,B)['generation'])==KEY
    assert TMDBConnection(tmp_path,store=store).status(1,B)['configured']
    with store.engine.connect() as c:
        rows=c.execute(select(store.table)).mappings().all()
    assert len(rows)==2 and all(KEY.encode() not in r['ciphertext'] and TOKEN.encode() not in r['ciphertext'] for r in rows)
    assert set(store.table.c.keys()).isdisjoint({'api_key','access_token','password','plaintext'})

def test_logout_is_memory_eviction_not_database_deletion(store):
    v=Vault(store=store);put(v);v.disconnect_binding(A)
    with store.auth_engine.begin() as c:c.execute(update(AuthSession).where(AuthSession.sid_hash==A).values(revoked_at=datetime.utcnow()))
    assert not v.status(1,A)['configured']
    assert Vault(store=store).status(1,B)['enabled']
    with store.engine.connect() as c:assert c.execute(select(func.count()).select_from(store.table)).scalar()==1

def test_pause_survives_restart_and_enable_needs_no_reentry(store,tmp_path):
    v=Vault(store=store);put(v);generation=v.config(1,A)['generation'];v.disconnect(1,A)
    fresh=Vault(store=store);assert fresh.status(1,B)['configured'] and not fresh.status(1,B)['enabled']
    with pytest.raises(ValueError):v.key(1,A,generation)
    assert fresh.enable(1,B)['enabled']
    tmdb=TMDBConnection(tmp_path,store=store);tmdb.put(1,A,dict(access_token=TOKEN,consent=True));tmdb.disconnect(1,A)
    fresh_tmdb=TMDBConnection(tmp_path,store=store)
    assert fresh_tmdb.status(1,B)['configured'] and not fresh_tmdb.status(1,B)['enabled']
    with pytest.raises(TMDBError):fresh_tmdb._get(1,B,'configuration')
    assert fresh_tmdb.enable(1,B)['enabled']

def test_expired_memory_rehydrates_and_other_users_do_not(store):
    v=Vault(store=store,ttl=1);put(v)
    with patch('assistant.byok_vault.time.monotonic',return_value=time.monotonic()+5):assert v.status(1,A)['configured']
    assert not Vault(store=store).status(2,OTHER)['configured']
    assert store.load(1,OTHER,'deepseek') is None and store.load(2,A,'deepseek') is None
    with pytest.raises(CredentialAccessDenied):store.save(1,OTHER,'deepseek',dict(api_key=KEY))

def test_replacement_revokes_old_generations_everywhere(store):
    v=Vault(store=store);put(v);old=v.config(1,A)['generation'];put(Vault(store=store),binding=B,key=KEY+'_NEW')
    with pytest.raises(ValueError):v.key(1,A,old)
    assert v.key(1,A,v.config(1,A)['generation'])==KEY+'_NEW'
    v.verified(1,A,old,'KEY_REJECTED')
    assert v.status(1,A)['verification']=='UNVERIFIED'

def test_ciphertext_bound_to_user_and_provider(store):
    put(Vault(store=store));put(Vault(store=store),2,OTHER)
    t=store.table
    with store.engine.begin() as c:
        row=c.execute(select(t).where(t.c.user_id==1)).mappings().one()
        c.execute(update(t).where(t.c.user_id==2).values(nonce=row['nonce'],ciphertext=row['ciphertext']))
    with pytest.raises(CredentialError):store.load(2,OTHER,'deepseek')

def test_wrong_master_fails_without_overwriting_ciphertext(store):
    put(Vault(store=store))
    wrong=CredentialStore(store.engine,store.auth_engine,os.urandom(32))
    with pytest.raises(CredentialError):wrong.load(1,A,'deepseek')
    assert store.load(1,A,'deepseek')['payload']['api_key']==KEY

def test_closure_requires_password_confirmation_and_only_erases_owner(store,tmp_path):
    v=Vault(store=store);put(v);put(v,2,OTHER)
    tmdb=TMDBConnection(tmp_path,store=store);tmdb.put(1,A,dict(access_token=TOKEN,consent=True))
    with pytest.raises(CredentialAccessDenied):store.close_account(1,A,'wrong password','synthetic1')
    with pytest.raises(ValueError):store.close_account(1,A,PASSWORD,'synthetic2')
    assert v.status(1,A)['configured']
    store.close_account(1,A,PASSWORD,'synthetic1')
    assert not v.status(1,A)['configured'] and not tmdb.status(1,B)['configured']
    assert Vault(store=store).status(2,OTHER)['configured']
    with store.engine.connect() as c:assert c.execute(select(func.count()).select_from(store.table).where(store.table.c.user_id==1)).scalar()==0
    with store.auth_engine.connect() as c:
        assert c.execute(select(User.status).where(User.id==1)).scalar()=='closed'
        assert c.execute(select(func.count()).select_from(AuthSession).where(AuthSession.user_id==1,AuthSession.revoked_at.is_(None))).scalar()==0
    with pytest.raises(CredentialAccessDenied):put(v)

def test_master_file_is_stable_and_never_automatically_regenerated(tmp_path):
    path=tmp_path/'master.key';original=os.urandom(32);path.write_bytes(original);path.chmod(0o600)
    assert read_master(path)==original and read_master(path)==original
    path.write_bytes(b'invalid')
    with pytest.raises(CredentialError):read_master(path)
    assert path.read_bytes()==b'invalid'
