"""Authenticated/CSRF-protected TMDB controls; credentials persist, movie data remains read-only."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from flask import Blueprint,request,jsonify,g
from app.auth import session_row,csrf_ok
from .tmdb_connection import TMDBConnection,TMDBError,BOARD_PATHS,MAX_DETAIL_REFRESH
from assistant.credential_store import CredentialError,CredentialAccessDenied

def install(app):
    service=TMDBConnection(Path(app.config['MEDIA_CACHE_DIR'])/'.private-tmdb',transport=app.config.get('TMDB_TEST_TRANSPORT') if app.config.get('TESTING') else None,store=app.extensions.get('credential_store'))
    app.extensions['tmdb_connection']=service
    bp=Blueprint('tmdb_connection',__name__,url_prefix='/api/v1/tmdb')
    def reply(data=None,code='OK',status=200):return jsonify(code=code,data=data),status
    @bp.before_request
    def guard():
        with app.extensions['auth_db'].begin() as db:
            row=session_row(db)
            if not row or row.user_id is None:return reply(code='AUTHENTICATION_REQUIRED',status=401)
            if request.method!='GET' and not csrf_ok(row):return reply(code='REQUEST_VERIFICATION_FAILED',status=403)
            g.tmdb_user=row.user_id;g.tmdb_binding=row.sid_hash
        if request.args:return reply(code='INVALID_INPUT',status=400)
    def body(keys):
        if not request.is_json:raise ValueError()
        value=request.get_json(silent=True)
        if type(value) is not dict or set(value)-set(keys):raise ValueError()
        return value
    @bp.errorhandler(TMDBError)
    def unavailable(error):
        code=error.code
        return reply(dict(service.status(g.tmdb_user,g.tmdb_binding),retry_after=error.retry_after),code='TMDB_'+code,status=429 if code in ('RATE_LIMITED','COOLDOWN','CHECK_THROTTLED') else 400 if code in ('NOT_CONNECTED','KEY_REJECTED','DISCONNECTED','DISABLED') else 503)
    @bp.errorhandler(CredentialAccessDenied)
    def access_denied(error):return reply(code='AUTHENTICATION_REQUIRED',status=401)
    @bp.errorhandler(CredentialError)
    def credentials_unavailable(error):return reply(code='CREDENTIALS_UNAVAILABLE',status=503)
    @bp.errorhandler(ValueError)
    def invalid(error):return reply(code='INVALID_INPUT',status=400)
    @bp.errorhandler(Exception)
    def internal(error):
        from werkzeug.exceptions import HTTPException
        if isinstance(error,HTTPException):return reply(code='REQUEST_REJECTED',status=error.code)
        app.logger.error('TMDB request failed: %s',type(error).__name__)
        return reply(code='TMDB_UNAVAILABLE',status=503)
    @bp.get('/status')
    def status():return reply(service.status(g.tmdb_user,g.tmdb_binding))
    @bp.post('/config')
    def configure():return reply(service.put(g.tmdb_user,g.tmdb_binding,body({'access_token','consent'})))
    @bp.post('/verify')
    def verify():
        body(set());return reply(service.verify(g.tmdb_user,g.tmdb_binding))
    @bp.post('/disconnect')
    def disconnect():
        body(set());service.disconnect(g.tmdb_user,g.tmdb_binding);return reply(service.status(g.tmdb_user,g.tmdb_binding))
    @bp.post('/enable')
    def enable_saved():
        body(set());return reply(service.enable(g.tmdb_user,g.tmdb_binding))
    @bp.post('/refresh')
    def refresh():
        b=body({'channel','movie_ids'});channel=b.get('channel');ids=b.get('movie_ids',[])
        if channel is not None and (type(channel) is not str or channel not in BOARD_PATHS):raise ValueError()
        if type(ids) is not list or len(ids)>MAX_DETAIL_REFRESH or len(set(i for i in ids if type(i) is int))!=len(ids) or any(type(i) is not int or i<1 for i in ids):raise ValueError()
        user,binding=g.tmdb_user,g.tmdb_binding
        state=service.status(user,binding)
        if not state['configured']:raise TMDBError('NOT_CONNECTED')
        if not state['enabled']:raise TMDBError('DISABLED')
        results=[]
        if ids:
            from .recommendation_data import load_candidates
            mapped={m['movie_id']:m for m in load_candidates(app.extensions['auth_engine']) if m.get('tmdb_id') and m.get('is_adult') is not True}
            if any(mid not in mapped or not str(mapped[mid]['tmdb_id']).isdecimal() for mid in ids):raise ValueError()
            def detail(mid):
                try:
                    doc=service.detail(user,binding,int(mapped[mid]['tmdb_id']),force=True)
                    return dict(movie_id=mid,status='OK',captured_at=doc['captured_at'])
                except TMDBError as e:return dict(movie_id=mid,status=e.code)
            with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(detail,ids))
        else:
            for c in ([channel] if channel else BOARD_PATHS):
                try:
                    doc=service.snapshot(c,user,binding,force=True)
                    if doc is None:raise TMDBError('DISCONNECTED')
                    results.append(dict(channel=c,status='OK',captured_at=doc['captured_at'],items=len(doc['items'])))
                except TMDBError as e:results.append(dict(channel=c,status=e.code))
        return reply(dict(results=results,all_updated=all(r['status']=='OK' for r in results),connection=service.status(user,binding),database_writes=0))
    @app.after_request
    def clear_on_logout(response):
        if request.path=='/api/v1/auth/logout' and response.status_code==200:
            cookie=request.cookies.get(app.config.get('AUTH_COOKIE_NAME','mi2_m6_sid'),'')
            service.disconnect_binding(hashlib.sha256(cookie.encode()).hexdigest())
        return response
    app.register_blueprint(bp)
