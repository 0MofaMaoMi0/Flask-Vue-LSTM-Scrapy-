"""Explicit bounded official movie capture; secrets only in prompt/process memory.

Uses already-installed curl with normal TLS verification and current Windows
proxy. No automatic retries, config changes, poster fetches or service startup.
"""
import argparse
import getpass
import hashlib
import json
import pathlib
import shutil
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
from urllib.request import getproxies


def capture(directory, ids, token):
    path = pathlib.Path(directory)
    if not path.is_absolute() or path.drive.upper() == 'C:' or not path.is_dir():
        raise ValueError('Existing absolute non-C output directory required')
    if any(p.exists() and getattr(p.lstat(), 'st_file_attributes', 0) & 1024 for p in (path, *path.parents)):
        raise ValueError('Linked output rejected')
    if not ids or len(ids) > 20 or len(set(ids)) != len(ids) or any(type(i) is not int or i <= 0 for i in ids):
        raise ValueError('1..20 distinct positive movie IDs required')
    if not token or any(c in token for c in '\r\n"\\'):
        raise ValueError('Invalid read token')
    curl = shutil.which('curl.exe') or shutil.which('curl')
    if not curl:
        raise ValueError('Existing curl required; installation prohibited')
    proxy = getproxies().get('https')
    if proxy:
        parsed = urlsplit(proxy)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost') or not parsed.port or parsed.username or parsed.password:
            raise ValueError('Review non-loopback/credential proxy before use')
        proxy = f'http://{parsed.hostname}:{parsed.port}'
    # Exclusive owned output only, no overwrite even after partial capture.
    with (path / 'Capture-Owner.json').open('x', encoding='utf-8') as owner:
        json.dump({'marker': 'MI2-M5-TMDB-CAPTURE-v1', 'ids': ids}, owner)
    (path / 'raw').mkdir()
    rows = []
    for mid in ids:
        url = f'https://api.themoviedb.org/3/movie/{mid}?language=zh-CN'
        config = f'url = "{url}"\nheader = "Authorization: Bearer {token}"\nheader = "Accept: application/json"\nuser-agent = "MovieInsight-M5/1.0 academic metadata capture"\n'
        if proxy:
            config += f'proxy = "{proxy}"\n'
        try:
            result = subprocess.run([curl, '--config', '-', '--silent', '--show-error', '--max-time', '25', '--proto', '=https', '--write-out', '\n%{http_code}'], input=config.encode('utf-8'), capture_output=True, timeout=30)
            raw, status = result.stdout.rsplit(b'\n', 1)
            status = status.decode('ascii').strip()
        except (subprocess.TimeoutExpired, ValueError, UnicodeError):
            result, raw, status = None, b'', '000'
        row = dict(tmdb_id=mid, url=url, acquired_utc=datetime.now(timezone.utc).isoformat(),
                   http_status=status, validated=False, license_ref='https://developer.themoviedb.org/docs/faq')
        if result is not None and result.returncode == 0 and row['http_status'] == '200':
            try:
                data = json.loads(raw)
                row.update(validated=type(data.get('id')) is int and data['id'] == mid and bool(data.get('title')) and 'status_code' not in data, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
                if row['validated']:
                    (path / 'raw' / f'tmdb-{mid}.json').write_bytes(raw)
            except (ValueError, AttributeError):
                row['error'] = 'INVALID_JSON'
        else:
            row['error'] = 'HTTP_OR_TRANSPORT_FAILURE'  # Never dump request headers.
        rows.append(row)
        (path / 'Capture-Manifest.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        if not row['validated']:
            break
        time.sleep(1)
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--ids', nargs='+', type=int, required=True)
    args = parser.parse_args()
    result = capture(args.output, args.ids, getpass.getpass('TMDB API read token (hidden): '))
    print(json.dumps({'successful': sum(r['validated'] for r in result), 'requested': len(args.ids), 'manifest': 'Capture-Manifest.json'}))
