"""M5 actual capture replay plus clearly labeled synthetic negative controls."""
import copy
import hashlib
import json
import pathlib
import sqlite3
import sys
from contextlib import closing
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from catalog.data_pipeline import canonical
from catalog.tmdb_local import adapt, prepare
from catalog.local_catalog_store import LocalCatalogStore
from catalog.local_catalog_server import records_from_capture, create_local_app


def main(output, capture):
    out = pathlib.Path(output).resolve()
    if not out.is_dir() or out.drive.upper() == 'C:':
        raise ValueError('Non-C output required')
    records = records_from_capture(capture)
    checks = []
    def check(name, ok):
        checks.append({'case': name, 'pass': bool(ok)})
        if not ok:
            raise RuntimeError(name)
    def rejected(fn):
        try:
            fn()
        except (ValueError, sqlite3.DatabaseError, FileExistsError):
            return True
        return False
    counter = 0
    def store():
        nonlocal counter
        counter += 1
        return LocalCatalogStore(out / f'control-{counter}.sqlite3', initialize=True)
    def modified(base, **changes):
        # Never publish these mutated inputs: only independent control-* DBs.
        raw = json.loads(base[0]);raw.update(changes)
        data = canonical(raw).encode()
        evidence = copy.deepcopy(base[1]);evidence['sha256'] = hashlib.sha256(data).hexdigest()
        evidence['url'] = f'https://api.themoviedb.org/3/movie/{raw["id"]}?language=zh-CN'
        return data, evidence
    first = adapt(*records[0])
    check('actual captures have official source hashes', all(adapt(*r)['source'] == 'tmdb' for r in records))
    check('raw precision preserved and normalized to 2 decimals', first['raw']['vote_average'] == 8.735 and first['rating_facts'][0]['score_value'] == '8.74')
    actual = LocalCatalogStore(out / 'actual-real-catalog.sqlite3', initialize=True)
    imported = actual.import_batch(records)
    check('real sample imported', imported['inserted'] == len(records) and not imported['issues'])
    before = actual.summary()
    check('same batch replay', actual.import_batch(records)['replayed'])
    check('reverse batch replay', actual.import_batch(list(reversed(records)))['replayed'])
    check('replay unchanged IDs and data', actual.summary() == before)
    reopened = LocalCatalogStore(actual.path)
    check('persistent stable registration', reopened.summary() == before)
    extra = modified(records[0], id=900001, imdb_id='tt9000001', title='SYNTHETIC CONTROL ONLY', original_title='SYNTHETIC CONTROL ONLY')
    control = store();control.import_batch(records)
    previous_ids = {r['source_id']: r['movie_id'] for r in control.summary()['records']}
    control.import_batch([extra])
    check('append preserves existing IDs', all(previous_ids.get(r['source_id'], r['movie_id']) == r['movie_id'] for r in control.summary()['records']))
    check('append creates only one', len(control.summary()['movies']) == len(records) + 1)
    changed = modified(records[0], title='CONFLICT SYNTHETIC TITLE')
    report = control.import_batch([changed])
    check('cross-batch source change reviewed', report['inserted'] == 0 and report['issues'][0]['reason'] == 'SOURCE_CHANGED_REVIEW_REQUIRED')
    rows, issues = prepare([records[0], changed])
    check('batch same ID conflict quarantines both', not rows and len(issues) == 2)
    changed_votes = modified(records[0], vote_count=99999)
    check('same identity different raw in batch quarantined', not prepare([records[0], changed_votes])[0])
    reversed_control = store()
    report1 = reversed_control.import_batch(list(reversed([records[0], changed])))
    check('conflict result order independent', report1['inserted'] == 0 and len(report1['issues']) == 2)
    cross = modified(records[1], imdb_id=first['external_ids']['imdb'])
    report = control.import_batch([cross])
    check('cross-source identifier conflict reviewed', report['inserted'] == 0 and bool(report['issues']))
    report = store().import_batch([records[0], cross])
    check('batch cross-source conflict isolates all', report['inserted'] == 0 and len(report['issues']) == 2)
    same_name = modified(records[0], id=900002, imdb_id=None)
    report = control.import_batch([same_name])
    check('title year alone never merges', report['inserted'] == 0 and report['issues'][0]['reason'] == 'TITLE_YEAR_CANDIDATE_ONLY')
    report = store().import_batch([records[0], records[0]])
    check('batch exact duplicate', report['inserted'] == 1 and report['duplicate']==1 and report['issues'][0]['status'] == 'duplicate')
    pretty=json.dumps(json.loads(records[0][0]),ensure_ascii=False,indent=2).encode('utf-8')
    evidence=dict(records[0][1],sha256=hashlib.sha256(pretty).hexdigest())
    equivalent=actual.import_batch([(pretty,evidence)])
    check('canonical-equivalent formatting cross-batch is idempotent',equivalent['duplicate']==1 and equivalent['this_attempt_inserted']==0 and actual.summary()==before)
    sparse = modified(records[0], release_date='', runtime='', original_language='', original_title='', overview='', genres=[], vote_average=None, vote_count=None, poster_path=None, imdb_id=None)
    sparse_row = adapt(*sparse)
    check('optional empty fields NULL', all(sparse_row[k] is None for k in ('release_date','release_year','runtime_minutes','original_language','original_title','overview')))
    check('no invented score or poster', sparse_row['rating_facts'] == [] and not sparse_row['poster_path_present'])
    for changes in [dict(title=''),dict(id=True),dict(runtime=-1),dict(media_type='tv'),dict(vote_average=11),dict(vote_count=-1),dict(imdb_id='bad'),dict(release_date='bad')]:
        check('invalid derivative rejected ' + canonical(changes), rejected(lambda changes=changes: adapt(*modified(records[0], **changes))))
    bad_evidence = copy.deepcopy(records[0][1]);bad_evidence['sha256'] = '0' * 64
    check('raw hash mismatch', rejected(lambda: adapt(records[0][0], bad_evidence)))
    for key, value in [('url','https://api.themoviedb.org/3/tv/278?language=zh-CN'),('license_ref','unknown'),('validated',False),('acquired_utc','2026-11-05T00:00:00')]:
        bad = dict(records[0][1]);bad[key] = value
        check('evidence fail closed '+key, rejected(lambda bad=bad: adapt(records[0][0],bad)))
    rollback = store();prior = rollback.summary()
    def interrupt(db):
        raise KeyboardInterrupt('Synthetic commit interruption')
    try:
        rollback.import_batch(records, before_commit=interrupt)
    except KeyboardInterrupt:
        pass
    check('interrupted batch rollback all projections and registry', rollback.summary() == prior)
    check('interrupted batch can retry', rollback.import_batch(records)['inserted'] == len(records))
    with closing(rollback.connect(readonly=True)) as db:
        check('SQLite integrity', db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok')
        check('source registry referential integrity', db.execute('PRAGMA foreign_key_check').fetchall() == [])
    check('exclusive initialization', rejected(lambda: LocalCatalogStore(actual.path,initialize=True)))
    foreign = out/'not-ours.sqlite3';foreign.write_bytes(b'NOT A DATABASE')
    check('foreign storage refused untouched', rejected(lambda: LocalCatalogStore(foreign)) and foreign.read_bytes()==b'NOT A DATABASE')
    missing = out/'missing.sqlite3'
    check('no implicit creation', rejected(lambda: LocalCatalogStore(missing)) and not missing.exists())
    check('C path denied before creation', rejected(lambda: LocalCatalogStore('C:/m5-prohibited.sqlite3',initialize=True)))
    with closing(actual.connect(readonly=True)) as db:
        check('rating facts trace to original source hash', db.execute('SELECT COUNT(*) FROM movie_ratings_summary r JOIN movie_source_records s ON r.selected_source_record_pk=s.id AND r.movie_id=s.movie_id JOIN m5_records m ON m.movie_id=s.movie_id AND m.raw_hash=s.payload_hash').fetchone()[0]==len(records))
    from catalog.recommendation import recommend
    candidates=[dict(m,catalog_status='active',genres=[]) for m in before['movies']]
    check('internal IDs compatible with pure recommendation contract', {x['movie_id'] for x in recommend(candidates,{},limit=2)['items']}=={m['movie_id'] for m in candidates})
    # No network calls: test collector transport contract using actual raw JSON,
    # a fake token and mocked process results in isolated output directories.
    from unittest.mock import patch
    from types import SimpleNamespace
    from catalog.tmdb_capture import capture as collect
    collector_dir=out/'capture-control';collector_dir.mkdir()
    fake_token='SYNTHETIC-NOT-A-REAL-SECRET'
    with patch('catalog.tmdb_capture.shutil.which',return_value='curl.exe'), patch('catalog.tmdb_capture.getproxies',return_value={'https':'http://127.0.0.1:7897'}), patch('catalog.tmdb_capture.time.sleep'), patch('catalog.tmdb_capture.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=records[0][0]+b'\n200')) as process:
        captured=collect(collector_dir,[int(first['source_record_id'])],fake_token)
        check('collector validates exact raw bytes',captured[0]['validated'] and (collector_dir/'raw'/('tmdb-'+first['source_record_id']+'.json')).read_bytes()==records[0][0])
        check('collector secrets absent from argv',fake_token not in str(process.call_args.args))
    check('collector secrets absent from output',all(fake_token.encode() not in f.read_bytes() for f in collector_dir.rglob('*') if f.is_file()))
    failure_dir=out/'capture-error-control';failure_dir.mkdir()
    with patch('catalog.tmdb_capture.shutil.which',return_value='curl.exe'), patch('catalog.tmdb_capture.getproxies',return_value={}), patch('catalog.tmdb_capture.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=b'ERROR BODY\n429')) as process:
        failed=collect(failure_dir,[278,27205],fake_token)
        check('collector stops on rate limit without retry',len(failed)==1 and not failed[0]['validated'] and process.call_count==1)
        check('collector never archives failed response body',list((failure_dir/'raw').iterdir())==[])
    app=create_local_app(actual.path);client=app.test_client()
    response=client.get('/api/catalog/movies?sort=id');check('API real list',response.status_code==200 and len(response.json['data']['items'])==2)
    target=response.json['data']['items'][0]
    detail=client.get('/api/catalog/movies/'+str(target['movie_id'])).json['data']
    check('API detail real source ratings', detail['ratings'][0]['source']=='tmdb' and bool(detail['genres']))
    check('API actual title search',len(client.get('/api/catalog/movies?keyword='+target['title']).json['data']['items'])==1)
    lookup=client.get('/api/catalog/lookup?source=tmdb&external_id=278')
    check('API exact external ID',lookup.status_code==200 and lookup.json['data']['title']=='肖申克的救赎')
    check('API unprovided douban not invented',client.get('/api/catalog/lookup?source=douban&external_id=278').status_code==404)
    check('API no synthesis fallback',client.get('/api/catalog/demo-info').json['data_source']=='LOCAL AUTHORIZED / TMDB')
    for route, method in [('behavior','get'),('behavior','post'),('recommendations','get')]:
        check('public demo cannot access real behaviors '+route+method,getattr(client,method)('/api/catalog/'+route,headers={'X-Demo-User':'demo-1'}).status_code==403)
    check('write catalog method denied',client.post('/api/catalog/movies').status_code==405)
    summary=actual.summary()
    quality={'actual_movie_count':len(summary['movies']),'sources':{'tmdb':len(records)},'missing_fields':{k:sum(m[k] is None for m in summary['movies']) for k in ('release_year','runtime_minutes','original_language','overview')},'poster_paths_in_source':sum(adapt(*r)['poster_path_present'] for r in records),'posters_downloaded':0,'synthetic_controls_never_in_actual_DB':True,'raw_captures':[{k:e[k] for k in ('tmdb_id','url','acquired_utc','sha256')} for _,e in records],'import':imported,'summary':summary}
    (out/'m5-check-results.json').write_text(json.dumps({'checks':checks,'scope':'2 real captures plus separately stored synthetic derivative controls'},ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'real-quality-and-bindings.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2),encoding='utf-8')
    print('M5 checks',len(checks),'PASS; actual movies',len(summary['movies']))


if __name__=='__main__':
    main(*sys.argv[1:])
