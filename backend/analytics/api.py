from datetime import datetime,timedelta,timezone
import time,threading
from flask import Blueprint,request,jsonify,g
from sqlalchemy.exc import SQLAlchemyError
from app.auth import session_row
from .registry import metric
from .metrics import catalog,behavior,usage

def parse_window(args):
    now=datetime.now(timezone.utc);end=now;start=now-timedelta(days=30)
    def parse(value):
        if len(value)>40:raise ValueError()
        d=datetime.fromisoformat(value.replace('Z','+00:00'))
        if d.tzinfo is None:
            if len(value)!=10:raise ValueError()
            d=d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    if 'from' in args:start=parse(args['from'])
    if 'to' in args:end=parse(args['to'])
    if start>=end or end-start>timedelta(days=366):raise ValueError()
    window={'from':start.isoformat().replace('+00:00','Z'),'to':end.isoformat().replace('+00:00','Z')}
    return start.replace(tzinfo=None),end.replace(tzinfo=None),window


class RateLimiter:
    """Fixed-window per-user limit with a bounded key table.

    Expired keys are pruned first; at capacity the oldest live key is evicted
    instead of rejecting a first-time caller, so memory stays bounded without
    turning away new users during a burst.
    """
    def __init__(self, *, limit=60, window=60.0, capacity=10000, clock=time.monotonic):
        self.limit=limit;self.window=window;self.capacity=capacity;self._clock=clock
        self._rates={};self._lock=threading.Lock()

    def allow(self,key):
        now=self._clock()
        with self._lock:
            for stale in [k for k,v in self._rates.items() if now-v[0]>=self.window]:
                self._rates.pop(stale,None)
            stamp,count=self._rates.get(key,(now,0))
            if count>=self.limit:return False
            if key not in self._rates and len(self._rates)>=self.capacity:
                oldest=min(self._rates,key=lambda k:(self._rates[k][0],str(k)))
                self._rates.pop(oldest,None)
            self._rates[key]=(stamp,count+1)
            return True

def install(app):
    bp=Blueprint('analytics',__name__,url_prefix='/api/v1/insights');limiter=RateLimiter()
    @bp.before_request
    def guard():
        try:
            with app.extensions['auth_db'].begin() as db:
                row=session_row(db)
                if not row or row.user_id is None:return jsonify(code='AUTHENTICATION_REQUIRED',data=None),401
                g.insight_user=row.user_id
        except SQLAlchemyError:return jsonify(code='DATA_UNAVAILABLE',data={'status':'UNAVAILABLE'}),503
        allowed={'from','to','dimension'} if request.endpoint.endswith('distributions') else {'from','to'}
        if set(request.args)-allowed or any(len(request.args.getlist(k))!=1 for k in request.args):return jsonify(code='INVALID_INPUT',data=None),400
        try:g.insight_window=parse_window(request.args)
        except (ValueError,TypeError,OverflowError):return jsonify(code='INVALID_TIME_WINDOW',data=None),400
        if not limiter.allow(g.insight_user):return jsonify(code='RATE_LIMITED',data=None),429
    def respond(fn):
        try:metrics=fn();return jsonify(code='OK',data={'metrics':metrics,'as_of':metrics[0]['as_of'] if metrics else None,'time_semantics':'UTC; [from,to); snapshot metrics ignore event windows'})
        except OverflowError:return jsonify(code='QUERY_LIMIT_EXCEEDED',data=None),422
        except SQLAlchemyError:return jsonify(code='DATA_UNAVAILABLE',data={'status':'UNAVAILABLE'}),503
    @bp.get('/catalog/summary')
    def summary():return respond(lambda:catalog(app.extensions['auth_engine'],g.insight_window[2]))
    @bp.get('/catalog/distributions')
    def distributions():
        dimension=request.args.get('dimension','genre')
        if dimension not in ('genre','decade','source'):return jsonify(code='INVALID_DIMENSION',data=None),400
        return respond(lambda:catalog(app.extensions['auth_engine'],g.insight_window[2],dimension))
    @bp.get('/me/behavior')
    def personal():return respond(lambda:behavior(app.extensions['auth_engine'],app.extensions['m6_behavior'],g.insight_user,*g.insight_window))
    @bp.get('/me/ai-usage')
    def ai():return respond(lambda:usage(app,g.insight_user,*g.insight_window))
    @bp.get('/recommendation/evaluation')
    def evaluation():
        def result():
            # No production evaluation dataset has been qualified/sealed. Never infer quality from catalog scores.
            n=catalog(app.extensions['auth_engine'],g.insight_window[2])[0]['value']
            return [metric('recommendation.'+name,None,'Qualified temporal holdout not established; catalog movie count only','PLATFORM_REAL',g.insight_window[2],0,status='INSUFFICIENT_DATA',unit='ratio',notes=f'K=10; {n} active candidates; no sealed independent test-user interaction split. Popularity and MovieInsightRules are available in isolated evaluator; no claimed real accuracy.') for name in ('precision_at_10','recall_at_10','ndcg_at_10','coverage_at_10')]
        return respond(result)
    app.register_blueprint(bp)

