"""Runtime configuration shared by the Scrapy project and helper commands."""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from project_config import optional


DEFAULT_USER_AGENT = 'Flask-Vue-LSTM-Scrapy/1.0 (learning project)'


def get_douban_cookie():
    """Return the configured Cookie header without logging its contents."""
    return (optional('DOUBAN_COOKIE') or optional('COOKIE', '')).strip()


def get_douban_user_agent():
    """Return the configured stable User-Agent value."""
    return (optional('DOUBAN_USER_AGENT') or DEFAULT_USER_AGENT).strip()


def get_poster_storage_root():
    """Return an absolute poster directory resolved from the project root."""
    configured = optional('POSTER_STORAGE_ROOT', 'data/covers').strip()
    path = Path(configured)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path.resolve())


def get_poster_public_prefix():
    """Return the same-origin URL prefix exposed through Flask/Vue proxying."""
    return optional('POSTER_PUBLIC_PREFIX', '/api/media/covers').rstrip('/')


def get_tmdb_api_read_token():
    """Return the optional TMDB API Read Access Token without logging it."""
    return (optional('TMDB_API_READ_TOKEN', '') or '').strip()


def get_tmdb_language():
    """Return the preferred TMDB metadata language."""
    return (optional('TMDB_LANGUAGE', 'zh-CN') or 'zh-CN').strip()


def get_tmdb_image_size():
    """Return the configured TMDB image size segment."""
    return (optional('TMDB_IMAGE_SIZE', 'w500') or 'w500').strip()


def parse_cookie_header(raw_cookie):
    """Convert a browser Cookie header to a Scrapy/requests cookie mapping."""
    cookies = {}
    for fragment in raw_cookie.split(';'):
        name, separator, value = fragment.strip().partition('=')
        if separator and name:
            cookies[name] = value
    return cookies
