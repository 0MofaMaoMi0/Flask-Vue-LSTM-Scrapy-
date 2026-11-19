#!/usr/bin/env python
"""Unified dry-run-first CLI for select -> capture -> verify -> commit.

All credentials and database DSNs are accepted strictly as environment variable
names (``--token-env``, ``--dsn-env``). Archive and plan-output paths must be
existing or creatable absolute non-C: directories without symlinks/reparse
points. Database writes require ``--execute`` and a matching ``commit_seal``.
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

from catalog import data_pipeline as _data_pipeline  # noqa: E402
from catalog.ingest_hub import (  # noqa: E402
    HUB_CONTRACT,
    RULE_VERSION,
    SUPPORTED_SOURCES,
    HubError,
    HubInputError,
    HubRejected,
    build_douban_archive,
    commit_archive,
    discover_catalog_targets,
    load_douban_raw,
    load_plan,
    run_capture,
    select_targets,
    summary_envelope,
    validate_archive_dir,
    validate_env_name,
    verify_archive,
)

def _configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8')
            except Exception:
                pass


EXIT_OK = 0
EXIT_INPUT = 2
EXIT_INCOMPLETE = 3
EXIT_AUTH = 4


class _Adapter:
    """Expose an SQLAlchemy raw DB-API connection as ``execute(sql, params) -> cursor``."""

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


def _connect_sqlalchemy(dsn_env):
    validate_env_name(dsn_env, 'dsn_env')
    dsn = os.environ.get(dsn_env, '')
    if not dsn:
        raise HubInputError('DSN_ENV_MISSING', f'environment variable {dsn_env} is empty')
    from sqlalchemy import create_engine

    return create_engine(dsn, hide_parameters=True, pool_pre_ping=True)


def _print_result(payload, *, summary_only=False):
    if summary_only:
        compact = summary_envelope(payload) if 'counts' in payload else payload
        pretty = json.dumps(compact, ensure_ascii=False, sort_keys=True, indent=2)
        if pretty.count('\n') + 1 < 30:
            print(pretty)
        else:
            print(json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
        return
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


def _failure(error, *, exit_code=EXIT_INPUT):
    payload = {
        'status': 'REJECTED' if isinstance(error, HubRejected) else 'ERROR',
        'code': getattr(error, 'code', 'ERROR'),
    }
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    return exit_code


def _load_json_arg(path_str):
    if not path_str:
        return None
    p = Path(path_str)
    if not p.is_file():
        raise HubInputError('FILE_MISSING', f'file not found: {p.name}')
    return json.loads(p.read_text(encoding='utf-8-sig'))


def _select_kwargs_from_args(args):
    documents = _load_json_arg(getattr(args, 'documents', None))
    targets = getattr(args, 'targets', None)
    if isinstance(targets, list) and len(targets) == 1 and targets[0].endswith('.json'):
        targets = targets[0]
    tmdb_ids = getattr(args, 'tmdb_ids', None)
    if isinstance(tmdb_ids, list) and len(tmdb_ids) == 1 and (',' in tmdb_ids[0] or tmdb_ids[0].lower().endswith('.json')):
        tmdb_ids = tmdb_ids[0]
    elif isinstance(tmdb_ids, list):
        parsed_ids = []
        for item in tmdb_ids:
            if not str(item).isdigit():
                raise HubInputError('TMDB_IDS_INVALID', 'tmdb_ids must be positive integers')
            parsed_ids.append(int(item))
        tmdb_ids = parsed_ids

    return {
        'source': args.source,
        'scope': getattr(args, 'scope', None),
        'job_id': args.job_id,
        'documents': documents,
        'targets': targets,
        'tmdb_ids': tmdb_ids,
        'topics': getattr(args, 'topics', None),
        'limit': getattr(args, 'limit', None),
        'max_requests': getattr(args, 'max_requests', None),
        'before': getattr(args, 'before', None),
        'pages': getattr(args, 'pages', 2),
        'language': getattr(args, 'language', 'zh-CN'),
        'input_path': getattr(args, 'input', None),
        'metadata': getattr(args, 'metadata', None),
        'output_plan_path': getattr(args, 'out_plan', None),
    }


def _cmd_select(args):
    kwargs = _select_kwargs_from_args(args)
    dsn_env = getattr(args, 'dsn_env', None)
    if dsn_env:
        engine = _connect_sqlalchemy(dsn_env)
        try:
            with engine.connect() as conn:
                plan = select_targets(connection=conn, **kwargs)
        finally:
            engine.dispose()
    else:
        plan = select_targets(**kwargs)
    _print_result(plan, summary_only=args.summary_only)
    return EXIT_OK


def _cmd_capture(args):
    validate_env_name(args.token_env, 'token_env')
    validate_archive_dir(args.archive, must_exist=False, create=False)
    if args.plan:
        plan = load_plan(args.plan)
    else:
        if not args.source or not args.job_id:
            raise HubInputError('PLAN_OR_SOURCE_REQUIRED', 'provide --plan or (--source and --job-id)')
        plan = select_targets(**_select_kwargs_from_args(args))
    result = run_capture(
        plan,
        archive_dir=args.archive,
        execute=bool(args.execute),
        token_env=args.token_env,
        proxy=args.proxy,
        reuse_connection=bool(args.reuse_connection),
    )
    _print_result(result, summary_only=args.summary_only)
    st = result.get('status')
    if st in ('DRY_RUN', 'COMPLETED', 'EMPTY_PLAN'):
        return EXIT_OK
    if st == 'AUTH_FAILED':
        return EXIT_AUTH
    return EXIT_INCOMPLETE


def _cmd_verify(args):
    validate_archive_dir(args.archive, must_exist=True)
    dsn_env = getattr(args, 'dsn_env', None)
    if dsn_env:
        engine = _connect_sqlalchemy(dsn_env)
        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                schema = os.environ.get('MI2_M6_SCHEMA') or conn.execute(text('SELECT DATABASE()')).scalar()
                receipt = verify_archive(
                    args.archive,
                    source=args.source,
                    plan=args.plan,
                    connection=conn,
                    catalog_config={'M6_SCHEMA': schema} if schema else None,
                )
        finally:
            engine.dispose()
    else:
        receipt = verify_archive(args.archive, source=args.source, plan=args.plan)
    _print_result(receipt, summary_only=args.summary_only)
    if args.strict and receipt.get('status') != 'READY_TO_COMMIT':
        return EXIT_INCOMPLETE
    return EXIT_OK


def _cmd_commit(args):
    if not args.execute:
        raise HubInputError('EXECUTE_FLAG_REQUIRED', 'database/archive commits require --execute')
    validate_archive_dir(args.archive, must_exist=True)
    validate_env_name(args.dsn_env, 'dsn_env')

    # Inspect source before opening DB if possible; local_envelope can run without DSN unless env set.
    pre_receipt = verify_archive(args.archive, source=args.source, write_receipt=False)
    source = pre_receipt['source']
    has_dsn = bool(os.environ.get(args.dsn_env, ''))
    if source == 'local_envelope' and not has_dsn:
        result = commit_archive(
            args.archive,
            source=source,
            execute=True,
            seal=args.seal,
            job_ref=args.job_ref,
        )
        _print_result(result, summary_only=args.summary_only)
        return EXIT_OK

    engine = _connect_sqlalchemy(args.dsn_env)
    try:
        if source == 'tmdb_catalog':
            from sqlalchemy import text
            with engine.connect() as conn:
                schema = os.environ.get('MI2_M6_SCHEMA') or conn.execute(text('SELECT DATABASE()')).scalar()
            result = commit_archive(
                args.archive,
                source=source,
                execute=True,
                seal=args.seal,
                connection=engine,
                catalog_config={'M6_SCHEMA': schema} if schema else {},
                job_ref=args.job_ref,
            )
        else:
            raw = engine.raw_connection()
            try:
                result = commit_archive(
                    args.archive,
                    source=source,
                    execute=True,
                    seal=args.seal,
                    connection=_Adapter(raw),
                    job_ref=args.job_ref,
                )
            finally:
                raw.close()
    finally:
        engine.dispose()
    _print_result(result, summary_only=args.summary_only)
    return EXIT_OK


def _cmd_pipeline_preview(args):
    validate_archive_dir(args.archive, must_exist=False, create=False)
    if args.plan:
        plan = load_plan(args.plan)
    else:
        if not args.source or not args.job_id:
            raise HubInputError('PLAN_OR_SOURCE_REQUIRED', 'provide --plan or (--source and --job-id)')
        kwargs = _select_kwargs_from_args(args)
        if args.dsn_env:
            engine = _connect_sqlalchemy(args.dsn_env)
            try:
                with engine.connect() as conn:
                    plan = select_targets(connection=conn, **kwargs)
            finally:
                engine.dispose()
        else:
            plan = select_targets(**kwargs)

    capture_preview = run_capture(plan, archive_dir=args.archive, execute=False, token_env=args.token_env)
    job_dir = Path(args.archive) / plan['job_id']
    verify_preview = None
    if job_dir.is_dir() and ((job_dir / 'manifest.json').is_file() or (job_dir / 'envelope.json').is_file()):
        verify_preview = verify_archive(job_dir, source=plan['source'], plan=plan, write_receipt=False)
    elif plan['source'] == 'local_envelope':
        target = plan['targets'][0]
        built = _data_pipeline.build(_data_pipeline.read_input(target['input_path'], target.get('metadata')))
        q = built['quality']
        verify_preview = {
            'contract': HUB_CONTRACT,
            'rule_version': RULE_VERSION,
            'source': 'local_envelope',
            'job_id': plan['job_id'],
            'status': ('REJECTED' if q['accepted_movies'] == 0 else ('NEEDS_REVIEW' if (q['quarantined'] or q['review']) else 'READY_TO_COMMIT')),
            'counts': {
                'total_input': int(q['input']),
                'valid': int(q['accepted_movies']),
                'rejected': int(q['rejected']),
                'quarantined': int(q['quarantined']),
                'needs_review': int(q['review']),
                'duplicates': int(q['duplicates']),
            },
        }
    out = {
        'contract': HUB_CONTRACT,
        'rule_version': RULE_VERSION,
        'command': 'pipeline-preview',
        'plan': {
            'source': plan['source'],
            'scope': plan['scope'],
            'job_id': plan['job_id'],
            'estimated_requests': plan['estimated_requests'],
            'request_budget': plan['request_budget'],
            'plan_digest': plan['plan_digest'],
        },
        'capture': capture_preview,
        'verify': summary_envelope(verify_preview) if verify_preview else None,
    }
    _print_result(out, summary_only=args.summary_only)
    return EXIT_OK


def _cmd_convert_douban(args):
    validate_archive_dir(args.archive, must_exist=False, create=False)
    raw_items = load_douban_raw(args.input)
    built = build_douban_archive(
        raw_items,
        args.archive,
        job_id=args.job_id,
        license_status=args.license_status,
        license_ref=args.license_ref,
        imported_at=args.imported_at,
        source_input_path=args.input,
    )
    receipt = verify_archive(built['archive_dir'], source='local_envelope')
    if args.summary_only:
        compact = summary_envelope(receipt)
        compact['archive_dir'] = built['archive_dir']
        compact['supplementary_counts'] = {
            'persons': built['counts']['persons'],
            'comments': built['counts']['comments'],
        }
        pretty = json.dumps(compact, ensure_ascii=False, sort_keys=True, indent=2)
        if pretty.count('\n') + 1 < 30:
            print(pretty)
        else:
            print(json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
    else:
        _print_result({
            'contract': HUB_CONTRACT,
            'rule_version': RULE_VERSION,
            'command': 'convert-douban',
            'archive': built,
            'verify': receipt,
        }, summary_only=False)
    if args.strict and receipt.get('status') != 'READY_TO_COMMIT':
        return EXIT_INCOMPLETE
    return EXIT_OK


def _cmd_discover(args):
    validate_env_name(args.token_env, 'token_env')
    if args.out_plan:
        validate_archive_dir(Path(args.out_plan).parent, must_exist=False, create=False)
    kwargs = {
        'job_id': args.job_id,
        'preset': args.preset,
        'limit': args.limit,
        'language': args.language,
        'start_page': args.start_page,
        'max_pages': args.max_pages,
        'max_requests': args.max_requests,
        'catalog_max_requests': args.catalog_max_requests,
        'sort_by': args.sort_by,
        'min_vote_count': args.min_vote_count,
        'min_vote_average': args.min_vote_average,
        'existing_ids': args.existing_ids,
        'execute': bool(args.execute),
        'token_env': args.token_env,
        'proxy': args.proxy,
        'reuse_connection': bool(args.reuse_connection),
        'output_plan_path': args.out_plan,
    }
    if args.dsn_env:
        engine = _connect_sqlalchemy(args.dsn_env)
        try:
            with engine.connect() as conn:
                result = discover_catalog_targets(connection=conn, **kwargs)
        finally:
            engine.dispose()
    else:
        result = discover_catalog_targets(**kwargs)

    if args.summary_only:
        compact = {
            k: result[k]
            for k in (
                'contract',
                'rule_version',
                'command',
                'job_id',
                'mode',
                'status',
                'preset',
                'language',
                'requests_used',
                'request_budget',
                'pages_scanned',
                'counts',
                'output_plan_path',
                'plan_digest',
                'error_code',
            )
            if k in result and result[k] is not None
        }
        pretty = json.dumps(compact, ensure_ascii=False, sort_keys=True, indent=2)
        if pretty.count('\n') + 1 < 30:
            print(pretty)
        else:
            print(json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
    else:
        _print_result(result, summary_only=False)

    st = result.get('status')
    if st in ('DRY_RUN', 'COMPLETED'):
        return EXIT_OK
    if st == 'PARTIAL':
        return EXIT_INCOMPLETE if args.strict else EXIT_OK
    if st == 'AUTH_FAILED':
        return EXIT_AUTH
    return EXIT_INCOMPLETE


def _add_select_options(parser, *, required_source=True):
    parser.add_argument('--source', choices=SUPPORTED_SOURCES, required=required_source)
    parser.add_argument('--scope', help='missing | explicit | stale | boards | catalog | envelope | douban')
    parser.add_argument('--job-id', required=required_source, help='stable job identifier')
    parser.add_argument('--targets', nargs='+', help='explicit movie_id:tmdb_id pairs or JSON file path')
    parser.add_argument('--tmdb-ids', nargs='+', help='1..500 TMDB IDs (ids, comma string, or .json path) for tmdb_catalog')
    parser.add_argument('--topics', help='comma-separated supplement topics')
    parser.add_argument('--limit', type=int, help='max movie count')
    parser.add_argument('--max-requests', type=int, help='request budget')
    parser.add_argument('--before', help='UTC ISO-8601 cutoff for stale scope')
    parser.add_argument('--pages', type=int, default=2, choices=(1, 2), help='pages per channel for trending')
    parser.add_argument('--language', default='zh-CN')
    parser.add_argument('--input', help='local JSON/CSV/JSONL path for local_envelope or douban_archive')
    parser.add_argument('--metadata', help='metadata JSON path for CSV local_envelope')
    parser.add_argument('--documents', help='offline normalized docs JSON path for select without DB')
    parser.add_argument('--dsn-env', help='environment variable name holding read-only DSN')


def build_parser():
    parser = argparse.ArgumentParser(description='Unified dry-run-first data collection and ingestion CLI')
    sub = parser.add_subparsers(dest='command', required=True)

    p_select = sub.add_parser('select', help='select targets and emit budgeted job-plan.json')
    _add_select_options(p_select, required_source=True)
    p_select.add_argument('--out-plan', help='non-C: absolute output path for job-plan.json')
    p_select.add_argument('--summary-only', action='store_true')

    p_disc = sub.add_parser('discover', help='discover deduplicated out-of-catalog TMDB movies and emit job-plan.json')
    p_disc.add_argument('--job-id', required=True, help='stable job identifier')
    p_disc.add_argument('--preset', default='popular', choices=('popular', 'top_rated', 'discover'))
    p_disc.add_argument('--limit', type=int, default=20, help='target new movie count (1..500)')
    p_disc.add_argument('--language', default='zh-CN')
    p_disc.add_argument('--start-page', type=int, default=1)
    p_disc.add_argument('--max-pages', type=int, default=20)
    p_disc.add_argument('--max-requests', type=int, help='discovery list request budget')
    p_disc.add_argument('--catalog-max-requests', type=int, help='capture request budget embedded into job-plan.json')
    p_disc.add_argument('--sort-by', default='popularity.desc', choices=('popularity.desc', 'vote_average.desc', 'vote_count.desc'))
    p_disc.add_argument('--min-vote-count', type=int)
    p_disc.add_argument('--min-vote-average', type=float)
    p_disc.add_argument('--existing-ids', help='JSON file of existing TMDB IDs for offline dedup')
    p_disc.add_argument('--dsn-env', help='environment variable name holding read-only DSN')
    p_disc.add_argument('--token-env', default='TMDB_READ_TOKEN')
    p_disc.add_argument('--proxy', help='explicit http(s) proxy; omit to keep proxies disabled')
    p_disc.add_argument('--reuse-connection', action='store_true')
    p_disc.add_argument('--out-plan', help='non-C: absolute output path for job-plan.json')
    p_disc.add_argument('--execute', action='store_true', help='required to perform network discovery and write plan')
    p_disc.add_argument('--strict', action='store_true', help='exit 3 if selected count < limit')
    p_disc.add_argument('--summary-only', action='store_true')

    p_capture = sub.add_parser('capture', help='bounded capture into archive_dir (dry-run by default)')
    p_capture.add_argument('--plan', help='path to job-plan.json')
    _add_select_options(p_capture, required_source=False)
    p_capture.add_argument('--archive', required=True, help='non-C: absolute archive root directory')
    p_capture.add_argument('--token-env', default='TMDB_READ_TOKEN', help='env var name holding TMDB token')
    p_capture.add_argument('--proxy', help='explicit http(s) proxy; omit to keep proxies disabled')
    p_capture.add_argument('--reuse-connection', action='store_true')
    p_capture.add_argument('--execute', action='store_true', help='required to perform network/archive writes')
    p_capture.add_argument('--summary-only', action='store_true')

    p_verify = sub.add_parser('verify', help='pure offline verification and compact summary generation')
    p_verify.add_argument('--archive', required=True, help='non-C: absolute archive job directory')
    p_verify.add_argument('--source', choices=SUPPORTED_SOURCES)
    p_verify.add_argument('--plan', help='optional job-plan.json path')
    p_verify.add_argument('--dsn-env', help='optional read-only DSN env var for tmdb_catalog conflict preview')
    p_verify.add_argument('--strict', action='store_true', help='exit 3 unless status == READY_TO_COMMIT')
    p_verify.add_argument('--summary-only', action='store_true')

    p_commit = sub.add_parser('commit', help='seal-verified idempotent single-transaction commit')
    p_commit.add_argument('--archive', required=True, help='non-C: absolute archive job directory')
    p_commit.add_argument('--source', choices=SUPPORTED_SOURCES)
    p_commit.add_argument('--seal', help='expected commit_seal SHA256 (defaults to verification-receipt.json)')
    p_commit.add_argument('--dsn-env', default='MI2_DATABASE_URL', help='env var name holding DSN')
    p_commit.add_argument('--job-ref', help='stable idempotence job_ref')
    p_commit.add_argument('--execute', action='store_true', help='required explicit database commit switch')
    p_commit.add_argument('--summary-only', action='store_true')

    p_prev = sub.add_parser('pipeline-preview', help='dry-run serial preview of select -> capture -> verify')
    p_prev.add_argument('--plan', help='optional job-plan.json path')
    _add_select_options(p_prev, required_source=False)
    p_prev.add_argument('--archive', required=True, help='non-C: absolute archive root directory')
    p_prev.add_argument('--token-env', default='TMDB_READ_TOKEN')
    p_prev.add_argument('--summary-only', action='store_true')

    p_douban = sub.add_parser('convert-douban', help='convert raw Douban JSON/JSONL to standard archive and verify')
    p_douban.add_argument('--input', required=True, help='raw Douban .json or .jsonl path')
    p_douban.add_argument('--job-id', required=True, help='stable job identifier')
    p_douban.add_argument('--archive', required=True, help='non-C: absolute archive root directory')
    p_douban.add_argument('--license-status', default='allowed', choices=('allowed', 'unknown', 'restricted'))
    p_douban.add_argument('--license-ref', default='douban-academic-local-archive')
    p_douban.add_argument('--imported-at', help='optional UTC ISO-8601 imported_at timestamp')
    p_douban.add_argument('--strict', action='store_true', help='exit 3 unless status == READY_TO_COMMIT')
    p_douban.add_argument('--summary-only', action='store_true')

    return parser


def main(argv=None):
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == 'select':
            return _cmd_select(args)
        if args.command == 'discover':
            return _cmd_discover(args)
        if args.command == 'capture':
            return _cmd_capture(args)
        if args.command == 'verify':
            return _cmd_verify(args)
        if args.command == 'commit':
            return _cmd_commit(args)
        if args.command == 'convert-douban':
            return _cmd_convert_douban(args)
        return _cmd_pipeline_preview(args)
    except HubRejected as exc:
        return _failure(exc, exit_code=EXIT_INCOMPLETE)
    except HubError as exc:
        return _failure(exc, exit_code=EXIT_INPUT)


if __name__ == '__main__':
    sys.exit(main())
