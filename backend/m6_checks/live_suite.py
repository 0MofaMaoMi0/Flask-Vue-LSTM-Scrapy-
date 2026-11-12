"""New M6 synthetic identities only; does not import legacy pytest policy."""
import json
from pathlib import Path
import secrets
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from app import create_app
from app.auth import ph, hashed
from app.models import User, AuthSession


def main():
    app = create_app({'TESTING': True})
    if app.config.get('M6_ISOLATED') is not True:
        raise RuntimeError('M6-only live suite')
    results = []
    cookie_name = app.config['AUTH_COOKIE_NAME']
    cookie_path = app.config['AUTH_COOKIE_PATH']
    origin = app.config['ALLOWED_ORIGINS'][0]
    password = secrets.token_urlsafe(24)
    counter = iter(range(1, 1000))
    def check(name, passed):
        results.append({'name': name, 'passed': bool(passed)})
        if not passed:
            raise AssertionError(name)
    from schema_checks import run_schema_checks
    run_schema_checks(app,check)
    def client():
        c = app.test_client()
        c.environ_base['REMOTE_ADDR'] = '198.18.' + str(secrets.randbelow(250)) + '.' + str(next(counter) % 250 + 1)
        return c
    def token(c):
        response = c.get('/api/v1/auth/csrf')
        if response.status_code != 200:
            raise AssertionError('CSRF endpoint')
        return response.json['data']['csrf_token']
    def post(c, action, data, **headers):
        return c.post('/api/v1/auth/' + action, json=data,
                      headers={'Origin': origin, 'X-CSRF-Token': token(c), **headers})
    name = 'm6_' + secrets.token_hex(8)
    c = client()
    check('register_mysql', post(c, 'register', {'username': name, 'password': password}).status_code == 201)
    with app.extensions['auth_db']() as db:
        user = db.scalar(select(User).where(User.username_normalized == name))
        check('argon2_hash_in_mysql', bool(user and user.password_hash.startswith('$argon2id$') and ph.verify(user.password_hash, password)))
    check('duplicate_normalized_identity', post(client(), 'register', {'username': name.upper(), 'password': password}).status_code == 409)
    check('unicode_casefold_storage_bound',post(client(),'register',{'username':'\u0390'*32,'password':password}).status_code==400)
    missing = c.post('/api/v1/auth/login', json={'username': name, 'password': password}, headers={'Origin': origin})
    check('missing_csrf_denied', missing.status_code == 403)
    check('cross_origin_denied', post(c, 'login', {'username': name, 'password': password}, Origin='https://invalid.example').status_code == 403)
    check('untrusted_user_id_denied', post(c, 'register', {'username': name, 'password': password, 'user_id': 1}).status_code == 400)
    bad = post(c, 'login', {'username': name, 'password': password + 'wrong'})
    absent = post(c, 'login', {'username': 'm6_absent_' + secrets.token_hex(5), 'password': password})
    check('uniform_authentication_failure', bad.status_code == absent.status_code == 401 and bad.json == absent.json)
    old = c.get_cookie(cookie_name, path=cookie_path).value
    response = post(c, 'login', {'username': name, 'password': password})
    check('login_mysql', response.status_code == 200)
    new = c.get_cookie(cookie_name, path=cookie_path).value
    check('session_rotation', new != old)
    replay = client(); replay.set_cookie(cookie_name, old, path=cookie_path)
    check('rotated_session_rejected', replay.get('/api/v1/auth/me').status_code == 401)
    check('authenticated_me', c.get('/api/v1/auth/me').json['data']['username'] == name)
    restored = client(); restored.set_cookie(cookie_name, new, path=cookie_path)
    second_app = create_app({'TESTING': True})
    fresh = second_app.test_client(); fresh.set_cookie(cookie_name, new, path=cookie_path)
    check('session_persists_across_app_instances', fresh.get('/api/v1/auth/me').status_code == 200)
    check('cookie_security', all(value in response.headers.get('Set-Cookie', '') for value in ['HttpOnly', 'SameSite=Lax', 'Path='+cookie_path]))
    check('logout', post(c, 'logout', {}).status_code == 200)
    check('logout_revokes_persistent_session', restored.get('/api/v1/auth/me').status_code == 401)
    check('login_again', post(c, 'login', {'username': name, 'password': password}).status_code == 200)
    sid = c.get_cookie(cookie_name, path=cookie_path).value
    with app.extensions['auth_db'].begin() as db:
        db.execute(update(AuthSession).where(AuthSession.sid_hash == hashed(sid)).values(idle_expires_at=datetime.utcnow() - timedelta(seconds=1)))
    check('expired_session_rejected', c.get('/api/v1/auth/me').status_code == 401)
    same = 'm6_race_' + secrets.token_hex(6)
    barrier = threading.Barrier(2)
    def race(_):
        c = client(); csrf = token(c); barrier.wait(timeout=10)
        return c.post('/api/v1/auth/register', json={'username': same, 'password': password}, headers={'Origin': origin, 'X-CSRF-Token': csrf}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(race, range(2)))
    check('concurrent_duplicate_registration', statuses == [201, 409])
    with app.extensions['auth_db']() as db:
        check('exactly_one_concurrent_identity', len(db.scalars(select(User).where(User.username_normalized == same)).all()) == 1)
    with app.extensions['auth_engine'].connect() as connection:
        grants = '\n'.join(row[0] for row in connection.execute(text('SHOW GRANTS')))
        check('application_no_global_admin', all(word not in grants.upper() for word in ['GRANT OPTION', ' FILE', ' SUPER', 'CREATE USER']))
        try:
            connection.execute(text('CREATE TABLE m6_permission_canary(id INT)'))
        except DBAPIError as error:
            check('application_ddl_denied', error.orig.args[0] == 1142)
        else:
            check('application_ddl_denied', False)
    second_app.extensions['auth_engine'].dispose()
    from import_checks import run_import_checks, approved_copy_checks
    from app.m6_activity import activity_tables
    receipts=activity_tables(app.config['M6_ACTIVITY_SCHEMA'],app.config['M6_SCHEMA'])['receipts']
    with app.extensions['auth_engine'].connect() as connection:
        previous=connection.execute(select(receipts.c.receipt)).scalar_one_or_none()
    source_kind=previous.get('source_kind') if previous else 'synthetic'
    receipt=approved_copy_checks(app,check) if source_kind=='approved_static_copy' else run_import_checks(app,check)
    catalog = c.get('/api/catalog/movies?limit=6')
    check('mysql_catalog_list', catalog.status_code == 200 and len(catalog.json['data']['items']) == 2)
    mid = catalog.json['data']['items'][0]['movie_id']
    check('mysql_catalog_detail', c.get('/api/catalog/movies/'+str(mid)).status_code == 200)
    from urllib.parse import quote
    keyword=quote(catalog.json['data']['items'][0]['title'])
    check('mysql_catalog_search', len(c.get('/api/catalog/movies?keyword='+keyword).json['data']['items']) == 1)
    imdb=next(mapping['external_ids']['imdb'] for mapping in receipt['mappings']['movies'] if 'imdb' in mapping['external_ids'])
    check('mysql_catalog_imdb_lookup', c.get('/api/catalog/lookup?source=imdb&external_id='+quote(imdb)).status_code == 200)
    anonymous = client()
    check('behavior_requires_real_session', anonymous.get('/api/catalog/behavior', headers={'X-Demo-User':'demo-1'}).status_code == 401)
    b = client()
    check('business_login', post(b, 'login', {'username':name,'password':password}).status_code == 200)
    event = {'event_id':'m6_'+secrets.token_hex(12),'movie_id':mid,'kind':'favorite','action':'set','value':None,'time':datetime.utcnow().isoformat()+'+00:00','source':'USER_ACTION'}
    check('behavior_csrf_required', b.post('/api/catalog/behavior',json=event,headers={'Origin':origin}).status_code == 403)
    def behavior_post(client,event,csrf=None):
        return client.post('/api/catalog/behavior',json=event,headers={'Origin':origin,'X-CSRF-Token':csrf or token(client)})
    response = behavior_post(b,event)
    check('mysql_behavior_write', response.status_code == 200 and response.json['data']['duplicate'] is False)
    check('mysql_behavior_idempotency', behavior_post(b,event).json['data']['duplicate'] is True)
    check('mysql_behavior_conflict', behavior_post(b,dict(event,kind='want')).status_code == 409)
    check('behavior_identity_injection_rejected', behavior_post(b,dict(event,event_id='m6_'+secrets.token_hex(12),user_id=1)).status_code == 400)
    state = b.get('/api/catalog/behavior').json['data']['state']
    check('mysql_behavior_read', state[str(mid)]['favorite'] is True)
    observer = create_app({'TESTING':True}).test_client()
    observer.set_cookie(cookie_name,b.get_cookie(cookie_name,path=cookie_path).value,path=cookie_path)
    check('behavior_persists_across_app_instances', observer.get('/api/catalog/behavior').json['data']['state'] == state)
    other = client();other_name='m6_other_'+secrets.token_hex(6)
    check('second_user_registered',post(other,'register',{'username':other_name,'password':password}).status_code == 201)
    check('second_user_login',post(other,'login',{'username':other_name,'password':password}).status_code == 200)
    check('behavior_users_isolated',other.get('/api/catalog/behavior?user_id='+str(user.id)).json['data']['state'] == {})
    csrf = token(b);sid=b.get_cookie(cookie_name,path=cookie_path).value
    def concurrent_behavior(kind):
        client=app.test_client();client.set_cookie(cookie_name,sid,path=cookie_path)
        return behavior_post(client,dict(event,event_id='m6_'+secrets.token_hex(12),kind=kind),csrf).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=sorted(pool.map(concurrent_behavior,['want','watched']))
    state=b.get('/api/catalog/behavior').json['data']['state'][str(mid)]
    check('concurrent_behavior_invariants',statuses in ([200,200],[200,409]) and state.get('watched') and not state.get('want'))
    recommendation=b.get('/api/catalog/recommendations?limit=6')
    check('mysql_authenticated_recommendation',recommendation.status_code==200 and recommendation.json['data']['mode']=='MYSQL_AUTHENTICATED_RECOMMENDATION' and recommendation.json['data']['paid_model_tokens']==0)
    check('recommendation_excludes_watched',all(row['movie_id']!=mid for row in recommendation.json['data']['items']))
    rate_app=create_app({'TESTING':True,'RATE_LIMIT':2})
    limited=rate_app.test_client();limited.environ_base['REMOTE_ADDR']='198.19.0.'+str(secrets.randbelow(250)+1)
    probe='m6_rate_'+secrets.token_hex(6)
    statuses=[post(limited,'login',{'username':probe,'password':password}).status_code for _ in range(3)]
    check('mysql_rate_limit',statuses==[401,401,429])
    rate_app.extensions['auth_engine'].dispose()
    app.extensions['auth_engine'].dispose()
    print(json.dumps({'suite': 'M6_REAL_MYSQL_AUTH_CATALOG', 'data_source':source_kind,'all_pass': True, 'checks': results}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # The privileged launcher suppresses failed command output to avoid
        # credential leaks. Report safe JSON instead: all_pass, not process
        # execution success, is the test verdict.
        import traceback
        locations=[{'function':frame.name,'line':frame.lineno,'file':Path(frame.filename).name}
                   for frame in traceback.extract_tb(error.__traceback__)]
        print(json.dumps({'all_pass':False,'error_type':type(error).__name__,
                          'failed_check':str(error) if isinstance(error,AssertionError) else None,
                          'locations':locations}))
