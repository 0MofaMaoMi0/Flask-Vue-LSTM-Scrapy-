"""Durable V2 idempotency and per-dispatch billing facts, no secret storage."""
import hashlib,json
from datetime import datetime,timedelta
from sqlalchemy import select,insert,update,text
from sqlalchemy.dialects.mysql import insert as upsert
from sqlalchemy.exc import IntegrityError
class Conflict(ValueError):pass
class Quota(ValueError):pass
class Ledger:
    def __init__(self,engine,md):self.engine=engine;self.md=md
    def key(self,user,rid):return hashlib.sha256(f'byok-v2:{user}:{rid}'.encode()).hexdigest()
    def claim(self,user,binding,rid,fingerprint):
        key=self.key(user,rid);t=self.md.tables['requests']
        with self.engine.begin() as c:
            row=c.execute(select(t).where(t.c.id==key)).mappings().first()
            if row:
                if row['user_id']!=user or row['fingerprint']!=fingerprint or row['response'].get('binding')!=binding:raise Conflict('Request identity/content conflict')
                return key,row['response'],False
            state={'version':2,'binding':binding,'state':'QUEUED','cancel':False,'dispatches':0,'usage':[],'cost_unknown':False,'result':None,'created':datetime.utcnow().isoformat()}
            try:c.execute(insert(t).values(id=key,user_id=user,fingerprint=fingerprint,response=state,expires_at=datetime.utcnow()+timedelta(days=1)))
            except IntegrityError:raise Conflict('Concurrent request; retry same ID') from None
            return key,state,True
    def read(self,key):
        t=self.md.tables['requests']
        with self.engine.connect() as c:return c.execute(select(t.c.response).where(t.c.id==key)).scalar_one_or_none()
    def mutate(self,key,fn):
        t=self.md.tables['requests']
        with self.engine.begin() as c:
            state=c.execute(select(t.c.response).where(t.c.id==key).with_for_update()).scalar_one();state=dict(state);fn(state);c.execute(update(t).where(t.c.id==key).values(response=state));return state
    def cancel(self,key):return self.mutate(key,lambda s:s.update(cancel=True))
    def dispatch(self,key,user,model,daily,paid):
        t=self.md.tables['requests'];usage=self.md.tables['usage'];budget=self.md.tables['budgets'];day=datetime.utcnow().date().isoformat()
        with self.engine.begin() as c:
            state=dict(c.execute(select(t.c.response).where(t.c.id==key).with_for_update()).scalar_one())
            if state['cancel']:raise Conflict('Cancelled before dispatch')
            # Separate BYOK user budgets; no legacy platform 5/50 gate.
            bk='byok:'+day+':'+str(user)+('' if paid else ':mock')
            stmt=upsert(budget).values(id=bk,calls=0);c.execute(stmt.on_duplicate_key_update(id=stmt.inserted.id))
            count=c.execute(select(budget.c.calls).where(budget.c.id==bk).with_for_update()).scalar_one()
            if daily is not None and count>=daily:raise Quota('User daily call limit')
            c.execute(update(budget).where(budget.c.id==bk).values(calls=count+1))
            number=state['dispatches']+1;call_id=hashlib.sha256(f'{key}:{number}'.encode()).hexdigest();state.update(dispatches=number,state='DISPATCHED',cost_unknown=paid)
            c.execute(insert(usage).values(id=call_id,user_id=user,model=model,status='DISPATCHED',created_at=datetime.utcnow(),tokens={'paid_request':paid,'input_tokens':None,'output_tokens':None,'kind':'BYOK_AGENT'}))
            c.execute(update(t).where(t.c.id==key).values(response=state))
        return call_id
    def record(self,key,call_id,usage,paid,status='SUCCEEDED'):
        table=self.md.tables['usage'];requests=self.md.tables['requests'];budgets=self.md.tables['budgets']
        with self.engine.begin() as c:
            state=dict(c.execute(select(requests.c.response).where(requests.c.id==key).with_for_update()).scalar_one())
            if any(r['call_id']==call_id for r in state['usage']):return
            original=c.execute(select(table).where(table.c.id==call_id).with_for_update()).mappings().one()
            c.execute(update(table).where(table.c.id==call_id).values(status=status,tokens=dict(usage or {},paid_request=paid,kind='BYOK_AGENT',usage_known=usage is not None)))
            if status=='NOT_DISPATCHED':
                from sqlalchemy import func
                original_paid=(original['tokens'] or {}).get('paid_request',True)
                budget_key='byok:'+original['created_at'].date().isoformat()+':'+str(original['user_id'])+('' if original_paid else ':mock')
                c.execute(update(budgets).where(budgets.c.id==budget_key).values(calls=func.greatest(budgets.c.calls-1,0)))
            state['usage'].append({'call_id':call_id,'tokens':usage,'paid':paid,'status':status})
            state['cost_unknown']=any(r['paid'] and r['tokens'] is None for r in state['usage'])
            c.execute(update(requests).where(requests.c.id==key).values(response=state))
    def finish(self,key,result,state='DONE'):
        def apply(s):
            if s['state'] in ('DONE','FAILED','CANCELLED','REJECTED','EXPIRED'):return
            if state=='DONE' and s.get('cancel'):
                s.update(state='CANCELLED',result={'error_code':'CANCELLED','cost':result.get('cost')});return
            s.update(state=state,result=result)
        return self.mutate(key,apply)
    def cost(self,key):
        s=self.read(key);rows=s['usage'];paid=any(r['paid'] for r in rows) or bool(s['dispatches'] and s['cost_unknown'])
        unknown=any(r['tokens'] is None for r in rows) or s['dispatches']>len(rows)
        def total(k):return None if unknown or any(r['tokens'].get(k) is None for r in rows if r['tokens'] is not None) else sum(r['tokens'].get(k,0) for r in rows)
        return {'paid_model_request':paid,'known_input_tokens':sum((r['tokens'] or {}).get('input_tokens') or 0 for r in rows),'known_output_tokens':sum((r['tokens'] or {}).get('output_tokens') or 0 for r in rows),'unknown_calls':sum(r['tokens'] is None for r in rows)+max(0,s['dispatches']-len(rows)),'provider_calls':s['dispatches']-sum(r['status']=='NOT_DISPATCHED' for r in rows),'reserved_attempts':s['dispatches'],'input_tokens':total('input_tokens'),'output_tokens':total('output_tokens'),'cached_input_tokens':total('cached_input_tokens'),'amount':0 if s['dispatches']==sum(r['status']=='NOT_DISPATCHED' for r in rows) else None,'currency':None,'amount_status':'NOT_CALLED' if s['dispatches']==sum(r['status']=='NOT_DISPATCHED' for r in rows) else 'UNKNOWN','usage_status':'UNKNOWN' if unknown else 'PROVIDER_REPORTED' if any(r['status']!='NOT_DISPATCHED' for r in rows) else 'NOT_CALLED'}
    def cleanup(self):
        t=self.md.tables['requests']
        with self.engine.begin() as c:
            c.execute(text("DELETE FROM `sessions` WHERE expires_at < :now AND JSON_EXTRACT(`state`, '$.version') = 2 LIMIT 50"),{'now':datetime.utcnow()})
            c.execute(text("DELETE FROM `cache` WHERE expires_at < :now AND JSON_EXTRACT(`response`, '$.conversation_id') IS NOT NULL LIMIT 50"),{'now':datetime.utcnow()})
            rows=c.execute(select(t).where(t.c.expires_at<datetime.utcnow(),text("JSON_EXTRACT(`response`, '$.version') = 2")).limit(50).with_for_update()).mappings().all()
            for row in rows:
                s=row['response']
                if not s or s.get('version')!=2:continue
                # Keep an irreversible idempotency tombstone; never silently rebill expired IDs.
                c.execute(update(t).where(t.c.id==row['id']).values(response={'version':2,'binding':s['binding'],'state':'EXPIRED','cancel':True,'dispatches':s.get('dispatches',0),'usage':[],'cost_unknown':True,'result':None},expires_at=datetime(2099,1,1)))
