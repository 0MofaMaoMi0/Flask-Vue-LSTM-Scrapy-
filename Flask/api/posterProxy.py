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
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlsplit

import requests
from flask import Blueprint, Response, abort, current_app, redirect
from sqlalchemy import text

from base.core import db


posterBp = Blueprint('poster', __name__)

LOCAL_PREFIX = '/api/media/covers/'
DOUBAN_REFERER = 'https://movie.douban.com/subject/{}/'
DOUBAN_HOST_SUFFIX = '.doubanio.com'
UPSTREAM_TIMEOUT = (3, 6)
UPSTREAM_MAX_BYTES = 5 * 1024 * 1024
HOST_INTERVAL_SECONDS = 0.2
FAILURE_THRESHOLD = 3
BREAKER_COOLDOWN_SECONDS = 60
IMMUTABLE_CACHE = 'public, max-age=31536000, immutable'
SOURCE_CACHE_LIMIT = 5000

_host_lock = threading.Lock()
_host_next_slot = {}
_inflight_lock = threading.Lock()
_inflight = {}
_cache_lock = threading.Lock()
_breaker_lock = threading.Lock()
_breaker_state = {}
_source_lock = threading.Lock()
_source_cache = OrderedDict()


def _lookup_source(douban_id):
    """用独立的短连接查一次地址，查完立刻归还连接池。

    这里刻意不走 ORM session：海报回源要花 3~6 秒做网络请求，如果把
    session（以及它占用的连接）跨过网络请求持有，刷新几次就会把连接池
    抽干，连登录、列表接口都会一起 500。
    """
    with _source_lock:
        cached = _source_cache.get(douban_id)
        if cached is not None:
            _source_cache.move_to_end(douban_id)
            return cached
    sql = text(
        'SELECT cover, cover_source_url FROM movies2 '
        'WHERE douban_id = :douban_id LIMIT 1'
    )
    with db.engine.connect() as connection:
        row = connection.execute(sql, {'douban_id': douban_id}).fetchone()
    if row is None:
        result = None
    else:
        result = (
            (row[1] or '').strip(),
            (row[0] or '').strip(),
        )
    with _source_lock:
        _source_cache[douban_id] = result
        if len(_source_cache) > SOURCE_CACHE_LIMIT:
            _source_cache.popitem(last=False)
    return result


def _breaker_open(host):
    with _breaker_lock:
        failures, opened_at = _breaker_state.get(host, (0, 0.0))
        if opened_at and time.monotonic() - opened_at < BREAKER_COOLDOWN_SECONDS:
            return True
    return False


def _breaker_record(host, ok):
    """连续失败达到阈值就短暂熔断，避免整页海报把工作线程全占住。"""
    with _breaker_lock:
        if ok:
            _breaker_state.pop(host, None)
            return
        failures, opened_at = _breaker_state.get(host, (0, 0.0))
        failures += 1
        if failures >= FAILURE_THRESHOLD:
            _breaker_state[host] = (0, time.monotonic())
        else:
            _breaker_state[host] = (failures, 0.0)


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
    host = urlsplit(url).hostname or ''
    if _breaker_open(host):
        return None
    with _inflight_lock:
        gate = _inflight.setdefault(url, threading.Lock())
    with gate:
        cached = _cache_read(url)
        if cached is not None:
            return cached
        _wait_for_host_slot(host)
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
            _breaker_record(host, False)
            return None
        try:
            if response.status_code != 200:
                _breaker_record(host, False)
                return None
            body = response.raw.read(UPSTREAM_MAX_BYTES + 1, decode_content=True)
            if not body or len(body) > UPSTREAM_MAX_BYTES:
                _breaker_record(host, False)
                return None
            content_type = response.headers.get('Content-Type') or 'image/jpeg'
        finally:
            response.close()
        _breaker_record(host, True)
        _cache_write(url, body, content_type)
        return body, content_type


@posterBp.route('/poster/<douban_id>', methods=['GET'])
def get_poster(douban_id):
    if not douban_id.isdigit():
        abort(404)
    lookup = _lookup_source(douban_id)
    if lookup is None:
        abort(404)
    source, cover = lookup

    target = source or cover

    if target.startswith(LOCAL_PREFIX):
        return redirect(target, code=302)
    if 'image.tmdb.org' in target:
        return redirect(target, code=302)
    if not target.startswith(('http://', 'https://')) or not _is_douban_image(target):
        abort(404)

    # 到这里数据库连接已经归还，下面的网络请求不会占用连接池。
    result = _fetch_douban(target, DOUBAN_REFERER.format(douban_id))
    if result is None:
        abort(502)
    body, content_type = result
    response = Response(body, content_type=content_type)
    response.headers['Cache-Control'] = IMMUTABLE_CACHE
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response