"""Root-only M7 companion for existing fixed backup/restore operations.
No secrets or row contents are printed; snapshots stay in private guest storage.
"""
import hashlib,json,os,re,subprocess
from pathlib import Path
from urllib.parse import urlsplit
TABLES=('budgets','cache','requests','sessions','usage')

def discover(sql):
    env=Path('/etc/movieinsight-m7/app.env')
    if os.geteuid()!=0:raise RuntimeError('Root maintenance only')
    st=env.stat()
    if env.is_symlink() or st.st_uid!=0 or st.st_mode&0o077:raise RuntimeError('Private M7 config required')
    fields=dict(line.split('=',1) for line in env.read_text().splitlines() if line)
    u=urlsplit(fields.get('MI2_M7_DATABASE_URL',''))
    if u.scheme!='mysql+pymysql' or u.hostname!='127.0.0.1' or u.port!=3306 or u.username!='mi2m7app' or not u.password or u.query:raise RuntimeError('Unexpected M7 endpoint')
    schema=u.path.lstrip('/')
    if schema!='mi2_m7_assistant_20261107':raise RuntimeError('Unexpected M7 schema')
    if sql("SELECT GROUP_CONCAT(TABLE_NAME ORDER BY TABLE_NAME) FROM information_schema.tables WHERE table_schema='"+schema+"'")!=','.join(TABLES):raise RuntimeError('M7 table set mismatch')
    grants=sql("SHOW GRANTS FOR 'mi2m7app'@'127.0.0.1'")
    if any(word in grants.upper() for word in ('ALL PRIVILEGES','WITH GRANT OPTION','CREATE,','ALTER,','DROP,')):raise RuntimeError('Unexpected application privilege')
    if ('`'+schema+'`.*') not in grants:raise RuntimeError('M7 schema grant missing')
    for line in grants.splitlines():
        match=re.match(r'GRANT (.+?) ON (.+?) TO ',line)
        if not match:raise RuntimeError('Unrecognized grants')
        privileges={x.strip() for x in match[1].split(',')}
        if match[2]=='*.*' and privileges=={'USAGE'}:continue
        if match[2]!='`'+schema+'`.*' or not privileges<={'SELECT','INSERT','UPDATE','DELETE'}:raise RuntimeError('Unexpected privilege scope')
    return schema

def fingerprint(sql,schema,table):
    if not re.fullmatch(r'mi2_(?:m6|m7)_[a-z0-9_]+',schema) or not re.fullmatch(r'[a-z0-9_]+',table):raise RuntimeError('Unsafe identifier')
    definition=sql('SHOW CREATE TABLE `'+schema+'`.`'+table+'`').split('\t',1)[-1]
    # Auto-increment counters are operational state, checked separately in DDL text.
    columns=sql("SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema='"+schema+"' AND table_name='"+table+"' ORDER BY ORDINAL_POSITION").splitlines()
    cols=','.join('`'+x+'`' for x in columns)
    order=sql("SELECT COLUMN_NAME FROM information_schema.key_column_usage WHERE table_schema='"+schema+"' AND table_name='"+table+"' AND constraint_name='PRIMARY' ORDER BY ORDINAL_POSITION").splitlines()
    if not order:raise RuntimeError('Primary key required')
    payload=sql('SELECT JSON_ARRAY('+cols+') FROM `'+schema+'`.`'+table+'` ORDER BY '+','.join('`'+x+'`' for x in order))
    return dict(structure_sha256=hashlib.sha256(definition.encode()).hexdigest(),content_sha256=hashlib.sha256(payload.encode()).hexdigest())

def semantic_summary(sql,schema):
    q='`'+schema+'`.'
    def count(table,condition='1=1'):return int(sql('SELECT COUNT(*) FROM '+q+'`'+table+'` WHERE '+condition))
    return dict(requests=count('requests'),expired_tombstones=count('requests',"JSON_UNQUOTE(JSON_EXTRACT(response,'$.state'))='EXPIRED'"),unknown_usage=count('usage',"tokens IS NULL OR JSON_TYPE(tokens)='NULL' OR JSON_EXTRACT(tokens,'$.input_tokens') IS NULL OR JSON_TYPE(JSON_EXTRACT(tokens,'$.input_tokens'))='NULL'"),budget_rows=count('budgets'),budget_calls=int(sql('SELECT COALESCE(SUM(calls),0) FROM '+q+'budgets')))

def mock_restore_probe(target):
    if not re.fullmatch(r'mi2_m7_restore_[a-z0-9_]+',target):raise RuntimeError('Restore-only target required')
    # Import existing ledger, but never instantiate a network model provider.
    import sys,secrets
    sys.path.insert(0,'/opt/movieinsight-m6/current')
    from sqlalchemy import create_engine,select,update,func
    from assistant.store import tables
    from assistant.ledger_v2 import Ledger,Conflict
    from datetime import datetime,timedelta
    engine=create_engine('mysql+pymysql://root@localhost/'+target+'?unix_socket=/var/run/mysqld/mysqld.sock',hide_parameters=True)
    ledger=Ledger(engine,tables());user=900000000000000000+secrets.randbelow(1000000);rid='M8_RESTORE_MOCK_'+secrets.token_hex(12);binding=secrets.token_hex(32);checks={}
    key,_,fresh=ledger.claim(user,binding,rid,'restore-probe');checks['fresh_mock_request']=fresh
    call=ledger.dispatch(key,user,'M8_RESTORE_MOCK',None,False)
    ledger.record(key,call,{'input_tokens':100,'output_tokens':10,'cached_input_tokens':40},False)
    _,_,again=ledger.claim(user,binding,rid,'restore-probe');checks['idempotency_no_new_dispatch']=not again and ledger.read(key)['dispatches']==1
    try:ledger.claim(user,binding,rid,'changed');checks['fingerprint_conflict']=False
    except Conflict:checks['fingerprint_conflict']=True
    cost=ledger.cost(key);checks['cached_is_subset']=cost['input_tokens']==100 and cost['cached_input_tokens']==40 and cost['known_input_tokens']==100
    budget=ledger.md.tables['budgets'];budget_id='byok:'+datetime.utcnow().date().isoformat()+':'+str(user)+':mock'
    with engine.connect() as c:checks['budget_after_dispatch']=c.execute(select(budget.c.calls).where(budget.c.id==budget_id)).scalar_one()==1
    missing,_,_=ledger.claim(user,binding,rid+'_unknown','unknown');ledger.dispatch(missing,user,'M8_RESTORE_MOCK',None,False)
    cost=ledger.cost(missing);checks['unknown_tokens_and_amount']=cost['input_tokens'] is None and cost['amount'] is None and cost['unknown_calls']==1
    unsent,_,_=ledger.claim(user,binding,rid+'_unsent','unsent');cid=ledger.dispatch(unsent,user,'M8_RESTORE_MOCK',None,False);ledger.record(unsent,cid,None,False,status='NOT_DISPATCHED')
    checks['not_dispatched_excluded']=ledger.cost(unsent)['provider_calls']==0
    with engine.connect() as c:checks['budget_reservation_reversed']=c.execute(select(budget.c.calls).where(budget.c.id==budget_id)).scalar_one()==2
    requests=ledger.md.tables['requests']
    # Exercise existing tombstone cleanup only in the detached restored copy.
    with engine.begin() as c:c.execute(update(requests).where(requests.c.id==key).values(expires_at=datetime.utcnow()-timedelta(days=1)))
    ledger.cleanup();_,state,new=ledger.claim(user,binding,rid,'restore-probe')
    checks['expired_tombstone_no_rebill']=not new and state['state']=='EXPIRED' and state['dispatches']==1 and state['result'] is None
    engine.dispose()
    if not all(checks.values()):raise RuntimeError('Restore mock semantics failed')
    return {'checks':checks,'all_pass':True,'real_provider_calls':0,'only_restore_target_written':True}

if __name__=='__main__':
    import sys
    try:
        if os.geteuid()!=0 or len(sys.argv)!=2:raise RuntimeError('Fixed privileged restore probe only')
        print(json.dumps(mock_restore_probe(sys.argv[1])))
    except Exception as error:
        print(json.dumps({'all_pass':False,'error_type':type(error).__name__}))
        raise SystemExit(1)
