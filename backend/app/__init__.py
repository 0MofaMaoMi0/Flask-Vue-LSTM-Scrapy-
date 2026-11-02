import os
from flask import Flask, jsonify
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

def create_app(overrides=None):
    app = Flask(__name__)
    app.config.update(DATABASE_URL=os.environ.get('MI2_DATABASE_URL', ''), TESTING=False,
        ALLOWED_ORIGINS=os.environ.get('MI2_ALLOWED_ORIGINS', 'http://127.0.0.1:5174').split(','),
        COOKIE_SECURE=os.environ.get('MI2_COOKIE_SECURE', 'false').lower() == 'true',
        IDLE_SECONDS=int(os.environ.get('MI2_IDLE_SECONDS', '7200')),
        ABSOLUTE_SECONDS=int(os.environ.get('MI2_ABSOLUTE_SECONDS', '86400')),
        RATE_LIMIT=10, RATE_WINDOW=int(os.environ.get('MI2_RATE_WINDOW','300')), CSRF_RATE_LIMIT=60, MAX_CONTENT_LENGTH=8192)
    if overrides: app.config.update(overrides)
    url = make_url(app.config['DATABASE_URL'])
    expected = 'mi2_auth_test' if app.config['TESTING'] else 'mi2_auth_dev'
    if url.drivername != 'mysql+pymysql' or url.database != expected or url.host not in ('localhost','127.0.0.1','::1'):
        raise RuntimeError('Authentication database isolation check failed')
    if not app.config['COOKIE_SECURE'] and any(not x.startswith(('http://127.0.0.1:', 'http://localhost:')) for x in app.config['ALLOWED_ORIGINS']):
        raise RuntimeError('Insecure cookies allowed only on local development origins')
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    app.extensions['auth_engine'] = engine
    app.extensions['auth_db'] = sessionmaker(engine, expire_on_commit=False)
    from .auth import bp
    app.register_blueprint(bp, url_prefix='/api/v1/auth')
    @app.get('/api/v1/health')
    def health(): return jsonify(code=0, message='ok', data={'status':'ok'})
    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
    from werkzeug.exceptions import HTTPException
    @app.errorhandler(Exception)
    def errors(error):
        if isinstance(error, HTTPException):
            return jsonify(code=40010, message='request rejected', data=None), error.code
        # Never log request bodies, SQL parameters or exception text containing credentials.
        app.logger.error('Authentication request failed: %s', type(error).__name__)
        return jsonify(code=50010, message='internal error', data=None), 500
    return app
