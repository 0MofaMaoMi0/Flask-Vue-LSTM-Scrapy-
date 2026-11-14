"""Authenticated administrator imports, implemented without system privileges."""
import threading
from pathlib import Path
from flask import Blueprint,request,jsonify,g
from sqlalchemy import select
from app.auth import session_row,csrf_ok
from app.models import User
from .admin_importer import CatalogImporter,ImportRejected,MAX_MOVIES,capture
from .import_jobs import ImportJobs
from .tmdb_connection import TMDBError

def install(app):
    engine=app.extensions['auth_engine'];admins={x.strip().lower() for x in app.config['ADMIN_USERS']}
    def authorized(uid):
        with engine.connect() as c:
            user=c.execute(select(User.username_normalized,User.status).where(User.id==uid)).first()
            return bool(user and user[0] in admins and user[1]=='active')
    importer=app.config.get('IMPORT_TEST_ADAPTER') if app.config.get('TESTING') else None
    importer=importer or CatalogImporter(engine,app.config)
    jobs=ImportJobs(Path(app.config['MEDIA_CACHE_DIR'])/'.admin-imports',importer,authorized)
    app.extensions['admin_import_jobs']=jobs
    slots=threading.BoundedSemaphore(1)
    bp=Blueprint('admin_imports',__name__,url_prefix='/api/admin/imports')
    def reply(data=None,code='OK',status=200):return jsonify(code=code,data=data),status
    @bp.before_request
    def guard():
        with app.extensions['auth_db'].begin() as db:
            row=session_row(db)
            if not row or not row.user_id:return reply(code='AUTHENTICATION_REQUIRED',status=401)
            user=db.get(User,row.user_id)
            if not user or user.status!='active' or user.username_normalized not in admins:return reply(code='FORBIDDEN',status=403)
            if request.method!='GET' and not csrf_ok(row):return reply(code='REQUEST_VERIFICATION_FAILED',status=403)
            g.import_owner=row.user_id;g.import_binding=row.sid_hash
        if request.args:return reply(code='INVALID_INPUT',status=400)
    def body(keys):
        data=request.get_json(silent=True)
        if type(data) is not dict or set(data)-set(keys):raise ValueError('Input')
        return data
    @bp.errorhandler(Exception)
    def errors(error):
        from werkzeug.exceptions import HTTPException
        if isinstance(error,HTTPException):return reply(code='REQUEST_REJECTED',status=error.code)
        if isinstance(error,FileNotFoundError):return reply(code='IMPORT_NOT_FOUND',status=404)
        if isinstance(error,PermissionError):return reply(code='FORBIDDEN',status=403)
        if isinstance(error,ImportRejected):return reply(code=str(error),status=409)
        if isinstance(error,TMDBError):return reply(code='TMDB_'+error.code,status=503)
        if isinstance(error,(ValueError,TypeError,KeyError)):return reply(code='INVALID_INPUT',status=400)
        app.logger.error('Admin import failure: %s',type(error).__name__)
        return reply(code='IMPORT_UNAVAILABLE',status=503)
    @bp.get('')
    def listing():return reply(dict(items=jobs.list(g.import_owner),max_movies=MAX_MOVIES,kind='tmdb_catalog'))
    @bp.get('/quality')
    def quality():
        from .data_quality import inventory
        return reply(inventory(engine))
    def quality_runs():
        from .quality_runs import QualityRuns
        if 'quality_runs' not in app.extensions:
            app.extensions['quality_runs']=QualityRuns(jobs.folder/'.quality-runs',jobs,app.extensions['tmdb_connection'])
        return app.extensions['quality_runs']
    @bp.get('/quality/runs')
    def latest_quality():return reply(quality_runs().latest(g.import_owner))
    @bp.get('/quality/runs/verify')
    def verify_quality():
        from .data_quality import verify_run
        run=quality_runs().latest(g.import_owner);result=verify_run(engine,run)
        import json,os,stat
        backups=[]
        for item in (run or {}).get('items',[]):
            if item.get('status')!='SUCCEEDED':continue
            path=jobs.folder/(item['import_job_id']+'.before')
            ok=False
            try:
                if not path.is_symlink() and (os.name=='nt' or not stat.S_IMODE(path.stat().st_mode)&0o077):
                    doc=json.loads(path.read_text(encoding='utf-8'))
                    ok=doc.get('job_id')==item['import_job_id'] and any(r.get('movie_id')==item['movie_id'] for r in doc.get('before',{}).get('movies',[]))
            except (OSError,ValueError):pass
            if not ok:backups.append(dict(movie_id=item['movie_id'],code='BEFORE_IMAGE_UNAVAILABLE'))
        result['backup_issues']=backups
        if backups:result['status']='REVIEW_REQUIRED'
        return reply(result)
    @bp.post('/quality/runs/cancel')
    def cancel_quality():body(set());return reply(quality_runs().cancel(g.import_owner))
    @bp.post('/quality/runs/resume')
    def resume_quality():
        data=body({'job_id','confirm'})
        if data.get('confirm') is not True:raise ValueError('Confirmation')
        return reply(quality_runs().resume(data.get('job_id'),g.import_owner,g.import_binding),status=202)
    @bp.post('/quality/runs')
    def start_quality():
        from .data_quality import inventory
        data=body({'scope_digest','confirm'})
        if data.get('confirm') is not True:raise ValueError('Confirmation')
        report=inventory(engine)
        if data.get('scope_digest')!=report['scope_digest']:raise ImportRejected('QUALITY_SCOPE_CHANGED')
        return reply(quality_runs().start(g.import_owner,g.import_binding,report['scope'],report['scope_digest']),status=202)
    @bp.post('/preview')
    def preview():
        data=body({'kind','tmdb_ids'})
        ids=data.get('tmdb_ids')
        if data.get('kind')!='tmdb_catalog' or type(ids) is not list or not 1<=len(ids)<=MAX_MOVIES or any(type(i) is not int or not 1<=i<=9999999999 for i in ids) or len(set(ids))!=len(ids):raise ValueError('IDs')
        if not slots.acquire(blocking=False):return reply(code='IMPORT_PREVIEW_BUSY',status=429)
        try:
            service=app.extensions['tmdb_connection'];payloads=[]
            for tid in ids:
                raw=service._get(g.import_owner,g.import_binding,'movie/'+str(tid),dict(language='zh-CN',append_to_response='credits'))
                if raw.get('id')!=tid:raise ImportRejected('UPSTREAM_IDENTITY_MISMATCH')
                payloads.append(capture(raw))
            return reply(jobs.preview(g.import_owner,payloads,importer.preview(payloads)),status=201)
        finally:slots.release()
    @bp.post('')
    def submit():
        data=body({'preview_id','digest','confirm'})
        if data.get('confirm') is not True:raise ValueError('Confirmation')
        result=jobs.approve(data.get('preview_id'),g.import_owner,data.get('digest'))
        jobs.start();return reply(result,status=202)
    @bp.get('/<job_id>')
    def status(job_id):return reply(jobs.get(job_id,g.import_owner))
    @bp.post('/<job_id>/reconcile')
    def reconcile(job_id):body(set());return reply(jobs.reconcile(job_id,g.import_owner))
    @bp.post('/<job_id>/cancel')
    def cancel(job_id):body(set());return reply(jobs.cancel(job_id,g.import_owner))
    app.register_blueprint(bp)
    if not app.config.get('TESTING'):jobs.start()
