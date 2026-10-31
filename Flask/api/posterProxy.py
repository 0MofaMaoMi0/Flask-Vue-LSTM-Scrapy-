"""按需回源的海报代理。

数据库里保存的多是豆瓣图片地址，浏览器直接引用会被防盗链拦下（403/418），
只有服务端带上豆瓣 Referer 才能取到图。这里按 douban_id 回源，做到：

- 本地已存好的 WebP、TMDB 图：直接 302，不经过本进程；
- 豆瓣图：单飞去重 + 按 host 节流 + 超时降级；
- 可选磁盘 LRU 缓存（``POSTER_PROXY_CACHE_MB``，默认 0，即完全不落盘）；
- 浏览器侧 immutable 缓存，第二次访问不再回源。
"""

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests
from flask import Blueprint, Response, abort, current_app, redirect

from base.core import db
from models.movie import Movie


posterBp = Blueprint('poster', __name__)

LOCAL_PREFIX = '/api/media/covers/'
DOUBAN_REFERER = 'https://movie.douban.com/subject/{}/'
DOUBAN_HOST_SUFFIX = '.doubanio.com'
UPSTREAM_TIMEOUT = 8
UPSTREAM_MAX_BYTES = 5 * 1024 * 1024
HOST_INTERVAL_SECONDS = 0.2
IMMUTABLE_CACHE = 'public, max-age=31536000, immutable'

_host_lock = threading.Lock()
_host_next_slot = {}
_inflight_lock = threading.Lock()
_inflight = {}
_cache_lock = threading.Lock()


def _wait_for_host_slot(host):
    """按域名限速，避免同一时间向一个图片站打太多请求。"""
    with _host_lock:
        now = time.monotonic()
        slot = _host_next_slot.get(host, now)
        start = max(now, slot)
        _host_next_slot[host] = start + HOST_INTERVAL_SECONDS
    delay = start - time.monotonic()
    if delay > 0:
        time.sleep(min(delay, 1.0))


def _is_douban_image(url):
    host = (urlsplit(url).hostname or '').lower()
    return host.endswith(DOUBAN_HOST_SUFFIX)


def _cache_dir():
    size_mb = current_app.config.get('POSTER_PROXY_CACHE_MB', 0) or 0
    if size_mb <= 0:
        return None
    root = Path(current_app.config['POSTER_STORAGE_ROOT']).parent / 'poster-cache'
    return root


def _cache_read(url):
    root = _cache_dir()
    if root is None:
        return None
    key = hashlib.sha256(url.encode('utf-8')).hexdigest()
    body = root / key[:2] / '{}.bin'.format(key)
    meta = root / key[:2] / '{}.json'.format(key)
    if not body.is_file() or not meta.is_file():
        return None
    try:
        content_type = json.loads(meta.read_text('utf-8'))['content_type']
    except (OSError, ValueError, KeyError):
        return None
    try:
        os.utime(body, None)  # 命中即刷新 LRU 顺序
        return body.read_bytes(), content_type
    except OSError:
        return None


def _cache_write(url, body, content_type):
    root = _cache_dir()
    if root is None:
        return
    limit = int((current_app.config.get('POSTER_PROXY_CACHE_MB') or 0) * 1024 * 1024)
    key = hashlib.sha256(url.encode('utf-8')).hexdigest()
    folder = root / key[:2]
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / '{}.bin'.format(key)).write_bytes(body)
        (folder / '{}.json'.format(key)).write_text(
            json.dumps({'content_type': content_type, 'url': url}), 'utf-8'
        )
    except OSError:
        return
    with _cache_lock:
        files = sorted(
            (p for p in root.rglob('*.bin') if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        total = sum(p.stat().st_size for p in files)
        for stale in files:
            if total <= limit:
                break
            size = stale.stat().st_size
            meta = stale.with_suffix('.json')
            try:
                stale.unlink()
                if meta.exists():
                    meta.unlink()
            except OSError:
                continue
            total -= size


def _fetch_douban(url, referer):
    """回源取图；同一 URL 并发只打一次上游。"""
    cached = _cache_read(url)
    if cached is not None:
        return cached
    with _inflight_lock:
        gate = _inflight.setdefault(url, threading.Lock())
    with gate:
        cached = _cache_read(url)
        if cached is not None:
            return cached
        _wait_for_host_slot(urlsplit(url).hostname or '')
        try:
            response = requests.get(
                url,
                headers={
                    'Referer': referer,
                    'User-Agent': current_app.config.get('POSTER_PROXY_USER_AGENT', ''),
                },
                timeout=UPSTREAM_TIMEOUT,
                stream=True,
            )
        except requests.RequestException:
            return None
        try:
            if response.status_code != 200:
                return None
            body = response.raw.read(UPSTREAM_MAX_BYTES + 1, decode_content=True)
            if not body or len(body) > UPSTREAM_MAX_BYTES:
                return None
            content_type = response.headers.get('Content-Type') or 'image/jpeg'
        finally:
            response.close()
        _cache_write(url, body, content_type)
        return body, content_type


@posterBp.route('/poster/<douban_id>', methods=['GET'])
def get_poster(douban_id):
    if not douban_id.isdigit():
        abort(404)
    row = db.session.query(Movie).filter(Movie.douban_id == douban_id).first()
    if row is None:
        abort(404)

    source = (getattr(row, 'cover_source_url', '') or '').strip()
    cover = (getattr(row, 'cover', '') or '').strip()
    target = source or cover

    if target.startswith(LOCAL_PREFIX):
        return redirect(target, code=302)
    if 'image.tmdb.org' in target:
        return redirect(target, code=302)
    if not target.startswith(('http://', 'https://')) or not _is_douban_image(target):
        abort(404)

    result = _fetch_douban(target, DOUBAN_REFERER.format(douban_id))
    if result is None:
        abort(502)
    body, content_type = result
    response = Response(body, content_type=content_type)
    response.headers['Cache-Control'] = IMMUTABLE_CACHE
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response