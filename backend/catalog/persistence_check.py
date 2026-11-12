"""Standalone M4 synthetic-only checks. Never imports the authentication app."""
import concurrent.futures
import hashlib
import json
import pathlib
import sqlite3
import sys
import uuid
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from flask import Flask
from catalog.persistent_behavior import PersistentBehaviorRepository as Repository
from catalog.platform_contracts import create_formal_blueprint
from catalog.recommendation_api import create_recommendation_blueprint


def main(output):
    out = pathlib.Path(output).resolve()
    if not out.is_dir() or out.drive.upper() == 'C:':
        raise ValueError('Existing non-C output required')
    checks = []
    def check(name, condition):
        checks.append({'case': name, 'pass': bool(condition)})
        if not condition:
            raise RuntimeError(name)
    def rejects(fn):
        try:
            fn()
        except (ValueError, FileExistsError, sqlite3.DatabaseError):
            return True
        return False
    def event(kind='favorite', action='set', value=None, mid=1):
        return dict(event_id=str(uuid.uuid4()), movie_id=mid, kind=kind,
                    action=action, value=value, time='2026-11-05T00:00:00+00:00', source='SYNTHETIC_DEMO')
    path = out / 'owned-m4.sqlite3'
    repo = Repository(path, 'synthetic-dataset-A', initialize=True)
    visible = {1, 2, 3}
    e = event()
    check('initial write', not repo.apply('demo-1', e, visible)['duplicate'])
    reopened = Repository(path, 'synthetic-dataset-A')
    check('restart replay', reopened.snapshot('demo-1') == {'1': {'favorite': True}})
    check('restart duplicate', reopened.apply('demo-1', e, visible)['duplicate'])
    check('cross-user conflict', rejects(lambda: reopened.apply('demo-2', e, visible)))
    check('cross-user isolation', reopened.snapshot('demo-2') == {})
    check('payload conflict', rejects(lambda: reopened.apply('demo-1', dict(e, kind='want'), visible)))
    for kind, value in [('want', None), ('rating', 4.5), ('watched', None), ('not_interested', None)]:
        reopened.apply('demo-1', event(kind, value=value), visible)
        check('persistent ' + kind, kind in Repository(path, 'synthetic-dataset-A').snapshot('demo-1')['1'])
    check('negative clears positive', reopened.snapshot('demo-1')['1'] == {'watched': True, 'not_interested': True})
    check('positive conflict', rejects(lambda: reopened.apply('demo-1', event(), visible)))
    for kind in ['not_interested', 'watched']:
        reopened.apply('demo-1', event(kind, 'undo'), visible)
        check('persistent undo ' + kind, kind not in Repository(path, 'synthetic-dataset-A').snapshot('demo-1')['1'])
    for value in [0, 5.5, True, float('nan'), 2.2]:
        check('reject rating ' + str(value), rejects(lambda: reopened.apply('demo-1', event('rating', value=value), visible)))
    check('hidden ID', rejects(lambda: reopened.apply('demo-1', event(mid=99), visible)))
    check('spoofed identity', rejects(lambda: reopened.apply('demo-1', dict(event(), user='demo-2'), visible)))
    check('unknown user', rejects(lambda: reopened.snapshot('real-user')))
    before = path.read_bytes()
    check('dataset mismatch denied', rejects(lambda: Repository(path, 'synthetic-dataset-B')))
    check('dataset mismatch untouched', path.read_bytes() == before)
    check('exclusive initialization', rejects(lambda: Repository(path, 'synthetic-dataset-A', initialize=True)))
    check('existing owned file untouched', path.read_bytes() == before)
    foreign = out / 'foreign.sqlite3'
    foreign.write_bytes(b'NOT AN M4 DATABASE')
    check('foreign denied', rejects(lambda: Repository(foreign, 'A')))
    check('foreign untouched', foreign.read_bytes() == b'NOT AN M4 DATABASE')
    missing = out / 'absent.sqlite3'
    check('missing not auto created', rejects(lambda: Repository(missing, 'A')) and not missing.exists())
    check('C target denied', rejects(lambda: Repository('C:/m4-prohibited.sqlite3', 'A', initialize=True)))
    class Broken(Repository):
        def _before_commit(self, db):
            raise RuntimeError('Injected interruption before commit')
    prior = reopened.snapshot('demo-1')
    failed_event = event('favorite', mid=2)
    try:
        Broken(path, 'synthetic-dataset-A').apply('demo-1', failed_event, visible)
    except RuntimeError:
        pass
    check('transaction rollback', Repository(path, 'synthetic-dataset-A').snapshot('demo-1') == prior)
    check('failed event can retry', not reopened.apply('demo-1', failed_event, visible)['duplicate'])
    shared = event('favorite', mid=3)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: Repository(path, 'synthetic-dataset-A').apply('demo-3', shared, visible), range(12)))
    check('independent connections duplicate atomicity', sum(not r['duplicate'] for r in results) == 1)
    with sqlite3.connect(path) as db:
        check('one committed row per event', db.execute('SELECT COUNT(*) FROM m4_events WHERE event_id=?', (shared['event_id'],)).fetchone()[0] == 1)
        check('sqlite integrity', db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok')
    movies = lambda: [dict(movie_id=1, title='SYNTHETIC', catalog_status='active', genres=[])]
    app = Flask('M4-SYNTHETIC-CHECK')
    app.register_blueprint(create_recommendation_blueprint(movies, reopened))
    client = app.test_client()
    headers = {'X-Demo-User': 'demo-2'}
    http_event = event()
    check('HTTP persistent event', client.post('/api/catalog/behavior', headers=headers, json=http_event).status_code == 200)
    check('HTTP duplicate', client.post('/api/catalog/behavior', headers=headers, json=http_event).json['data']['duplicate'])
    check('HTTP cross-user untouched', client.get('/api/catalog/behavior', headers=headers).json['data']['state'] == {'1': {'favorite': True}})
    check('formal missing providers denied', rejects(lambda: create_formal_blueprint()))
    formal = Flask('M4-FORMAL-DENY-CHECK')
    formal.register_blueprint(create_formal_blueprint(movies, reopened))
    for route, method in [('behavior', 'get'), ('recommendations', 'get'), ('behavior', 'post')]:
        response = getattr(formal.test_client(), method)('/api/catalog/' + route, headers=headers, json=http_event if method == 'post' else None)
        check('formal spoofed demo denied ' + route + method, response.status_code == 401)
    # Reuse every original M3 behavior/recommendation test with file-backed stores.
    import catalog.recommendation_check as m3
    counter = 0
    def factory():
        nonlocal counter
        counter += 1
        return Repository(out / ('m3-contract-' + str(counter) + '.sqlite3'), 'M3-synthetic-check', initialize=True)
    m3.BehaviorStore = factory
    m3.main(str(out))
    check('original M3 40 checks with persistent repository', len(json.loads((out / 'recommendation-results.json').read_text())['checks']) == 40)
    (out / 'persistence-results.json').write_text(json.dumps({'checks': checks, 'scope': 'M4 owned synthetic SQLite ONLY', 'database_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}, indent=2), encoding='utf-8')
    print('M4 persistence checks', len(checks), 'PASS; M3 persistent checks 40 PASS')


if __name__ == '__main__':
    main(sys.argv[1])
