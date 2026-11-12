"""Backfill movie_assets for the first 22 catalog movies (local static artwork).

Idempotent: inserts only when no primary asset of that type exists.
Run on the VM with the service environment loaded.
"""
from datetime import datetime
import json
from sqlalchemy import select, insert
from app import create_app
from catalog.models import CatalogBase

ASSETS = {1: {'poster': '/m9/shaw-poster.jpg', 'backdrop': '/m9/shaw-hero.jpg'}, 2: {'poster': '/m9/inception-poster.jpg', 'backdrop': '/m9/inception-hero.jpg'}, 1015: {'poster': '/movie-artwork/tmdb-550-poster.jpg', 'backdrop': '/movie-artwork/tmdb-550-backdrop.jpg'}, 1011: {'poster': '/movie-artwork/tmdb-680-poster.jpg', 'backdrop': '/movie-artwork/tmdb-680-backdrop.jpg'}, 1005: {'poster': '/movie-artwork/tmdb-13-poster.jpg', 'backdrop': '/movie-artwork/tmdb-13-backdrop.jpg'}, 1018: {'poster': '/movie-artwork/tmdb-424-poster.jpg', 'backdrop': '/movie-artwork/tmdb-424-backdrop.jpg'}, 1007: {'poster': '/movie-artwork/tmdb-238-poster.jpg', 'backdrop': '/movie-artwork/tmdb-238-backdrop.jpg'}, 1008: {'poster': '/movie-artwork/tmdb-240-poster.jpg', 'backdrop': '/movie-artwork/tmdb-240-backdrop.jpg'}, 1020: {'poster': '/movie-artwork/tmdb-155-poster.jpg', 'backdrop': '/movie-artwork/tmdb-155-backdrop.jpg'}, 1019: {'poster': '/movie-artwork/tmdb-11-poster.jpg', 'backdrop': '/movie-artwork/tmdb-11-backdrop.jpg'}, 1006: {'poster': '/movie-artwork/tmdb-120-poster.jpg', 'backdrop': '/movie-artwork/tmdb-120-backdrop.jpg'}, 1002: {'poster': '/movie-artwork/tmdb-121-poster.jpg', 'backdrop': '/movie-artwork/tmdb-121-backdrop.jpg'}, 1009: {'poster': '/movie-artwork/tmdb-122-poster.jpg', 'backdrop': '/movie-artwork/tmdb-122-backdrop.jpg'}, 1003: {'poster': '/movie-artwork/tmdb-129-poster.jpg', 'backdrop': '/movie-artwork/tmdb-129-backdrop.jpg'}, 1012: {'poster': '/movie-artwork/tmdb-496243-poster.jpg', 'backdrop': '/movie-artwork/tmdb-496243-backdrop.jpg'}, 1016: {'poster': '/movie-artwork/tmdb-372058-poster.jpg', 'backdrop': '/movie-artwork/tmdb-372058-backdrop.jpg'}, 1017: {'poster': '/movie-artwork/tmdb-194-poster.jpg', 'backdrop': '/movie-artwork/tmdb-194-backdrop.jpg'}, 1014: {'poster': '/movie-artwork/tmdb-8363-poster.jpg', 'backdrop': '/movie-artwork/tmdb-8363-backdrop.jpg'}, 1004: {'poster': '/movie-artwork/tmdb-348-poster.jpg', 'backdrop': '/movie-artwork/tmdb-348-backdrop.jpg'}, 1013: {'poster': '/movie-artwork/tmdb-4935-poster.jpg', 'backdrop': '/movie-artwork/tmdb-4935-backdrop.jpg'}, 1010: {'poster': '/movie-artwork/tmdb-862-poster.jpg', 'backdrop': '/movie-artwork/tmdb-862-backdrop.jpg'}, 1021: {'poster': '/movie-artwork/tmdb-157336-poster.jpg', 'backdrop': '/movie-artwork/tmdb-157336-backdrop.jpg'}}

app = create_app()
engine = app.extensions["auth_engine"]
table = CatalogBase.metadata.tables["movie_assets"]
inserted = skipped = missing = 0
with engine.begin() as connection:
    for movie_id, paths in ASSETS.items():
        for asset_type in ("poster", "backdrop"):
            path = paths.get(asset_type)
            if not path:
                continue
            movie = connection.execute(
                select(CatalogBase.metadata.tables["movies"].c.movie_id)
                .where(CatalogBase.metadata.tables["movies"].c.movie_id == movie_id)
            ).scalar_one_or_none()
            if movie is None:
                missing += 1
                continue
            exists = connection.execute(
                select(table.c.asset_id).where(
                    table.c.movie_id == movie_id,
                    table.c.asset_type == asset_type,
                    table.c.is_primary.is_(True),
                )
            ).scalar_one_or_none()
            if exists:
                skipped += 1
                continue
            connection.execute(insert(table).values(
                movie_id=movie_id, asset_type=asset_type, source="local_static",
                source_path=path, language=None, width=None, height=None,
                is_primary=True, license_status="allowed", updated_at=datetime.utcnow(),
            ))
            inserted += 1
print(json.dumps({"inserted": inserted, "skipped": skipped, "missing_movie": missing}))
