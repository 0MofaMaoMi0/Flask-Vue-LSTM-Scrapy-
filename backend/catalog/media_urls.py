"""Resolve stored source paths at the DTO boundary; never mutate catalog data."""
import re
from urllib.parse import quote,urlsplit
from media_api import ALLOWED_HOSTS

def asset_url(asset):
    path=asset.get('source_path')
    if not isinstance(path,str) or not path:return None
    if asset.get('source')=='tmdb' and path.startswith('/'):
        if not re.fullmatch(r'/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)',path,re.I):return None
        size='w1280' if asset.get('asset_type')=='backdrop' else 'w342'
        path='https://image.tmdb.org/t/p/'+size+path
    try:
        parsed=urlsplit(path);port=parsed.port
    except ValueError:return None
    if parsed.scheme in ('http','https') and parsed.hostname in ALLOWED_HOSTS and not parsed.username and not parsed.password and port in (None,80,443):
        return '/api/media/poster?u='+quote(path,safe='')
    return None
