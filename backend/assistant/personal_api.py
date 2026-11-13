"""Own-account profile, notes and answer feedback. No arbitrary uploads or SQL."""
from flask import Blueprint,request,jsonify,g
from sqlalchemy.exc import SQLAlchemyError
from app.auth import session_row,csrf_ok
from .personal_features import PersonalFeatures,FeatureConflict,FeatureNotFound

def install(app):
 service=PersonalFeatures(app.extensions['m7_engine'],app.extensions['auth_engine'])
 # Feature absence must not break login during a rolling deployment.
 try:service.ready();enabled=True
 except SQLAlchemyError:enabled=False
 app.extensions['personal_features']=service if enabled else None
 # Always include these tables in erasure. A transient readiness failure must
 # never let account closure succeed while private rows remain undeleted.
 app.extensions['credential_store'].personal_tables=service.t
 bp=Blueprint('personal_features',__name__,url_prefix='/api/v1/account')
 def reply(data=None,code='OK',status=200):return jsonify(code=code,data=data),status
 @bp.before_request
 def guard():
  with app.extensions['auth_db'].begin() as db:
   row=session_row(db)
   if not row or not row.user_id:return reply(code='AUTHENTICATION_REQUIRED',status=401)
   if request.method!='GET' and not csrf_ok(row):return reply(code='REQUEST_VERIFICATION_FAILED',status=403)
   g.personal_user=row.user_id
  if not enabled:return reply(code='PERSONAL_FEATURES_NOT_INSTALLED',status=503)
  request.max_content_length=100000
 def body(keys):
  data=request.get_json(silent=True)
  if request.args or type(data) is not dict or set(data)!=set(keys):raise ValueError('Input')
  return data
 @bp.errorhandler(Exception)
 def failure(e):
  from werkzeug.exceptions import HTTPException
  if isinstance(e,HTTPException):return reply(code='REQUEST_REJECTED',status=e.code)
  if isinstance(e,FeatureConflict):return reply(code=str(e),status=409)
  if isinstance(e,FeatureNotFound):return reply(code=str(e),status=404)
  if isinstance(e,PermissionError):return reply(code='AUTHENTICATION_REQUIRED',status=401)
  if isinstance(e,(ValueError,TypeError,KeyError)):return reply(code='INVALID_INPUT',status=400)
  app.logger.error('Personal feature failure: %s',type(e).__name__);return reply(code='PERSONAL_FEATURES_UNAVAILABLE',status=503)
 @bp.get('/profile')
 def profile():
  if request.args:raise ValueError()
  return reply(service.profile(g.personal_user))
 @bp.post('/profile')
 def avatar():
  b=body({'avatar','version'});return reply(service.avatar(g.personal_user,b['avatar'],b['version']))
 @bp.get('/movie-notes')
 def notes():
  if request.args:raise ValueError()
  return reply(dict(items=service.notes(g.personal_user)))
 @bp.post('/movie-notes')
 def note():
  b=body({'movie_id','note','version'});return reply(service.note(g.personal_user,b['movie_id'],b['note'],b['version']))
 @bp.get('/assistant-feedback')
 def read_feedback():
  if set(request.args)!={'request_id'} or len(request.args.getlist('request_id'))!=1:raise ValueError()
  return reply(service.feedback(g.personal_user,request.args['request_id']))
 @bp.post('/assistant-feedback')
 def feedback():
  b=body({'request_id','reaction'})
  if b['reaction'] not in ('up','down',None):raise ValueError()
  return reply(service.feedback(g.personal_user,b['request_id'],b['reaction']))
 app.register_blueprint(bp)
