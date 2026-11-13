import os
from flask import Flask, jsonify
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

def validate_disposable(url, config):
    import re
    if config.get('TESTING') is not True or config.get('DISPOSABLE_SANDBOX') is not True:
        raise RuntimeError('Explicit sandbox mode required')
    schema=config.get('DISPOSABLE_SCHEMA')
    host=config.get('DISPOSABLE_HOST');port=config.get('DISPOSABLE_PORT')
    user=config.get('DISPOSABLE_USER')
    if not isinstance(schema,str) or re.fullmatch(r'mi2_auth_r2_[a-z0-9]{8,24}',schema) is None:
        raise RuntimeError('Approved full sandbox name required')
    if host not in ('localhost','127.0.0.1','::1') or type(port) is not int or not 1<=port<=65535 or not isinstance(user,str) or not user:
        raise RuntimeError('Approved endpoint and user required')
    if url.drivername!='mysql+pymysql' or url.database!=schema or url.host!=host or url.port!=port or url.username!=user or not url.password or url.query:
        raise RuntimeError('Sandbox contract mismatch')
    return 'CANDIDATE'

def create_app(overrides=None):
    app = Flask(__name__)
    app.config.update(DATABASE_URL=os.environ.get('MI2_DATABASE_URL', ''), TESTING=False,
        ALLOWED_ORIGINS=os.environ.get('MI2_ALLOWED_ORIGINS', 'http://127.0.0.1:5174').split(','),
        COOKIE_SECURE=os.environ.get('MI2_COOKIE_SECURE', 'false').lower() == 'true',
        IDLE_SECONDS=int(os.environ.get('MI2_IDLE_SECONDS', '7200')),
        ABSOLUTE_SECONDS=int(os.environ.get('MI2_ABSOLUTE_SECONDS', '86400')),
        RATE_LIMIT=10, RATE_WINDOW=int(os.environ.get('MI2_RATE_WINDOW','300')), CSRF_RATE_LIMIT=60, MAX_CONTENT_LENGTH=8192,
        ADMIN_USERS=[x.strip() for x in os.environ.get('MI2_ADMIN_USERS','zhang').split(',') if x.strip()],
        MEDIA_CACHE_DIR=os.environ.get('MI2_MEDIA_CACHE','/tmp/mi2-media'),
        TRENDING_CACHE_DIR=os.environ.get('MI2_TRENDING_CACHE','/var/lib/movieinsight-m6-realcopy/appdata/trending'))
    if overrides: app.config.update(overrides)
    from .m6_database import environment_config, validate_m6_url, verify_m6_connection
    for key, value in environment_config(os.environ).items():
        app.config.setdefault(key, value)
    url = make_url(app.config['DATABASE_URL'])
    if app.config.get('M6_ISOLATED') is True:
        validate_m6_url(url, app.config)
        app.config.update(AUTH_COOKIE_NAME='mi2_m6_sid', AUTH_COOKIE_PATH='/api')
    elif app.config.get('TESTING') is True and app.config.get('DISPOSABLE_SANDBOX') is True:
        validate_disposable(url,app.config)
    else:
        expected = 'mi2_auth_test' if app.config['TESTING'] else 'mi2_auth_dev'
        if url.drivername != 'mysql+pymysql' or url.database != expected or url.host not in ('localhost','127.0.0.1','::1'):
            raise RuntimeError('Authentication database isolation check failed')
    if not app.config['COOKIE_SECURE'] and any(not x.startswith(('http://127.0.0.1:', 'http://localhost:')) for x in app.config['ALLOWED_ORIGINS']):
        raise RuntimeError('Insecure cookies allowed only on local development origins')
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    if app.config.get('M6_ISOLATED') is True:
        with engine.connect() as connection:
            verify_m6_connection(connection, app.config)
    app.extensions['auth_engine'] = engine
    app.extensions['auth_db'] = sessionmaker(engine, expire_on_commit=False)
    from .auth import bp
    app.register_blueprint(bp, url_prefix='/api/v1/auth')
    if app.config.get('M6_ISOLATED') is True:
        from catalog.m6_business import install_m6_business
        install_m6_business(app)
        app.config.setdefault("M7_DATABASE_URL", os.environ.get("MI2_M7_DATABASE_URL", ""))
        from assistant.credential_store import install as install_credentials
        install_credentials(app)
        from catalog.tmdb_api import install as install_tmdb
        install_tmdb(app)
        from assistant.api_v2 import install as install_assistant
        install_assistant(app)
        from assistant.account_api import install as install_account
        install_account(app)
        from analytics.api import install as install_analytics
        install_analytics(app)
        from admin_api import install as install_admin
        install_admin(app)
        from media_api import install as install_media
        install_media(app)
        from catalog.recommendation_feedback import install as install_feedback
        install_feedback(app)
    @app.get('/api/v1/health')
    def health(): return jsonify(code=0, message='ok', data={'status':'ok'})
    if app.config.get('M6_ISOLATED') is True:
        @app.get('/api/v1/ready')
        def ready():
            try:
                with engine.connect() as connection:
                    verify_m6_connection(connection, app.config)
                credentials=app.extensions.get('credential_store')
                if credentials:
                    from sqlalchemy import select
                    with credentials.engine.connect() as connection:connection.execute(select(credentials.table.c.user_id).limit(0))
                return jsonify(code=0, message='ok', data={'status':'ready'})
            except Exception:
                return jsonify(code=50310, message='database unavailable', data=None), 503
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

