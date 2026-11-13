"""Write a pending-TMDB queue for trending titles not yet in the local catalog.

Reads the trending snapshots and the catalog mapping; no writes to the database.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from app import create_app
from catalog.models import CatalogBase

from catalog.board_snapshots import directory
CACHE = directory()
app = create_app()
md = CatalogBase.metadata
ext = md.tables['movie_external_ids']
m = md.tables['movies']
with app.extensions['auth_engine'].connect() as c:
    mapped = {str(r[1]) for r in c.execute(select(ext.c.movie_id, ext.c.external_id).where(ext.c.source == 'tmdb'))}
out = {'generated_at': datetime.now(timezone.utc).isoformat(), 'windows': {}, 'pending': {}}
for window in ('popular', 'trending_day', 'trending_week'):
    p = CACHE / f'{window}.json'
    if not p.is_file():
        out['windows'][window] = {'status': 'MISSING'}
        continue
    doc = json.loads(p.read_text(encoding='utf-8'))
    rows = doc.get('items', [])
    out['windows'][window] = {'fetched_at': doc.get('captured_at'), 'upstream': len(rows)}
    for row in rows:
        tid = str(row.get('id'))
        if tid and tid not in mapped:
            entry = out['pending'].setdefault(tid, {'tmdb_id': int(tid), 'title': row.get('title'),
                                                   'release_date': row.get('release_date'), 'windows': []})
            entry['windows'].append({'window': window, 'rank': row.get('source_rank')})
target = CACHE / 'pending_tmdb.json'
temporary=target.with_suffix('.tmp')
temporary.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
os.replace(temporary,target)
print(json.dumps({'pending': len(out['pending']), 'path': str(target), 'windows': out['windows']}))
