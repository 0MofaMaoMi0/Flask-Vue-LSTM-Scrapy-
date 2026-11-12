"""Demo identities only, separate from all authentication systems."""
from flask import Blueprint,request,jsonify
from .recommendation import BehaviorStore,Conflict,USERS,recommend
from .platform_contracts import DemoIdentityProvider,AuthenticationRequired

def create_recommendation_blueprint(movie_provider,store=None,identity=None):
    store=store or BehaviorStore();bp=Blueprint('demo_recommendation',__name__)
    identity=identity or DemoIdentityProvider()
    def user():
        uid=identity.resolve(request)
        store.user(uid);return uid
    def response(data=None,status=200,code='OK'):return jsonify(code=code,data=data),status
    def guarded(fn):
        def call():
            try:return fn()
            except AuthenticationRequired:return response(status=401,code='AUTHENTICATION_REQUIRED')
            except Conflict:return response(status=409,code='STATE_CONFLICT')
            except ValueError:return response(status=400,code='INVALID_INPUT')
            except Exception:return response(status=500,code='INTERNAL_ERROR')
        call.__name__=fn.__name__;return call
    @bp.get('/api/catalog/behavior')
    @guarded
    def behavior():return response({'user':user(),'state':store.snapshot(user()),'mode':'DEMO_USERS_ONLY'})
    @bp.post('/api/catalog/behavior')
    @guarded
    def record():
        if request.headers.get('Origin') not in (None,'http://127.0.0.1:5175'):raise ValueError('Origin rejected')
        if not request.is_json:raise ValueError('JSON required')
        movies=movie_provider();return response(store.apply(user(),request.get_json(silent=True),{m['movie_id'] for m in movies if m.get('catalog_status')=='active'}))
    @bp.get('/api/catalog/recommendations')
    @guarded
    def recommendations():
        if set(request.args)-{'limit','favorite_weight','want_weight','rating_weight'} or any(len(request.args.getlist(k))!=1 for k in request.args):raise ValueError('Unexpected parameter')
        weights={k:float(request.args.get(k+'_weight',default)) for k,default in [('favorite',3),('want',2),('rating',1)]}
        return response(recommend(movie_provider(),store.snapshot(user()),limit=int(request.args.get('limit',12)),weights=weights))
    return bp
