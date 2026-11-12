"""M4-owned disposable SYNTHETIC SQLite only. No migration or reset path."""
import json
import os
import pathlib
import sqlite3
import threading
from contextlib import closing
from .recommendation import BehaviorStore, USERS

MARKER = 'MI2-M4-SYNTHETIC-BEHAVIOR-v1'


class PersistentBehaviorRepository:
    def __init__(self, path, dataset_id, *, initialize=False):
        raw = pathlib.Path(path)
        if not raw.is_absolute() or raw.drive.upper() == 'C:' or raw.suffix != '.sqlite3':
            raise ValueError('Explicit absolute non-C .sqlite3 path required')
        if any(p.exists() and getattr(p.lstat(), 'st_file_attributes', 0) & 1024 for p in (raw, *raw.parents)):
            raise ValueError('Linked paths prohibited')
        self.path = raw.resolve()
        if not isinstance(dataset_id, str) or not dataset_id:
            raise ValueError('Dataset binding required')
        self.lock = threading.RLock()
        self.dataset_id = dataset_id
        if initialize:
            # Exclusive creation: never overwrite, initialize or upgrade another DB.
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute('CREATE TABLE m4_metadata (marker TEXT NOT NULL, dataset_id TEXT NOT NULL)')
                db.execute('INSERT INTO m4_metadata VALUES (?, ?)', (MARKER, dataset_id))
                db.execute('CREATE TABLE m4_events (seq INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, user TEXT NOT NULL, payload TEXT NOT NULL)')
        if not self.path.is_file() or self.path.stat().st_nlink != 1:
            raise ValueError('Existing unlinked owned file required')
        # Read-only verification precedes opening for writes, including wrong bindings.
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)) as db:
            if db.execute('SELECT marker,dataset_id FROM m4_metadata').fetchall() != [(MARKER, dataset_id)]:
                raise ValueError('Marker/version/dataset mismatch; no automatic migration')
            columns = [r[1] for r in db.execute('PRAGMA table_info(m4_events)')]
            if columns != ['seq', 'event_id', 'user', 'payload']:
                raise ValueError('Unexpected event schema')
        # No persistent connection: each operation closes its own transaction.

    def user(self, user):
        if user not in USERS:
            raise ValueError('Unknown demo user')

    def _connect(self):
        db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=5)
        db.execute('PRAGMA synchronous=FULL')
        if db.execute('SELECT marker,dataset_id FROM m4_metadata').fetchall() != [(MARKER, self.dataset_id)]:
            db.close()
            raise ValueError('Storage binding changed')
        return db

    @staticmethod
    def _replay(db):
        store = BehaviorStore()
        for user, payload in db.execute('SELECT user,payload FROM m4_events ORDER BY seq'):
            event = json.loads(payload)
            store.apply(user, event, {event['movie_id']})
        return store

    def snapshot(self, user):
        self.user(user)
        with self.lock:
            db = self._connect()
            try:
                db.execute('BEGIN')
                return self._replay(db).snapshot(user)
            finally:
                db.close()

    def apply(self, user, event, visible):
        self.user(user)
        with self.lock:
            db = self._connect()
            try:
                db.execute('BEGIN IMMEDIATE')
                store = self._replay(db)
                result = store.apply(user, event, visible)
                if not result['duplicate']:
                    db.execute('INSERT INTO m4_events(event_id,user,payload) VALUES (?,?,?)',
                               (event['event_id'], user, json.dumps(event, sort_keys=True, separators=(',', ':'))))
                    self._before_commit(db)
                db.commit()
                return result
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def _before_commit(self, db):
        """Fault-injection seam; production implementation has no side effect."""
