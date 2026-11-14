"""Atomic paid-call reservations and per-user versioned cache, all on MySQL."""
import hashlib,json
from datetime import datetime,timedelta
from sqlalchemy import select,insert,update
from sqlalchemy.dialects.mysql import insert as upsert
from sqlalchemy.exc import IntegrityError,OperationalError
from .providers import Unavailable,verify_output
class BudgetExceeded(Unavailable):pass
class ModelService:
    def __init__(self,engine,md,provider,user_limit=5,global_limit=50):
        self.engine=engine;self.md=md;self.provider=provider;self.user_limit=user_limit;self.global_limit=global_limit
    def explain(self,user,request_id,facts,version):
        digest=lambda obj:hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
        key=digest([user,version,facts,'m7-prompt-v1',getattr(self.provider,'model','mock')]);rid=digest([user,request_id]);now=datetime.utcnow()
        cache=self.md.tables['cache'];usage=self.md.tables['usage'];budgets=self.md.tables['budgets']
        try:
            with self.engine.begin() as c:
                prior=c.execute(select(usage).where(usage.c.id==rid)).mappings().first()
                if prior:
                    # Unknown completion is never retried and cannot cause duplicate billing.
                    raise Unavailable('REQUEST_ALREADY_RESERVED')
                cached=c.execute(select(cache).where(cache.c.id==key,cache.c.expires_at>now)).mappings().first()
                if cached:return cached['response'],'CACHE',False
                day=now.date().isoformat()
                for scope,limit in [('global',self.global_limit),(str(user),self.user_limit)]:
                    k=day+':'+('paid:' if self.provider.paid else 'mock:')+scope
                    stmt=upsert(budgets).values(id=k,calls=0);c.execute(stmt.on_duplicate_key_update(id=stmt.inserted.id))
                    count=c.execute(select(budgets.c.calls).where(budgets.c.id==k).with_for_update()).scalar_one()
                    if count>=limit:raise BudgetExceeded('BUDGET_EXCEEDED')
                    c.execute(update(budgets).where(budgets.c.id==k).values(calls=count+1))
                c.execute(insert(usage).values(id=rid,user_id=user,model=getattr(self.provider,'model','mock'),status='RESERVED',created_at=now))
        except (IntegrityError,OperationalError):
            raise Unavailable('RESERVATION_UNAVAILABLE') from None
        try:
            result=self.provider.explain(facts);ids,tokens=verify_output(result,facts)
            data={'movie_ids':ids,'usage':tokens}
            with self.engine.begin() as c:
                c.execute(update(usage).where(usage.c.id==rid).values(status='VERIFIED',tokens=tokens))
                stmt=upsert(cache).values(id=key,response=data,expires_at=now+timedelta(minutes=10));c.execute(stmt.on_duplicate_key_update(response=stmt.inserted.response,expires_at=stmt.inserted.expires_at))
            return data,'LLM' if self.provider.paid else 'MOCK',self.provider.paid
        except Exception:
            with self.engine.begin() as c:c.execute(update(usage).where(usage.c.id==rid).values(status='UNAVAILABLE'))
            error=Unavailable('LLM_UNAVAILABLE');error.paid_model_request=self.provider.paid
            raise error from None
