"""Explicit account closure, retaining original login/password/CSRF mechanisms."""
from flask import Blueprint,request,jsonify
from app.auth import session_row,csrf_ok,limiter
from .credential_store import CredentialAccessDenied,CredentialError

def install(app):
    bp=Blueprint('account_credentials',__name__,url_prefix='/api/v1/account')
    @bp.post('/close')
    def close():
        with app.extensions['auth_db'].begin() as db:
            row=session_row(db)
            if not row or row.user_id is None:return jsonify(code='AUTHENTICATION_REQUIRED',data=None),401
            if not csrf_ok(row):return jsonify(code='REQUEST_VERIFICATION_FAILED',data=None),403
            user,binding=row.user_id,row.sid_hash
        limited=limiter('account-close',str(user))
        if limited:return limited
        body=request.get_json(silent=True)
        if request.args or type(body) is not dict or set(body)!={'password','confirmation','understand'} or body['understand'] is not True:return jsonify(code='INVALID_INPUT',data=None),400
        store=app.extensions.get('credential_store')
        if not store:return jsonify(code='PERSISTENCE_UNAVAILABLE',data=None),503
        try:store.close_account(user,binding,body['password'],body['confirmation'])
        except CredentialAccessDenied:return jsonify(code='CURRENT_PASSWORD_REJECTED',data=None),403
        except ValueError:return jsonify(code='ACCOUNT_CONFIRMATION_REQUIRED',data=None),400
        except CredentialError:return jsonify(code='CREDENTIALS_UNAVAILABLE',data=None),503
        vault=app.extensions.get('m7_vault');tmdb=app.extensions.get('tmdb_connection');agent=app.extensions.get('m7_agent')
        if vault:vault.clear_user(user)
        if tmdb:tmdb.clear_user(user)
        if agent:
            try:agent.cancel_account(user)
            except Exception:app.logger.error('Closed account request cancellation incomplete')
        response=jsonify(code='OK',data={'closed':True,'credentials_removed':True,'sessions_revoked':True})
        response.delete_cookie(app.config.get('AUTH_COOKIE_NAME','mi2_m6_sid'),path=app.config.get('AUTH_COOKIE_PATH','/api'),httponly=True,samesite='Lax',secure=app.config['COOKIE_SECURE'])
        return response
    @app.errorhandler(CredentialAccessDenied)
    def forbidden(error):return jsonify(code='AUTHENTICATION_REQUIRED',data=None),401
    @app.errorhandler(CredentialError)
    def unavailable(error):return jsonify(code='CREDENTIALS_UNAVAILABLE',data=None),503
    app.register_blueprint(bp)
