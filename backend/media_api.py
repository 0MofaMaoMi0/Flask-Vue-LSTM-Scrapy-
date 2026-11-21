"""Poster proxy with disk cache (M9 option C).

Only allow-listed image hosts are fetched; responses are size/type checked and
cached by URL hash. No user credentials or cookies are forwarded.
"""
import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

from flask import Blueprint, request, jsonify, Response

ALLOWED_HOSTS = {
    "image.tmdb.org",
    "img1.doubanio.com", "img2.doubanio.com", "img3.doubanio.com", "img4.doubanio.com",
    "img5.doubanio.com", "img6.doubanio.com", "img7.doubanio.com",
    "img8.doubanio.com", "img9.doubanio.com", "img10.doubanio.com",
}
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT = 15
CACHE_HEADERS = {'Cache-Control': 'public, max-age=86400'}


def image_content_type(data):
    """Return the canonical type for a supported image header, or None."""
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def cached_response(target):
    """Serve an existing cache entry without deleting or rewriting it."""
    try:
        if target.stat().st_size > MAX_BYTES:
            return jsonify(code='TOO_LARGE', data=None), 413
        data = target.read_bytes()
    except OSError:
        return jsonify(code='MEDIA_CACHE_UNAVAILABLE', data=None), 503
    if len(data) > MAX_BYTES:
        return jsonify(code='TOO_LARGE', data=None), 413
    ctype = image_content_type(data)
    if ctype is None:
        return jsonify(code='NOT_AN_IMAGE', data=None), 415
    return Response(data, mimetype=ctype, headers=CACHE_HEADERS)


def install(app):
    bp = Blueprint('media_api', __name__, url_prefix='/api/media')
    cache_root = Path(app.config.get('MEDIA_CACHE_DIR', '/tmp/mi2-media'))

    @bp.get('/poster')
    def poster():
        raw = request.args.get('u', '')
        if not raw or len(raw) > 2048:
            return jsonify(code='INVALID_INPUT', data=None), 400
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme not in ('http', 'https') or parsed.hostname not in ALLOWED_HOSTS:
            return jsonify(code='HOST_NOT_ALLOWED', data=None), 400
        digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        target = cache_root / digest[:2] / (digest + '.img')
        if target.is_file():
            return cached_response(target)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            return jsonify(code='MEDIA_CACHE_UNAVAILABLE',data=None),503
        req = urllib.request.Request(raw, headers={
            'User-Agent': 'MovieInsight/1.0 (poster cache)',
            'Referer': ('https://movie.douban.com/' if (parsed.hostname or '').endswith('doubanio.com') else 'https://www.themoviedb.org/'),
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                upstream_type = (response.headers.get('Content-Type') or '').split(';')[0].strip().lower()
                if not upstream_type.startswith('image/'):
                    return jsonify(code='NOT_AN_IMAGE', data=None), 415
                data = response.read(MAX_BYTES + 1)
        except Exception:
            return jsonify(code='UPSTREAM_UNAVAILABLE', data=None), 502
        if len(data) > MAX_BYTES:
            return jsonify(code='TOO_LARGE', data=None), 413
        ctype = image_content_type(data)
        if ctype is None:
            return jsonify(code='NOT_AN_IMAGE', data=None), 415
        try:
            with open(target, 'xb') as cached:
                cached.write(data)
        except FileExistsError:
            return cached_response(target)
        except Exception:
            return jsonify(code='MEDIA_CACHE_UNAVAILABLE',data=None),503
        return Response(data, mimetype=ctype, headers=CACHE_HEADERS)

    app.register_blueprint(bp)
