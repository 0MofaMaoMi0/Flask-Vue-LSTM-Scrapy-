"""Test-only isolation entry. Default operation is read-only preflight.

--execute requires a separate later authorization; do not use during BLOCK-02B.
No production module or database schema is changed by this module.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import unicodedata
from urllib.parse import urlsplit

from flask.testing import FlaskClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

_ACTIVE = None
TABLES = {'auth_users': 'id', 'auth_sessions': 'id', 'auth_throttles': 'key_hash'}

class IsolationError(RuntimeError):
    pass

def digest(value):
    encoded=json.dumps(value,ensure_ascii=False,default=str,separators=(',',':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()

def keyhash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

def normalized(value):
    if not isinstance(value,str): raise ValueError('invalid test username')
    name=unicodedata.normalize('NFKC',value).strip()
    if not 3<=len(name)<=32 or not all(c.isalnum() or c in '_-' for c in name):raise ValueError('invalid test username')
    return name.casefold()

def validate_disposable(url, config):
    import re
    if config.get('TESTING') is not True or config.get('DISPOSABLE_SANDBOX') is not True:
        raise RuntimeError('Explicit sandbox mode required')
    schema=config.get('DISPOSABLE_SCHEMA')
    host=config.get('DISPOSABLE_HOST');port=config.get('DISPOSABLE_PORT')
    user=config.get('DISPOSABLE_USER')
    if not isinstance(schema,str) or re.fullmatch(r'mi2_auth_r2_[a-z0-9]{8,24}',schema) is None:
        raise RuntimeError('Approved full sandbox name required')
    if host not in ('localhost','127.0.0.1','::1') or type(port) is not int or not 1<=port<=65535 or not isinstance(user,str) or not user:
        raise RuntimeError('Approved endpoint and user required')
    if url.drivername!='mysql+pymysql' or url.database!=schema or url.host!=host or url.port!=port or url.username!=user or not url.password or url.query:
        raise RuntimeError('Sandbox contract mismatch')
    return 'CANDIDATE'

def disposable_config():
    try:
        port=int(os.environ.get('MI2_DISPOSABLE_PORT',''))
    except ValueError:
        raise IsolationError('Explicit sandbox endpoint required') from None
    return {'TESTING':True,'DISPOSABLE_SANDBOX':os.environ.get('MI2_DISPOSABLE_MODE')=='true',
            'DISPOSABLE_SCHEMA':os.environ.get('MI2_DISPOSABLE_SCHEMA'),
            'DISPOSABLE_HOST':os.environ.get('MI2_DISPOSABLE_HOST'),
            'DISPOSABLE_PORT':port,'DISPOSABLE_USER':os.environ.get('MI2_DISPOSABLE_USER')}

def configured_test_url():
    # Explicit process injection only. Never inspect a credential file or dev URL.
    value=os.environ.get('MI2_TEST_DATABASE_URL')
    if not value:
        raise IsolationError('Explicit test configuration required')
    try:
        url=make_url(value)
        validate_disposable(url,disposable_config())
    except Exception:
        raise IsolationError('Invalid disposable test contract') from None
    return url


class Policy:
    def __init__(self):
        self.url=configured_test_url()
        self.engine=create_engine(self.url,hide_parameters=True,pool_pre_ping=True)
        self.run_id=secrets.token_hex(16)
        self.counter=0
        self.lock=threading.RLock()
        self.names=set();self.ips=set();self.claimed_names=set();self.claimed_keys=set()
        try:
            self.verify_permissions()
            self.verify_effective_identity()
            self.original=self.snapshot()
        except BaseException:
            try:
                self.engine.dispose()
            except Exception:
                pass
            raise
        self.request_checks=0

    def verify_permissions(self):
        # Fail closed for roles/global DML/unknown grants: no DDL probe used here.
        with self.engine.connect() as c:grants=[row[0] for row in c.exec_driver_sql('SHOW GRANTS')]
        if not grants:raise IsolationError('Empty grant result')
        account = r"(?:'(?:''|[^'])*'|`(?:``|[^`])*`)"
        pattern = r"GRANT (.+?) ON (.+?) TO " + account + r"@" + account
        observed={t:set() for t in TABLES}
        for grant in grants:
            # Reject before USAGE can short-circuit, including whitespace/case variants.
            if re.search(r'\bWITH\s+GRANT\s+OPTION\b',grant,re.IGNORECASE):
                raise IsolationError('Grant option rejected')
            m=re.fullmatch(pattern,grant,flags=re.IGNORECASE)
            if not m:raise IsolationError('Unresolved grant, role or suffix')
            privileges={x.strip().upper() for x in m[1].split(',')}
            target=m[2].replace('`','')
            if privileges=={'USAGE'}:
                if target!='*.*':raise IsolationError('Unexpected USAGE target')
                continue
            if target not in {self.url.database+"."+t for t in TABLES} or not privileges<={'SELECT','INSERT','UPDATE'}:
                raise IsolationError('DDL/cross-schema/unknown privilege risk')
            observed[target.split('.',1)[1]].update(privileges)
        if any(p != {'SELECT','INSERT','UPDATE'} for p in observed.values()):
            raise IsolationError('Incomplete test table permissions')
        self.permissions_checked=True

    def verify_effective_identity(self):
        # No flag or SHOW GRANTS parser can establish the missing external proof.
        # Fail closed before history reads until host/transport, effective identity,
        # roles/proxy inheritance and independent audit receive implementation approval.
        raise IsolationError('Effective identity, transport and audit gates not implemented')

    def snapshot(self):
        result={}
        with self.engine.connect() as c:
            c.exec_driver_sql('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
            for table,pk in TABLES.items():
                rows=c.execute(text('SELECT * FROM '+table+' ORDER BY '+pk)).mappings()
                result[table]={row[pk]:digest(list(row.items())) for row in rows}
        return result

    def historical_check(self):
        mismatches=0
        with self.engine.connect() as c:
            c.exec_driver_sql('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
            for table,old in self.original.items():
                pk=TABLES[table]
                for identity,expected in old.items():
                    row=c.execute(text('SELECT * FROM '+table+' WHERE '+pk+'=:pk'),{'pk':identity}).mappings().first()
                    if row is None or digest(list(row.items()))!=expected:mismatches+=1
        return {'historical_rows_compared':sum(map(len,self.original.values())),'mismatches':mismatches,'unchanged':mismatches==0}

    def identifier(self,prefix='u',ip=False):
        # Regenerate collisions BEFORE exposing identifiers to test assertions.
        for _ in range(8):
            with self.lock:
                self.counter+=1
                value=prefix+self.run_id[:16]+format(self.counter,'08x')
            account=None if ip else normalized(value)
            candidates=[keyhash(a+':ip:'+value) for a in ('csrf','login','register')] if ip else [keyhash(a+':account:'+account) for a in ('login','register')]
            with self.engine.connect() as c:
                occupied=bool(account and c.execute(text('SELECT id FROM auth_users WHERE username_normalized=:n'),{'n':account}).first())
                occupied |= any(c.execute(text('SELECT key_hash FROM auth_throttles WHERE key_hash=:k'),{'k':k}).first() is not None for k in candidates)
            if occupied:continue
            with self.lock:
                if ip:self.ips.add(value)
                else:self.names.add(account)
            return value
        raise IsolationError('Identifier collision retries exhausted')

    def reserve(self,ip,account=None):
        with self.lock, self.engine.connect() as c:
            if ip not in self.ips:raise IsolationError('Unplanned test IP')
            keys=[keyhash(action+':ip:'+ip) for action in ('csrf','login','register')]
            if account is not None:
                if account not in self.names:raise IsolationError('Unplanned test account')
                if account not in self.claimed_names:
                    if c.execute(text('SELECT id FROM auth_users WHERE username_normalized=:n'),{'n':account}).first():
                        raise IsolationError('Account collision: regenerate plan before request')
                    self.claimed_names.add(account)
                keys += [keyhash(action+':account:'+account) for action in ('login','register')]
            # A request is not dispatched until ALL its potential keys are proven fresh.
            for key in keys:
                if key not in self.claimed_keys and c.execute(text('SELECT key_hash FROM auth_throttles WHERE key_hash=:k'),{'k':key}).first():
                    raise IsolationError('Throttle collision: request blocked')
            self.claimed_keys.update(keys)
            self.request_checks+=1

    def require_new(self,table,identity):
        if table not in TABLES or identity in self.original[table]:raise IsolationError('Historical record write rejected')
        with self.engine.connect() as c:
            if not c.execute(text('SELECT '+TABLES[table]+' FROM '+table+' WHERE '+TABLES[table]+'=:p'),{'p':identity}).first():
                raise IsolationError('Expected current-run record is absent')

    def summary(self):
        return {'run_namespace_digest':digest(self.run_id),'baseline':{t:{'rows':len(rows),'pk_set_digest':digest(sorted(rows,key=str)),'row_hash_aggregate':digest(sorted(rows.items(),key=lambda v:str(v[0])))} for t,rows in self.original.items()},'ddl_permission_gate':self.permissions_checked,'pre_request_checks':self.request_checks}

def policy():
    if _ACTIVE is None:raise IsolationError('Use protected_regression_runner.py; direct pytest is blocked')
    return _ACTIVE

def new_name(prefix='u'):return policy().identifier(prefix)
def new_ip(prefix='test-'):return policy().identifier(prefix,ip=True)

class GuardedClient(FlaskClient):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.environ_base['REMOTE_ADDR']=new_ip()

    def open(self,*args,**kwargs):
        p=policy();ip=kwargs.get('environ_overrides',{}).get('REMOTE_ADDR',self.environ_base['REMOTE_ADDR'])
        data=kwargs.get('json');account=None
        # Check every actual name, including case/full-width variants. Rejected input
        # cases have no account DML; still preflight their IP/CSRF keys.
        headers=kwargs.get('headers') or {}
        if isinstance(data,dict) and set(data)=={'username','password'} and isinstance(data['password'],str) and 6<=len(data['password'])<=128 and headers.get('Origin') in self.application.config['ALLOWED_ORIGINS']:
            try:account=normalized(data['username'])
            except ValueError:pass
        p.reserve(ip,account)
        cookie=self.get_cookie('mi2_sid',path='/api/v1')
        if cookie:
            sid=keyhash(cookie.value)
            with p.engine.connect() as c:
                row=c.execute(text('SELECT id FROM auth_sessions WHERE sid_hash=:s'),{'s':sid}).first()
            if row and row[0] in p.original['auth_sessions']:raise IsolationError('Historical session reuse rejected')
        return super().open(*args,**kwargs)

def guarded_create_app(overrides=None):
    p=policy()
    from app import create_app
    contract=disposable_config()
    contract.update(overrides or {})
    validate_disposable(p.url,contract)
    if 'DATABASE_URL' in contract:
        try:
            supplied=make_url(contract['DATABASE_URL'])
        except Exception:
            raise IsolationError('Invalid explicit test URL') from None
        if supplied != p.url:
            raise IsolationError('Conflicting explicit test URL')
    contract['DATABASE_URL']=p.url
    app=create_app(contract)
    if app.extensions['auth_engine'].url != p.url:raise IsolationError('Unexpected regression connection')
    app.test_client_class=GuardedClient
    return app

def require_new_user(app,name):
    p=policy()
    with p.engine.connect() as c:identity=c.execute(text('SELECT id FROM auth_users WHERE username_normalized=:n'),{'n':normalized(name)}).scalar_one()
    p.require_new('auth_users',identity)

def require_new_session(app,sid):
    p=policy()
    with p.engine.connect() as c:identity=c.execute(text('SELECT id FROM auth_sessions WHERE sid_hash=:s'),{'s':keyhash(sid)}).scalar_one()
    p.require_new('auth_sessions',identity)

def require_new_throttle(app,key):policy().require_new('auth_throttles',key)

def main():
    global _ACTIVE
    parser=argparse.ArgumentParser()
    parser.add_argument('--execute-authorized',action='store_true',help='requires separate authorization AFTER BLOCK-02B review')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    output=Path(args.output).resolve()
    log_root=Path(__file__).resolve().parents[3]/'项目工作日志'
    if log_root.resolve() not in output.parents or output.suffix!='.json':
        raise IsolationError('Report output must be JSON inside project work logs')
    result={'scope':'read-only preflight','regression_executed':False,'status':'BLOCKED'}
    try:
        _ACTIVE=Policy()
        # Demonstrate plan preflight without HTTP calls or DML.
        _ACTIVE.reserve(new_ip(),normalized(new_name('probe')))
        result.update(_ACTIVE.summary())
        if args.execute_authorized:
            raise IsolationError('Real regression is not enabled')
        check=_ACTIVE.historical_check();result['historical_check']=check
        result['status']='PASS' if check['unchanged'] and result.get('pytest_exit_code',0)==0 else 'BLOCKED'
    except BaseException as error:
        result['error_type']=type(error).__name__
        result['historical_check']={'unchanged':None,'state':'NOT PROVEN after interrupted preflight/run; do not continue'}
        # No exception message/traceback: drivers may contain usernames or parameters.
        result['status']='BLOCKED'
    finally:
        if _ACTIVE is not None:
            try:
                _ACTIVE.engine.dispose()
            except Exception:
                result['status']='BLOCKED'
                result['cleanup_state']='FAILED / NOT PROVEN'
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['status']=='PASS' else 1

if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    sys.modules['protected_regression_runner']=sys.modules[__name__]
    raise SystemExit(main())
