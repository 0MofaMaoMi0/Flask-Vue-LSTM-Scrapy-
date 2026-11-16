#!/usr/bin/env python
"""Offline-first CLI for bounded TMDB supplement capture.

``plan`` and ``validate`` are always side-effect-free dry-runs.  ``capture``
does nothing unless ``--execute`` is present, and even then it requires an
existing absolute non-C: archive root.  The token is read from an environment
variable at runtime; there is deliberately no ``--token`` option and no token
is written to the archive, manifest or output.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / 'backend'
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from catalog.tmdb_supplement.capture import (  # noqa: E402
    ATTRIBUTION,
    CaptureError,
    CaptureInputError,
    capture_movies,
    make_keepalive_fetch,
    make_urllib_fetch,
)
from catalog.tmdb_supplement.ingest import (  # noqa: E402
    IngestError,
    IngestInputError,
    IngestRejected,
    dry_run as ingest_dry_run,
    run as ingest_run,
)
from catalog.tmdb_supplement import board_ingest, trending  # noqa: E402

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_INCOMPLETE = 3
EXIT_AUTH = 4
MAX_TARGET_FILE = 1024 * 1024


def _targets(path):
    file = Path(path)
    try:
        if file.stat().st_size > MAX_TARGET_FILE:
            raise ValueError('target file too large')
        value = json.loads(file.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        raise CaptureInputError('TARGET_FILE_INVALID') from None
    return value


def _common(parser):
    parser.add_argument('--targets', required=True, help='JSON mapping or list of movie_id/tmdb_id pairs')
    parser.add_argument('--archive', help='existing absolute non-C: archive root (execution only)')
    parser.add_argument('--job-id', default='plan', help='stable job identifier')
    parser.add_argument('--max-requests', required=True, type=int, help='cumulative request budget (1..10000)')
    parser.add_argument('--language', default='zh-CN')


def _print(value):
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _failure(error):
    print(json.dumps({'status': 'ERROR', 'code': getattr(error, 'code', 'ERROR')}, ensure_ascii=False), file=sys.stderr)
    return EXIT_INPUT


def _archive_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise IngestInputError('ARCHIVE_NOT_ABSOLUTE', 'archive must be an absolute path')
    if path.drive[:1].upper() == 'C':
        raise IngestInputError('ARCHIVE_ON_SYSTEM_DRIVE', 'archive must not live on the C: drive')
    if not path.is_dir():
        raise IngestInputError('ARCHIVE_MISSING', 'archive directory does not exist')
    return path


class _Adapter:
    """Expose a DB-API connection as ``execute(sql, params) -> cursor``."""

    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql, params=()):
        cursor = self._connection.cursor()
        cursor.execute(sql, params)
        return cursor

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()


def _connect(dsn_env):
    dsn = os.environ.get(dsn_env, '')
    if not dsn:
        raise IngestInputError('DSN_ENV_MISSING', 'database DSN environment variable is empty')
    from sqlalchemy import create_engine

    engine = create_engine(dsn, hide_parameters=True, pool_pre_ping=True)
    return engine, engine.raw_connection()


def _ingest(args):
    try:
        archive = _archive_path(args.archive)
        if args.command == 'ingest-plan':
            result = ingest_dry_run(str(archive))
            result['command'] = 'ingest-plan'
            _print(result)
            return EXIT_OK
        if not args.execute:
            return _failure(IngestInputError('EXECUTE_FLAG_REQUIRED', 'database writes require --execute'))
        engine, raw = _connect(args.dsn_env)
        try:
            result = ingest_run(_Adapter(raw), str(archive), job_ref=args.job_ref)
        finally:
            try:
                raw.close()
            finally:
                engine.dispose()
        result['command'] = 'ingest'
        _print(result)
        return EXIT_OK
    except IngestRejected as error:
        print(json.dumps({'status': 'REJECTED', 'code': error.code}, ensure_ascii=False), file=sys.stderr)
        return EXIT_INCOMPLETE
    except IngestError as error:
        return _failure(error)


def _trending(args):
    provider = lambda: os.environ.get(args.token_env, '')
    try:
        archive = _archive_path(args.archive) if args.execute else args.archive
        result = trending.capture_trending(
            str(archive),
            job_id=args.job_id,
            fetch=make_urllib_fetch(args.proxy) if args.execute else None,
            execute=args.execute,
            pages=args.pages,
            max_requests=args.max_requests,
            language=args.language,
            token=provider() if args.execute else None,
        )
    except CaptureError as error:
        return _failure(error)
    _print(result)
    return EXIT_OK if result.get('status') in ('COMPLETED', 'DRY_RUN') else EXIT_INCOMPLETE


def _board(args):
    try:
        archive = _archive_path(args.archive)
        if args.command == 'board-ingest-plan':
            _print(board_ingest.dry_run(str(archive)))
            return EXIT_OK
        if not args.execute:
            return _failure(IngestInputError('EXECUTE_FLAG_REQUIRED', 'database writes require --execute'))
        engine, raw = _connect(args.dsn_env)
        try:
            result = board_ingest.run(_Adapter(raw), str(archive), job_ref=args.job_ref)
        finally:
            try:
                raw.close()
            finally:
                engine.dispose()
        _print(result)
        return EXIT_OK
    except IngestRejected as error:
        print(json.dumps({'status': 'REJECTED', 'code': error.code}, ensure_ascii=False), file=sys.stderr)
        return EXIT_INCOMPLETE
    except IngestError as error:
        return _failure(error)
    except trending.TrendingArchiveError as error:
        return _failure(error)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Bounded TMDB supplement capture (dry-run by default)')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('plan', 'validate'):
        child = sub.add_parser(name, help='offline dry-run; never requests or writes')
        _common(child)
    capture = sub.add_parser('capture', help='explicit bounded execution after --execute')
    _common(capture)
    capture.add_argument('--execute', action='store_true', help='required explicit real-network switch')
    capture.add_argument('--token-env', default='TMDB_READ_TOKEN', help='environment variable name (never the value)')
    capture.add_argument('--proxy', help='explicit http(s) proxy for TMDB reads; omit to keep proxies disabled')
    capture.add_argument('--reuse-connection', action='store_true',
                         help='reuse one validated TLS tunnel instead of a handshake per request')
    ingest_plan = sub.add_parser('ingest-plan', help='offline dry-run of a capture archive; never touches a database')
    ingest_plan.add_argument('--archive', required=True, help='capture job archive directory')
    ingest_plan.add_argument('--job-ref', help='stable job reference (defaults to the archive job id)')
    ingest = sub.add_parser('ingest', help='explicit transactional write of an archive after --execute')
    ingest.add_argument('--archive', required=True, help='capture job archive directory')
    ingest.add_argument('--job-ref', help='stable job reference (defaults to the archive job id)')
    ingest.add_argument('--dsn-env', default='MI2_DATABASE_URL', help='environment variable holding the DSN (never the value)')
    ingest.add_argument('--execute', action='store_true', help='required explicit database write switch')
    trend = sub.add_parser('trending-capture', help='bounded day/week trending board capture')
    trend.add_argument('--archive', required=True, help='existing absolute non-C: archive root')
    trend.add_argument('--job-id', required=True, help='stable job identifier')
    trend.add_argument('--pages', type=int, default=2, choices=(1, 2), help='pages per channel (max 2)')
    trend.add_argument('--max-requests', type=int, default=trending.MAX_REQUESTS,
                       help='total request budget for this run (max %d)' % trending.MAX_REQUESTS)
    trend.add_argument('--language', default='zh-CN')
    trend.add_argument('--token-env', default='TMDB_READ_TOKEN')
    trend.add_argument('--proxy', help='explicit http(s) proxy; omit to keep proxies disabled')
    trend.add_argument('--execute', action='store_true', help='required explicit real-network switch')
    board_plan = sub.add_parser('board-ingest-plan', help='offline dry-run of a trending archive')
    board_plan.add_argument('--archive', required=True, help='trending job archive directory')
    board = sub.add_parser('board-ingest', help='explicit transactional write of a trending archive')
    board.add_argument('--archive', required=True, help='trending job archive directory')
    board.add_argument('--job-ref', help='stable job reference (defaults to the archive job id)')
    board.add_argument('--dsn-env', default='MI2_DATABASE_URL')
    board.add_argument('--execute', action='store_true', help='required explicit database write switch')
    args = parser.parse_args(argv)

    if args.command in ('ingest', 'ingest-plan'):
        return _ingest(args)
    if args.command in ('board-ingest', 'board-ingest-plan'):
        return _board(args)
    if args.command == 'trending-capture':
        return _trending(args)

    try:
        targets = _targets(args.targets)
    except CaptureError as error:
        return _failure(error)
    if args.command == 'capture':
        if not args.execute:
            return _failure(CaptureError('EXECUTE_FLAG_REQUIRED'))
        if not args.archive:
            return _failure(CaptureError('ARCHIVE_REQUIRED'))
        provider = lambda: os.environ.get(args.token_env, '')
        try:
            result = capture_movies(
                targets,
                fetch=make_keepalive_fetch(args.proxy) if args.reuse_connection
                else make_urllib_fetch(args.proxy),
                archive_dir=args.archive,
                job_id=args.job_id,
                max_requests=args.max_requests,
                execute=True,
                language=args.language,
                token_provider=provider,
            )
        except CaptureError as error:
            return _failure(error)
        _print(result)
        if result.get('status') == 'COMPLETED':
            return EXIT_OK
        if result.get('status') == 'AUTH_FAILED':
            return EXIT_AUTH
        return EXIT_INCOMPLETE

    try:
        result = capture_movies(
            targets,
            fetch=None,
            archive_dir=args.archive,
            job_id=args.job_id,
            max_requests=args.max_requests,
            execute=False,
            language=args.language,
        )
    except CaptureError as error:
        return _failure(error)
    result['command'] = args.command
    if args.command == 'validate':
        result['valid'] = True
    result['attribution'] = dict(ATTRIBUTION)
    _print(result)
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())