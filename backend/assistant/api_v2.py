"""V2 BYOK API. No platform key or global paid-approval gate."""
import json,re,time,hashlib,secrets
from pathlib import Path
from flask import Blueprint,request,jsonify,Response,stream_with_context
from app.auth import session_row,csrf_ok
from .store import connect_store,tables
from .reader import CatalogReader
from .byok_vault import Vault,MODELS,redact
from .ledger_v2 import Ledger,Conflict
from .agent_service import AgentService

def install(app):
    url=app.config.get('M7_DATABASE_URL','')
    if not url:return
    # Guest-process hardening only; never changes Windows policy or disk settings.
    import sys
    if sys.platform.startswith('linux'):
        import resource
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    engine=connect_store(url,app.config['M6_SERVER_UUID']);md=tables();vault=Vault();ledger=Ledger(engine,md)
    service=AgentService(engine,md,CatalogReader(app.extensions['auth_engine'],app.extensions['m6_behavior'],app.extensions.get('tmdb_connection'),Path(app.config['MEDIA_CACHE_DIR'])/'.private-recommendations'),vault,ledger,app.config.get('M7_TEST_PROVIDER_FACTORY') if app.config.get('TESTING') else None)
    app.extensions.update(m7_engine=engine,m7_vault=vault,m7_agent=service,m7_ledger=ledger)
    bp=Blueprint('assistant_v2',__name__,url_prefix='/api/v1/assistant')
    def reply(data=None,code='OK',status=200):return jsonify(code=code,data=data),status
    def identity():
        with app.extensions['auth_db'].begin() as db:
            row=session_row(db)
            if not row or row.user_id is None:return None,None
            if request.method!='GET' and not csrf_ok(row):raise PermissionError()
            return row.user_id,row.sid_hash
    @bp.before_request
    def guard():
        from flask import g
        try:user,binding=identity()
        except PermissionError:return reply(code='REQUEST_VERIFICATION_FAILED',status=403)
        if user is None:return reply(code='AUTHENTICATION_REQUIRED',status=401)
        g.m7_user=user;g.m7_binding=binding
        if request.method!='GET' and request.path.rsplit('/',1)[-1] not in ('cancel','disconnect'):
            from sqlalchemy import select
            from sqlalchemy.dialects.mysql import insert as upsert
            budget=md.tables['budgets'];key='byok-rate:'+str(int(time.time())//60)+':'+str(user)
            with engine.begin() as c:
                stmt=upsert(budget).values(id=key,calls=1);c.execute(stmt.on_duplicate_key_update(calls=budget.c.calls+1));count=c.execute(select(budget.c.calls).where(budget.c.id==key).with_for_update()).scalar_one()
            if count>60:return reply(code='RATE_LIMITED',status=429)
    def who():
        from flask import g
        return g.m7_user,g.m7_binding
    def body(allowed):
        b=request.get_json(silent=True)
        if type(b) is not dict or set(b)-set(allowed):raise ValueError('Invalid fields')
        return b
    @bp.get('/status')
    @bp.get('/config')
    def status():
        from datetime import datetime
        from sqlalchemy import select
        user,binding=who();config=vault.status(user,binding);budget=md.tables['budgets']
        with engine.connect() as c:used=c.execute(select(budget.c.calls).where(budget.c.id=='byok:'+datetime.utcnow().date().isoformat()+':'+str(user))).scalar_one_or_none() or 0
        return reply(dict(config,daily_calls_used=used,daily_calls_remaining=max(0,config['daily_calls']-used) if config['daily_calls'] is not None else None,local_available=True,cloud_available=config['configured'] and config['enabled'] and config['verification'] not in ('KEY_REJECTED','MODEL_UNAVAILABLE'),degraded=not config['enabled'],reason='BYOK_CONNECTED_UNVERIFIED' if config['enabled'] else 'BYOK_NOT_CONNECTED',models=list(MODELS),billing='USER_PROVIDER_ACCOUNT',server_mode='SINGLE_PROCESS_EPHEMERAL_CUSTODY'))
    @bp.post('/config')
    def configure():
        user,binding=who()
        try:return reply(vault.put(user,binding,body({'provider','model','api_key','daily_calls','max_output_tokens','enabled','consent','cache_enabled'})))
        except ValueError:return reply(code='INVALID_INPUT',status=400)
    @bp.delete('/config')
    @bp.post('/disconnect')
    def disconnect():
        user,binding=who();vault.disconnect(user,binding);service.cancel_user(user,binding);return reply({'configured':False,'enabled':False})
    @bp.post('/verify')
    def verify():
        user,binding=who()
        try:
            if body(set()):raise ValueError()
            config=vault.config(user,binding)
            if not config:raise ValueError()
            # Explicit metadata check, never a hidden chat-completion test.
            provider=service.provider(user,binding,config);valid=provider.verify();vault.verified(user,binding,config['generation'],'VERIFIED' if valid else 'MODEL_UNAVAILABLE')
            return reply(dict(vault.status(user,binding),paid_model_request=False,check_type='MODELS_METADATA_ONLY'))
        except ValueError:return reply(code='BYOK_NOT_CONNECTED',status=400)
        except Exception as error:
            if config:vault.verified(user,binding,config['generation'],'KEY_REJECTED' if getattr(error,'code','')=='KEY_REJECTED' else 'CHECK_FAILED')
            return reply(code='KEY_CHECK_FAILED',status=502)
    def validate_query(b):
        message=b.get('message');cid=b.get('conversation_id');rid=b.get('request_id')
        if type(message) is not str or not 1<=len(message.strip())<=2000 or type(rid) is not str or not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}',rid) or (cid is not None and (type(cid) is not str or not re.fullmatch(r'[a-zA-Z0-9_-]{32,64}',cid))) or type(b.get('stream',False)) is not bool:raise ValueError()
        return redact(message.strip()),cid,rid
    def start(b,kind='query',movie_id=None):
        user,binding=who();message,cid,rid=validate_query(b)
        try:service.conversation(user,binding,cid)
        except ValueError:return reply(code='CONVERSATION_UNAVAILABLE',status=404)
        fingerprint=hashlib.sha256(json.dumps([kind,message,cid,movie_id],ensure_ascii=False).encode()).hexdigest()
        ledger.cleanup()
        try:key,state,new=ledger.claim(user,binding,rid,fingerprint)
        except Conflict:return reply(code='REQUEST_ID_CONFLICT',status=409)
        if new:service.launch(key,user,binding,message,cid,kind,movie_id)
        if b.get('stream'):return stream(key,not new)
        end=time.monotonic()+100
        while time.monotonic()<end:
            state=ledger.read(key)
            if state['state'] in ('DONE','FAILED','CANCELLED','REJECTED','EXPIRED'):return result(state,not new)
            time.sleep(.1)
        return reply({'request_id':rid,'state':'PENDING','cost':ledger.cost(key)},code='REQUEST_PENDING',status=202)
    def result(state,replayed):
        data=dict(state.get('result') or {})
        if state['state']=='DONE':
            data['replayed']=replayed;data['new_provider_calls']=0 if replayed else data.get('cost',{}).get('provider_calls',0)
            return reply(data)
        return reply(dict(data,state=state['state']),code=data.get('error_code') or ('REQUEST_EXPIRED' if state['state']=='EXPIRED' else 'REQUEST_UNAVAILABLE'),status=409)
    def stream(key,replayed):
        @stream_with_context
        def events():
            last='';end=time.monotonic()+105;yield 'event: status\ndata: {"state":"STARTED"}\n\n'
            try:
                while time.monotonic()<end:
                    state=ledger.read(key);runtime=service.runtime.get(key,{});draft=runtime.get('draft','')
                    if draft!=last:
                        last=draft;yield 'event: draft\ndata: '+json.dumps({'text':preview(draft),'provisional':True},ensure_ascii=True)+'\n\n'
                    if state['state'] in ('DONE','FAILED','CANCELLED','REJECTED','EXPIRED'):
                        data=dict(state.get('result') or {});data.update(state=state['state'],replayed=replayed,new_provider_calls=0 if replayed else data.get('cost',{}).get('provider_calls',state.get('dispatches',0)));yield 'event: result\ndata: '+json.dumps(data,ensure_ascii=True)+'\n\n';return
                    yield ': heartbeat\n\n';time.sleep(.2)
                yield 'event: result\ndata: '+json.dumps({'state':'PENDING','error_code':'REQUEST_PENDING','cost':ledger.cost(key)})+'\n\n'
            except GeneratorExit:
                service.cancel(key);raise
        return Response(events(),mimetype='text/event-stream',headers={'X-Accel-Buffering':'no','Cache-Control':'no-store'})
    @bp.post('/query')
    def query():
        try:return start(body({'message','conversation_id','request_id','stream'}))
        except ValueError:return reply(code='INVALID_INPUT',status=400)
    @bp.post('/explain')
    def explain():
        try:
            b=body({'movie_id','request_id','stream'});mid=b.get('movie_id')
            if type(mid) is not int or mid<1:raise ValueError()
            return start({'message':f'请根据我的偏好解释为什么推荐库内电影ID {mid}。请先读取该片和我的偏好摘要。','request_id':b.get('request_id'),'stream':b.get('stream',False)},'explain',mid)
        except ValueError:return reply(code='INVALID_INPUT',status=400)
    @bp.post('/test')
    def paid_test():
        try:
            b=body({'confirm_paid','request_id','stream'})
            if b.get('confirm_paid') is not True:raise ValueError()
            user,binding=who()
            if not vault.config(user,binding):return reply(code='BYOK_NOT_CONNECTED',status=400)
            return start({'message':'这是我明确触发的付费连接测试。请简短说明你是电影助手，不要调用工具或推荐影片。','request_id':b.get('request_id'),'stream':b.get('stream',False)},'paid_test')
        except ValueError:return reply(code='PAID_TEST_CONFIRMATION_REQUIRED',status=400)
    @bp.get('/requests/<rid>')
    def request_status(rid):
        from datetime import datetime
        if not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}',rid):return reply(code='INVALID_INPUT',status=400)
        user,binding=who();key=ledger.key(user,rid);state=ledger.read(key)
        if not state or state.get('binding')!=binding:return reply(code='REQUEST_NOT_FOUND',status=404)
        # A worker lost on restart is terminal UNKNOWN, never automatically reissued.
        if state['state'] in ('QUEUED','DISPATCHED') and 'created' in state:
            if (datetime.utcnow()-datetime.fromisoformat(state['created'])).total_seconds()>120:
                ledger.finish(key,{'error_code':'REQUEST_INTERRUPTED','cost':ledger.cost(key)},'FAILED');state=ledger.read(key)
        if state['state'] in ('DONE','FAILED','CANCELLED','REJECTED','EXPIRED'):return result(state,True)
        return reply({'state':state['state'],'cost':ledger.cost(key)})
    @bp.post('/cancel')
    def cancel():
        try:
            b=body({'request_id'});user,binding=who();rid=b.get('request_id')
            if type(rid) is not str or not re.fullmatch(r'[a-zA-Z0-9_-]{8,100}',rid):raise ValueError()
            key=ledger.key(user,rid);state=ledger.read(key)
            if not state or state.get('binding')!=binding:return reply(code='REQUEST_NOT_FOUND',status=404)
            active=state['state'] not in ('DONE','FAILED','CANCELLED','REJECTED','EXPIRED')
            if active:service.cancel(key)
            return reply({'state':ledger.read(key)['state'],'cancel_requested':active,'cost':ledger.cost(key)})
        except ValueError:return reply(code='INVALID_INPUT',status=400)
    @app.after_request
    def logout_custody(response):
        if request.path=='/api/v1/auth/logout' and response.status_code==200:
            sid=request.cookies.get(app.config.get('AUTH_COOKIE_NAME','mi2_m6_sid'),'');binding=hashlib.sha256(sid.encode()).hexdigest();vault.disconnect_binding(binding);service.cancel_binding(binding)
        return response
    app.register_blueprint(bp)

def preview(content):
    match=re.search(r'"answer"\s*:\s*"',content)
    if not match:return ''
    part=content[match.end():];escape=False;end=0
    for i,c in enumerate(part):
        if c=='"' and not escape:
            try:return json.loads('"'+part[:i]+'"')
            except Exception:return ''
        if c=='\\' and not escape:escape=True
        else:escape=False
        end=i+1
    for trim in range(min(7,len(part))+1):
        try:return json.loads('"'+(part[:-trim] if trim else part)+'"')
        except Exception:pass
    return ''
