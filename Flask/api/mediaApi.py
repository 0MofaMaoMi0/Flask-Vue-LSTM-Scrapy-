from pathlib import Path

from flask import Blueprint, abort, current_app, send_from_directory


mediaBp = Blueprint('media', __name__)


@mediaBp.route('/covers/<path:filename>', methods=['GET'])
def get_cover(filename):
    if Path(filename).suffix.lower() != '.webp':
        abort(404)
    response = send_from_directory(
        current_app.config['POSTER_STORAGE_ROOT'],
        filename,
        conditional=True,
        max_age=31536000,
    )
    response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
