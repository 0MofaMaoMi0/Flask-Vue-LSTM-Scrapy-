"""Explicit disposable M5 SQLite read projection, NOT a MySQL migration.

Only seven public-query/provenance tables are projected. Separate m5_* bookkeeping
does not extend frozen CatalogBase or touch authentication metadata.
"""
import hashlib
import json
import os
import pathlib
import sqlite3
from contextlib import closing
from decimal import Decimal
from sqlalchemy.dialects import sqlite
from sqlalchemy.sql.selectable import Select
from .models import CatalogBase
from .data_pipeline import canonical, digest
from .tmdb_local import prepare, MODE

MARKER = 'MI2-M5-TMDB-LOCAL-v1'
GOVERNANCE_POLICY = 'M5-F1-F3-v1'
TABLES = ('movies', 'movie_external_ids', 'movie_source_records', 'movie_aliases', 'genres', 'movie_genres', 'movie_ratings_summary')


class LocalCatalogStore:
    def __init__(self, path, *, initialize=False):
        path = pathlib.Path(path)
        if not path.is_absolute() or path.drive.upper() == 'C:' or path.suffix != '.sqlite3':
            raise ValueError('Explicit absolute non-C SQLite required')
        if any(p.exists() and getattr(p.lstat(), 'st_file_attributes', 0) & 1024 for p in (path, *path.parents)):
            raise ValueError('Linked storage prohibited')
        self.path = path.resolve()
        if initialize:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('CREATE TABLE m5_metadata (marker TEXT NOT NULL)')
                db.execute('INSERT INTO m5_metadata VALUES (?)', (MARKER,))
                for name in TABLES:
                    cols = []
                    for c in CatalogBase.metadata.tables[name].c:
                        if c.name in ('id', 'movie_id', 'genre_id') and len(CatalogBase.metadata.tables[name].primary_key.columns) == 1 and c.primary_key:
                            decl = 'INTEGER PRIMARY KEY AUTOINCREMENT'
                        else:
                            decl = 'INTEGER' if c.type.__class__.__name__ in ('BigInteger', 'Integer', 'SmallInteger', 'Boolean') else 'TEXT'
                        cols.append('"' + c.name + '" ' + decl)
                    db.execute('CREATE TABLE "' + name + '" (' + ','.join(cols) + ')')
                db.execute('CREATE UNIQUE INDEX m5_external_unique ON movie_external_ids(source,external_id)')
                db.execute('CREATE UNIQUE INDEX m5_genre_unique ON genres(tmdb_genre_id)')
                db.execute('CREATE UNIQUE INDEX m5_movie_genre_unique ON movie_genres(movie_id,genre_id)')
                db.execute('CREATE TABLE m5_records (source TEXT NOT NULL, source_id TEXT NOT NULL, movie_id INTEGER NOT NULL REFERENCES movies(movie_id), raw_hash TEXT NOT NULL, identity_signature TEXT NOT NULL, payload TEXT NOT NULL, evidence TEXT NOT NULL, PRIMARY KEY(source,source_id))')
                db.execute('CREATE TABLE m5_batches (batch_hash TEXT PRIMARY KEY, report TEXT NOT NULL)')
        self._verify()

    def _verify(self):
        if not self.path.is_file() or self.path.stat().st_nlink != 1:
            raise ValueError('Missing or linked storage')
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('SELECT marker FROM m5_metadata').fetchall() != [(MARKER,)]:
                raise ValueError('Foreign storage or version; no auto migration')

    def connect(self, readonly=False):
        self._verify()
        db = sqlite3.connect(self.path.as_uri() + ('?mode=ro' if readonly else '?mode=rw'), uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def import_batch(self, records, *, before_commit=None):
        rows, issues = prepare(records)
        # Old cached decisions must not bypass corrected governance. This only
        # namespaces new receipts; existing records/receipts are never rewritten.
        batch = digest({'governance_policy': GOVERNANCE_POLICY, 'inputs': sorted(hashlib.sha256(raw).hexdigest() + ':' + digest(evidence) for raw, evidence in records)})
        report = {'batch_hash': batch, 'governance_policy': GOVERNANCE_POLICY, 'mode': MODE, 'input': len(records), 'inserted': 0, 'duplicate': sum(i['status']=='duplicate' for i in issues), 'issues': issues, 'bindings': []}
        with closing(self.connect()) as db:
            try:
                db.execute('BEGIN IMMEDIATE')
                previous = db.execute('SELECT report FROM m5_batches WHERE batch_hash=?', (batch,)).fetchone()
                if previous:
                    return dict(json.loads(previous[0]), replayed=True, this_attempt_inserted=0)
                # Validate genre claims before any movie/projection writes.
                # Existing labels remain authoritative; a new ambiguous ID has
                # no first-row winner. Only affected records go to review.
                stored_names = {r['tmdb_genre_id']: r['name_zh'] for r in db.execute('SELECT tmdb_genre_id,name_zh FROM genres')}
                claimed_names = {}
                for row in rows:
                    for genre in row['tmdb_genres']:
                        claimed_names.setdefault(str(genre['id']), set()).add(genre['name'])
                accepted_rows = []
                for row in rows:
                    genre_conflicts = []
                    for gid, name in sorted({(str(g['id']), g['name']) for g in row['tmdb_genres']}):
                        if gid in stored_names and name != stored_names[gid]:
                            genre_conflicts.append({'tmdb_genre_id': gid, 'stored_name': stored_names[gid], 'incoming_name': name, 'scope': 'existing'})
                        elif gid not in stored_names and len(claimed_names[gid]) > 1:
                            genre_conflicts.append({'tmdb_genre_id': gid, 'incoming_names': sorted(claimed_names[gid]), 'scope': 'batch'})
                    if genre_conflicts:
                        issues.append({'status': 'review', 'reason': 'GENRE_SOURCE_CONFLICT', 'source': row['source'], 'source_record_id': row['source_record_id'], 'raw_sha256': row['raw_sha256'], 'details': genre_conflicts})
                    else:
                        accepted_rows.append(row)
                for row in accepted_rows:
                    owners = {r[0] for source, eid in row['external_ids'].items() for r in db.execute('SELECT movie_id FROM movie_external_ids WHERE source=? AND external_id=?', (source, eid))}
                    old = db.execute('SELECT * FROM m5_records WHERE source=? AND source_id=?', ('tmdb', row['source_record_id'])).fetchone()
                    reason = None
                    if len(owners) > 1 or (old and owners != {old['movie_id']}):
                        reason = 'EXTERNAL_ID_BRIDGE_CONFLICT'
                    elif old and old['raw_hash'] != row['raw_sha256'] and old['payload'] != canonical(row['raw']):
                        reason = 'SOURCE_CHANGED_REVIEW_REQUIRED'
                    elif owners and not old:
                        reason = 'CROSS_SOURCE_ID_OWNER_CONFLICT'
                    elif not owners and any(r[0].casefold() == row['title_normalized'] for r in db.execute('SELECT title FROM movies WHERE release_year IS ?', (row['release_year'],))):
                        reason = 'TITLE_YEAR_CANDIDATE_ONLY'
                    if reason:
                        issues.append({'status': 'review', 'reason': reason, 'raw_sha256': row['raw_sha256']})
                        continue
                    if old:
                        report['duplicate'] += 1
                        report['bindings'].append({'source_id': row['source_record_id'], 'movie_id': old['movie_id']})
                        continue
                    timestamp = row['observed_at']
                    fields = ('title', 'original_title', 'release_date', 'release_year', 'runtime_minutes', 'original_language', 'overview', 'is_adult')
                    cursor = db.execute('INSERT INTO movies (' + ','.join(fields) + ',catalog_status,created_at,updated_at) VALUES (' + ','.join('?' for _ in range(len(fields) + 3)) + ')', tuple(row[k] for k in fields) + ('active', timestamp, timestamp))
                    mid = cursor.lastrowid
                    for source, eid in row['external_ids'].items():
                        db.execute('INSERT INTO movie_external_ids(movie_id,source,external_id,verified_method,created_at) VALUES (?,?,?,?,?)', (mid, source, eid, 'tmdb_api_assertion', timestamp))
                    for alias in row['aliases']:
                        db.execute('INSERT INTO movie_aliases(movie_id,title,title_normalized,language,source,created_at) VALUES (?,?,?,?,?,?)', (mid, alias, alias.casefold(), row['original_language'], 'tmdb', timestamp))
                    source_pk = db.execute('INSERT INTO movie_source_records(movie_id,source,source_record_id,fetched_at,payload_hash,metadata_json,license_status) VALUES (?,?,?,?,?,?,?)',
                                           (mid, 'tmdb', row['source_record_id'], timestamp, row['raw_sha256'], canonical(row['raw']), 'allowed')).lastrowid
                    for genre_id, genre_name in sorted({(g['id'], g['name']) for g in row['tmdb_genres']}):
                        found = db.execute('SELECT genre_id,name_zh FROM genres WHERE tmdb_genre_id=?', (str(genre_id),)).fetchone()
                        if found and found['name_zh'] != genre_name:
                            raise RuntimeError('Genre preflight invariant violated')
                        gid = found['genre_id'] if found else db.execute('INSERT INTO genres(name_zh,tmdb_genre_id) VALUES (?,?)', (genre_name, str(genre_id))).lastrowid
                        db.execute('INSERT INTO movie_genres VALUES (?,?)', (mid, gid))
                    for fact in row['rating_facts']:
                        db.execute('INSERT INTO movie_ratings_summary(movie_id,source,score_value,score_scale,vote_count,observed_at,selected_source_record_pk) VALUES (?,?,?,?,?,?,?)', (mid, fact['source'], fact['score_value'], fact['score_scale'], fact['vote_count'], fact['observed_at'], source_pk))
                    evidence = {k: row[k] for k in ('source_url', 'observed_at', 'license_ref', 'raw_sha256', 'poster_path_present')}
                    db.execute('INSERT INTO m5_records VALUES (?,?,?,?,?,?,?)', ('tmdb', row['source_record_id'], mid, row['raw_sha256'], row['identity_signature'], canonical(row['raw']), canonical(evidence)))
                    report['inserted'] += 1
                    report['bindings'].append({'source_id': row['source_record_id'], 'movie_id': mid})
                report['issues'] = sorted(issues, key=canonical)
                report['bindings'].sort(key=lambda r: r['source_id'])
                db.execute('INSERT INTO m5_batches VALUES (?,?)', (batch, canonical(report)))
                if before_commit:
                    before_commit(db)
                db.commit()
                return dict(report, replayed=False, this_attempt_inserted=report['inserted'])
            except BaseException:
                db.rollback()
                raise

    def __call__(self, statement):
        if not isinstance(statement, Select):
            raise ValueError('Read-only Select required')
        compiled = statement.compile(dialect=sqlite.dialect(), compile_kwargs={'render_postcompile': True})
        with closing(self.connect(readonly=True)) as db:
            rows = [dict(r) for r in db.execute(str(compiled), tuple(compiled.params[k] for k in compiled.positiontup)).fetchall()]
        for row in rows:
            if row.get('score_value') is not None:
                row['score_value'] = Decimal(row['score_value'])
            if row.get('is_adult') is not None:
                row['is_adult'] = bool(row['is_adult'])
        return rows

    def summary(self):
        with closing(self.connect(readonly=True)) as db:
            movies = [dict(r) for r in db.execute('SELECT movie_id,title,release_year,runtime_minutes,original_language,overview FROM movies ORDER BY movie_id')]
            records = [dict(r) for r in db.execute('SELECT source,source_id,movie_id,raw_hash FROM m5_records ORDER BY source,source_id')]
            return {'mode': MODE, 'dataset_id': digest(records), 'movies': movies, 'records': records,
                    'genres': [dict(r) for r in db.execute('SELECT genre_id,name_zh FROM genres ORDER BY genre_id')]}
