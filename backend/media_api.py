"""Poster proxy with disk cache (M9 option C).

Only allow-listed image hosts are fetched; responses are size/type checked and
cached by URL hash. No user credentials or cookies are forwarded.
"""
import hashlib
import os
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


def install(app):
    import traceback
    @app.errorhandler(Exception)
    def _debug_errors(error):
        return Response(traceback.format_exc(), status=500, mimetype='text/plain')
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
        if target.is_file() and target.stat().st_size > 0:
            return Response(target.read_bytes(), mimetype='image/jpeg', headers={'Cache-Control':'public, max-age=86400'})
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            import traceback
            return Response(traceback.format_exc(), status=500, mimetype='text/plain')
        req = urllib.request.Request(raw, headers={
            'User-Agent': 'MovieInsight/1.0 (poster cache)',
            'Referer': ('https://movie.douban.com/' if (parsed.hostname or '').endswith('doubanio.com') else 'https://www.themoviedb.org/'),
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                ctype = (response.headers.get('Content-Type') or '').split(';')[0].strip().lower()
                if not ctype.startswith('image/'):
                    return jsonify(code='NOT_AN_IMAGE', data=None), 415
                data = response.read(MAX_BYTES + 1)
        except Exception:
            return jsonify(code='UPSTREAM_UNAVAILABLE', data=None), 502
        if len(data) > MAX_BYTES:
            return jsonify(code='TOO_LARGE', data=None), 413
        if not (data.startswith(b'\xff\xd8') or data.startswith(b'\x89PNG')
                or data[:4] == b'RIFF' or data.startswith(b'GIF8')):
            return jsonify(code='NOT_AN_IMAGE', data=None), 415
        try:
            target.write_bytes(data)
            return Response(data, mimetype=ctype, headers={'Cache-Control':'public, max-age=86400'})
        except Exception:
            import traceback
            return Response(traceback.format_exc(), status=500, mimetype='text/plain')

    app.register_blueprint(bp)
