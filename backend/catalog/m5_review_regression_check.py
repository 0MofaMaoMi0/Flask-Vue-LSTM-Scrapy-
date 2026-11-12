"""Independent F1-F3 counterexamples. Synthetic bytes only; no network/auth."""
import hashlib
import json
import pathlib
import sys
from contextlib import closing
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from catalog.tmdb_local import adapt, prepare, LICENSE
from catalog.local_catalog_store import LocalCatalogStore
from catalog.data_pipeline import digest


def sample(mid, *, title='SYNTHETIC IDENTICAL TITLE', imdb='tt1234567', genres=None):
    raw = dict(id=mid, title=title, original_title=title, release_date='2000-01-01',
               adult=False, imdb_id=imdb, genres=[{'id': 18, 'name': '剧情'}] if genres is None else genres)
    data = json.dumps(raw, ensure_ascii=False, sort_keys=True).encode('utf-8')
    return data, evidence(mid, data)


def evidence(mid, data):
    return dict(license_ref=LICENSE, http_status='200', validated=True,
                sha256=hashlib.sha256(data).hexdigest(), acquired_utc='2026-11-05T00:00:00+00:00',
                url=f'https://api.themoviedb.org/3/movie/{mid}?language=zh-CN')


def main(output):
    out = pathlib.Path(output).resolve()
    if not out.is_dir() or out.drive.upper() == 'C:':
        raise ValueError('Existing non-C output required')
    checks, receipts = [], []
    def check(name, ok):
        checks.append({'case': name, 'pass': bool(ok)})
        if not ok:
            raise RuntimeError(name)
    counter = 0
    def store():
        nonlocal counter
        counter += 1
        return LocalCatalogStore(out / f'synthetic-review-{counter}.sqlite3', initialize=True)
    def save(name, receipt):
        receipts.append({'scenario': name, 'receipt': receipt})
        return receipt
    a, b = sample(101), sample(102)
    clean = sample(103, title='SYNTHETIC UNDISPUTED', imdb='tt1234568')
    # F1: identical textual identity must not conceal distinct reliable owners.
    for name, inputs in [('forward', [a, b]), ('reverse', [b, a]), ('duplicates', [a, a, b]), ('with valid peer', [a, b, clean])]:
        rows, issues = prepare(inputs)
        check('F1 '+name+' all disputed claims isolated', {r['source_record_id'] for r in rows} == ({'103'} if clean in inputs else set()) and sum(i['status']=='review' for i in issues)==len(inputs)-(1 if clean in inputs else 0))
        check('F1 '+name+' owner evidence retained', all({r['source_record_id'] for key in i['disputed_keys'] for r in key['claiming_source_records']}=={'101','102'} for i in issues if i['status']=='review'))
        db = store();report=save('F1 '+name, db.import_batch(inputs))
        check('F1 '+name+' no first winner in storage', report['inserted']==(1 if clean in inputs else 0) and not {'101','102'} & {r['source_id'] for r in db.summary()['records']})
        prior=db.summary();again=db.import_batch(list(reversed(inputs)))
        check('F1 '+name+' replay no new writes',again['replayed'] and again['this_attempt_inserted']==0 and db.summary()==prior)
    db=store();db.import_batch([a]);prior=db.summary()
    report=save('F1 pre-existing accepted owner',db.import_batch([a,b,clean]))
    check('F1 existing facts are not deleted or remapped',db.summary()['records'][0]==prior['records'][0] and {r['source_id'] for r in db.summary()['records']}=={'101','103'})
    check('F1 all incoming disputed owners still reviewed',report['inserted']==1 and len(report['issues'])==2)
    # An old unversioned receipt must not make a corrected repeat import return
    # a historical first-winner decision. Preserve it, re-evaluate in new policy.
    db=store();db.import_batch([a]);prior=db.summary()
    legacy_hash=digest(sorted(hashlib.sha256(raw).hexdigest()+':'+digest(meta) for raw,meta in [a,b]))
    legacy_receipt=json.dumps({'batch_hash':legacy_hash,'inserted':1,'issues':[{'status':'review','reason':'CROSS_SOURCE_ID_OWNER_CONFLICT'}]})
    with closing(db.connect()) as con, con:
        con.execute('INSERT INTO m5_batches VALUES (?,?)',(legacy_hash,legacy_receipt))
    report=save('F1 legacy receipt migration-free re-evaluation',db.import_batch([a,b]))
    check('F1 old receipt cannot bypass new isolation',not report['replayed'] and report['batch_hash']!=legacy_hash and report['this_attempt_inserted']==0 and len(report['issues'])==2 and db.summary()==prior)
    with closing(db.connect(readonly=True)) as con:
        check('F1 historical receipt left byte-for-byte unchanged',con.execute('SELECT report FROM m5_batches WHERE batch_hash=?',(legacy_hash,)).fetchone()[0]==legacy_receipt)
    again=db.import_batch([b,a])
    check('F1 corrected versioned receipt replays all review decisions',again['replayed'] and len(again['issues'])==2 and again['governance_policy']==report['governance_policy'])
    # F2: root type errors are individual rejected records, not batch exceptions.
    for value in [[], None, 42, 'SYNTHETIC ROOT STRING', True, 3.5]:
        data=json.dumps(value).encode();bad=(data,evidence(104,data))
        try:
            adapt(*bad)
        except ValueError as exc:
            reason=str(exc)
        else:
            reason='NOT_REJECTED'
        check('F2 direct root '+repr(value),reason=='JSON_ROOT_NOT_OBJECT')
        for order in [[bad,clean],[clean,bad]]:
            db=store();report=save('F2 '+repr(value),db.import_batch(order))
            check('F2 mixed root '+repr(value)+' valid peer committed',report['inserted']==1 and len(report['issues'])==1 and report['issues'][0]['status']=='rejected' and report['issues'][0]['reason']=='JSON_ROOT_NOT_OBJECT')
            prior=db.summary();again=db.import_batch(list(reversed(order)))
            check('F2 mixed root '+repr(value)+' replay safe',again['replayed'] and again['this_attempt_inserted']==0 and db.summary()==prior)
    # F3: seeded labels stay unchanged; independent records still commit.
    bad_genre=sample(105,title='SYNTHETIC CONFLICT GENRE',imdb='tt1234570',genres=[{'id':18,'name':'不同译名'}])
    new_clean=sample(106,title='SYNTHETIC CLEAN GENRE',imdb='tt1234571',genres=[{'id':28,'name':'动作'}])
    for order in [[bad_genre,new_clean],[new_clean,bad_genre]]:
        db=store();db.import_batch([a]);seed=db.summary()
        report=save('F3 existing genre',db.import_batch(order))
        check('F3 existing label conflict is row review',report['inserted']==1 and len(report['issues'])==1 and report['issues'][0]['reason']=='GENRE_SOURCE_CONFLICT')
        check('F3 review preserves both names and provenance',report['issues'][0]['source_record_id']=='105' and report['issues'][0]['details']==[{'tmdb_genre_id':'18','stored_name':'剧情','incoming_name':'不同译名','scope':'existing'}])
        summary=db.summary()
        check('F3 seed identity and label untouched',summary['records'][0]==seed['records'][0] and next(g['name_zh'] for g in summary['genres'] if g['genre_id']==seed['genres'][0]['genre_id'])=='剧情')
        check('F3 conflicting movie has no partial writes',{r['source_id'] for r in summary['records']}=={'101','106'} and len(summary['movies'])==2)
        prior=summary;again=db.import_batch(list(reversed(order)))
        check('F3 replay no covert overwrite',again['replayed'] and db.summary()==prior)
    matching=sample(111,title='SYNTHETIC MATCHING ESTABLISHED LABEL',imdb='tt1234576')
    db=store();db.import_batch([a]);report=db.import_batch([bad_genre,matching])
    check('F3 peer matching established genre remains eligible',report['inserted']==1 and {r['source_id'] for r in db.summary()['records']}=={'101','111'} and len(report['issues'])==1)
    # New genre ambiguity has no known canonical label: isolate every claimant.
    ga=sample(107,title='SYNTHETIC GENRE A',imdb='tt1234572',genres=[{'id':99,'name':'名称甲'}])
    gb=sample(108,title='SYNTHETIC GENRE B',imdb='tt1234573',genres=[{'id':99,'name':'名称乙'}])
    for order in [[ga,gb,new_clean],[gb,ga,new_clean]]:
        db=store();report=save('F3 new genre batch conflict',db.import_batch(order))
        check('F3 new ambiguous ID all rows isolated',report['inserted']==1 and len(report['issues'])==2 and {r['source_id'] for r in db.summary()['records']}=={'106'})
        check('F3 ambiguous genre never created',all(g['name_zh'] not in ('名称甲','名称乙') for g in db.summary()['genres']))
    duplicate_genre=sample(109,title='SYNTHETIC REPEATED FACT',imdb='tt1234574',genres=[{'id':18,'name':'剧情'},{'id':18,'name':'剧情'}])
    db=store();report=db.import_batch([duplicate_genre])
    with closing(db.connect(readonly=True)) as con:
        check('F3 repeated identical genre fact not a batch failure',report['inserted']==1 and con.execute('SELECT COUNT(*) FROM movie_genres').fetchone()[0]==1)
    within_row=sample(110,title='SYNTHETIC INTERNAL GENRE CONFLICT',imdb='tt1234575',genres=[{'id':99,'name':'名称甲'},{'id':99,'name':'名称乙'}])
    db=store();report=db.import_batch([within_row,new_clean])
    check('F3 one record contradicting itself reviewed',report['inserted']==1 and report['issues'][0]['source_record_id']=='110')
    # Normal row review must still preserve whole-transaction rollback on faults.
    db=store();db.import_batch([a]);prior=db.summary()
    def interrupted(con):
        raise KeyboardInterrupt('SYNTHETIC F3 precommit interruption')
    try:
        db.import_batch([bad_genre,new_clean],before_commit=interrupted)
    except KeyboardInterrupt:
        pass
    check('F3 unexpected interruption rolls back valid peer and receipt',db.summary()==prior)
    report=db.import_batch([bad_genre,new_clean]);check('F3 rolled-back mixed batch retries',not report['replayed'] and report['inserted']==1)
    with closing(db.connect(readonly=True)) as con:
        check('F3 SQLite integrity after mixed commit',con.execute('PRAGMA integrity_check').fetchone()[0]=='ok')
    result={'checks':checks,'receipts':receipts,'scope':'Fully synthetic F1-F3 counterexamples; no real input/network/authentication'}
    (out/'review-counterexample-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('M5 independent review counterexamples',len(checks),'PASS')


if __name__=='__main__':
    main(sys.argv[1])
