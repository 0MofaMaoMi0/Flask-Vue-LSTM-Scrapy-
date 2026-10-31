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
        # 部分 Windows/Slim 镜像的 mimetypes 库不认识 .webp，会返回空
        # Content-Type；配合 nosniff 浏览器就不会渲染图片，这里显式声明。
        mimetype='image/webp',
        conditional=True,
        max_age=31536000,
    )
    response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
