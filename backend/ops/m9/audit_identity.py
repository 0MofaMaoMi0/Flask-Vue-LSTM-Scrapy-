"""Read only mapping evidence for the specific conflicting delivery entries."""
import json
from app import create_app
from sqlalchemy import select
from catalog.models import CatalogBase
app=create_app();md=CatalogBase.metadata
movies=md.tables['movies'];ext=md.tables['movie_external_ids']
with app.extensions['auth_engine'].connect() as c:
    rows=c.execute(select(movies.c.movie_id,movies.c.title,movies.c.release_year,ext.c.external_id).join(ext,ext.c.movie_id==movies.c.movie_id).where(movies.c.movie_id.in_([2259,2274,1457,1459]),ext.c.source=='tmdb')).all()
print(json.dumps({'identity_readonly':[dict(movie_id=r[0],title=r[1],year=r[2],tmdb_id=r[3]) for r in rows]},ensure_ascii=False))
