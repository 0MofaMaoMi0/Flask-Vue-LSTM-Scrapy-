"""Offline tests for Douban adapter, archive conversion and Scrapy-free verify/commit."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from catalog.ingest_hub import (  # noqa: E402
    HUB_CONTRACT,
    HubRejected,
    HubSecurityError,
    build_douban_archive,
    commit_archive,
    douban_item_to_envelope_record,
    load_douban_raw,
    run_capture,
    select_targets,
    summary_envelope,
    verify_archive,
)

NOW = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)


@contextmanager
def non_c_archive():
    base = REPO / '.local-cache' / 'test-douban-adapter'
    base.mkdir(parents=True, exist_ok=True)
    root = base / f'run-{uuid.uuid4().hex[:10]}'
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _run_cli(args):
    script = REPO / 'scripts' / 'data_collector.py'
    env = dict(os.environ)
    env.pop('TMDB_READ_TOKEN', None)
    env.pop('MI2_DATABASE_URL', None)
    env['PYTHONIOENCODING'] = 'utf-8'
    return subprocess.run(
        [sys.executable, str(script)] + list(args),
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        encoding='utf-8',
    )


def test_dirty_douban_movie_formats_normalize_and_pass_verify_ready():
    raw_items = [
        {
            'douban_id': '1292052',
            'type': 'movie',
            'name': '　肖申克的救赎　',
            'year': '(1994)',
            'release_date': '1994-09-10(多伦多电影节) / 1994-10-14(美国)',
            'mins': '142分钟(导演剪辑版)',
            'genres': '犯罪 / 剧情 / 剧情',
            'languages': '英语 / 西班牙语',
            'alias': '刺激1995(台) / 月黑高飞(港) / 刺激1995(台)',
            'directors': '弗兰克·德拉邦特',
            'director_ids': '弗兰克·德拉邦特:1047973',
            'actors': '蒂姆·罗宾斯 / 摩根·弗里曼 / 无ID演员',
            'actor_ids': '蒂姆·罗宾斯:1054521|摩根·弗里曼:1054534',
            'imdb_id': 'tt0111161',
            'douban_score': '9.7',
            'douban_votes': '3012345',
            'storyline': '一场关于希望的救赎故事。',
        },
        {
            # Zero votes must null score; invalid imdb_id must be filtered; mismatched date/year must drop date
            'douban_id': '3000001',
            'name': '待上映新片',
            'year': '2026',
            'release_date': '2025-11-01(电影节)',
            'mins': '98 分钟',
            'genres': '剧情',
            'languages': '汉语普通话',
            'directors': '张三',
            'director_ids': '',
            'imdb_id': 'not_valid_imdb',
            'douban_score': '8.5',
            'douban_votes': '0',
        },
    ]

    with non_c_archive() as root:
        built = build_douban_archive(
            raw_items,
            root,
            job_id='douban-clean-01',
            imported_at='2026-11-19T10:00:00Z',
        )
        assert built['counts'] == {'movies': 2, 'persons': 0, 'comments': 0, 'skipped': 0}

        env = json.loads(Path(built['envelope_path']).read_text(encoding='utf-8'))
        rec1, rec2 = env['records']

        assert rec1['source_record_id'] == 'douban:1292052'
        assert rec1['external_ids'] == {'douban': '1292052', 'imdb': 'tt0111161'}
        assert rec1['title'] == '肖申克的救赎'
        assert rec1['release_year'] == 1994
        assert rec1['release_date'] == '1994-09-10'
        assert rec1['runtime_minutes'] == 142
        assert rec1['language'] == '英语'
        assert rec1['genres'] == ['犯罪', '剧情']
        assert rec1['aliases'] == ['刺激1995(台)', '月黑高飞(港)']
        assert any(p == {'source': 'douban', 'external_id': '1047973', 'name': '弗兰克·德拉邦特', 'role': 'crew'} for p in rec1['people'])
        assert any(p['name'] == '无ID演员' and p['external_id'].startswith('name:') and p['role'] == 'cast' for p in rec1['people'])
        assert rec1['ratings'][0]['value'] == 9.7
        assert rec1['ratings'][0]['votes'] == 3012345

        assert rec2['external_ids'] == {'douban': '3000001'}
        assert rec2['release_year'] == 2026
        assert 'release_date' not in rec2
        assert rec2['ratings'][0]['votes'] == 0
        assert rec2['ratings'][0]['value'] is None

        receipt = verify_archive(built['archive_dir'], now=NOW)
        assert receipt['status'] == 'READY_TO_COMMIT'
        assert receipt['counts']['valid'] == 2
        assert receipt['counts']['rejected'] == 0
        assert receipt['counts']['quarantined'] == 0
        assert len(receipt['commit_seal']) == 64

        committed = commit_archive(
            built['archive_dir'],
            execute=True,
            seal=receipt['commit_seal'],
            now=NOW,
        )
        assert committed['status'] == 'COMMITTED'
        assert committed['idempotent'] is False
        replayed = commit_archive(
            built['archive_dir'],
            execute=True,
            seal=receipt['commit_seal'],
            now=NOW,
        )
        assert replayed['idempotent'] is True


def test_douban_conflicts_and_non_movie_items_surface_in_compact_summary():
    raw_items = [
        # Valid movie
        {'douban_id': '101', 'name': '正常电影', 'year': '2021', 'type': 'movie'},
        # Same douban_id with conflicting title/year -> quarantined
        {'douban_id': '202', 'name': '冲突片甲', 'year': '2020', 'type': 'movie'},
        {'douban_id': '202', 'name': '冲突片乙', 'year': '2022', 'type': 'movie'},
        # Non-movie TV items -> rejected (invalid_record)
        *[{'douban_id': str(900 + i), 'name': f'电视剧{i}', 'year': '2024', 'type': 'tv'} for i in range(6)],
    ]

    with non_c_archive() as root:
        built = build_douban_archive(raw_items, root, job_id='douban-anomalies-01')
        receipt = verify_archive(built['archive_dir'], now=NOW)
        assert receipt['status'] == 'NEEDS_REVIEW'
        assert receipt['counts']['valid'] == 1
        assert receipt['counts']['quarantined'] == 2
        assert receipt['counts']['rejected'] == 6

        compact = summary_envelope(receipt)
        assert compact['error_breakdown']['external_id_identity_conflict'] == 2
        assert compact['error_breakdown']['invalid_record'] == 6
        assert len(compact['anomaly_samples']) <= 5

        with pytest.raises(HubRejected) as exc:
            commit_archive(built['archive_dir'], execute=True, seal=receipt['commit_seal'], now=NOW)
        assert exc.value.code == 'REVIEW_REQUIRED'


def test_douban_supplementary_persons_and_comments_archived_and_sealed():
    raw_items = [
        {'douban_id': '555', 'name': '主电影', 'year': '2023', 'type': 'movie'},
        {
            'person_id': '1047973',
            'name': '弗兰克·德拉邦特',
            'sex': '男',
            'birth': '1959-01-28',
            'profession': '导演 / 编剧',
        },
        {
            'douban_id': '555',
            'douban_comment_id': 'c-9001',
            'douban_user_nickname': '影迷甲',
            'content': '非常震撼。',
            'votes': 42,
            'rating': '5',
            'comment_time': '2026-11-01 12:00:00',
        },
        {'douban_id': '555', 'type': 'movie'},  # Subject discovery stub -> skipped
    ]

    with non_c_archive() as root:
        built = build_douban_archive(raw_items, root, job_id='douban-sup-01')
        job_dir = Path(built['archive_dir'])
        assert built['counts'] == {'movies': 1, 'persons': 1, 'comments': 1, 'skipped': 1}
        persons_file = job_dir / 'supplementary' / 'persons.jsonl'
        comments_file = job_dir / 'supplementary' / 'comments.jsonl'
        assert persons_file.is_file()
        assert comments_file.is_file()
        assert json.loads(persons_file.read_text(encoding='utf-8').strip())['person_id'] == '1047973'
        assert json.loads(comments_file.read_text(encoding='utf-8').strip())['douban_comment_id'] == 'c-9001'

        receipt = verify_archive(job_dir, now=NOW)
        assert receipt['status'] == 'READY_TO_COMMIT'
        assert receipt['preview']['supplementary']['persons']['count'] == 1
        assert receipt['preview']['supplementary']['comments']['count'] == 1

        # Tampering supplementary/comments.jsonl must invalidate commit_seal
        comments_file.write_text('{"douban_comment_id":"tampered"}\n', encoding='utf-8')
        with pytest.raises(HubRejected) as exc:
            commit_archive(job_dir, execute=True, seal=receipt['commit_seal'], now=NOW)
        assert exc.value.code == 'SEAL_MISMATCH'


def test_douban_adapter_rejects_c_drive_and_invalid_dates_or_runtimes():
    with pytest.raises(HubSecurityError) as exc:
        build_douban_archive(
            [{'douban_id': '1', 'name': '测试'}],
            r'C:\Users\20876\bad-douban-archive',
            job_id='bad-c',
        )
    assert exc.value.code == 'ARCHIVE_ON_SYSTEM_DRIVE'

    # Invalid calendar date + out-of-range runtime + >2 decimal score are sanitized cleanly
    rec = douban_item_to_envelope_record(
        {
            'douban_id': '777',
            'name': '边界测试片',
            'year': '1700',
            'release_date': '2021-02-30 / 2021-03-15(中国)',
            'mins': '99999分钟',
            'douban_score': '8.456',
            'douban_votes': '100',
        },
        observed_at='2026-11-19T00:00:00Z',
    )
    assert rec['release_year'] == 2021
    assert rec['release_date'] == '2021-03-15'
    assert 'runtime_minutes' not in rec
    assert rec['ratings'][0]['value'] == 8.46


def test_douban_archive_source_select_capture_verify_commit_flow():
    with non_c_archive() as root:
        jsonl_path = root / 'raw-douban.jsonl'
        jsonl_path.write_text(
            '\n'.join([
                json.dumps({'douban_id': '801', 'name': '归档桥接片', 'year': '2022', 'genres': '科幻 / 冒险'}, ensure_ascii=False),
                json.dumps({'person_id': 'p801', 'name': '测试导演'}, ensure_ascii=False),
            ]) + '\n',
            encoding='utf-8',
        )

        plan = select_targets(source='douban_archive', job_id='bridge-01', input_path=str(jsonl_path))
        assert plan['request_budget'] == 0

        dry = run_capture(plan, archive_dir=str(root / 'archives'), execute=False)
        assert dry['status'] == 'DRY_RUN'
        assert not (root / 'archives' / 'bridge-01').exists()

        cap = run_capture(plan, archive_dir=str(root / 'archives'), execute=True)
        assert cap['status'] == 'COMPLETED'
        assert cap['counts'] == {'movies': 1, 'persons': 1, 'comments': 0, 'skipped': 0}

        receipt = verify_archive(cap['archive_dir'], now=NOW)
        assert receipt['status'] == 'READY_TO_COMMIT'
        committed = commit_archive(cap['archive_dir'], execute=True, seal=receipt['commit_seal'], now=NOW)
        assert committed['status'] == 'COMMITTED'


def test_load_douban_raw_supports_grouped_json_and_rejects_bad_formats():
    with non_c_archive() as root:
        grouped_file = root / 'grouped.json'
        grouped_file.write_text(
            json.dumps({
                'movies': [{'douban_id': '11', 'name': '片一', 'year': '2020'}],
                'persons': [{'person_id': 'p1', 'name': '影人一'}],
                'comments': [{'douban_id': '11', 'douban_comment_id': 'c1', 'content': '好'}],
            }, ensure_ascii=False),
            encoding='utf-8',
        )
        items = load_douban_raw(grouped_file)
        assert len(items) == 3

        built = build_douban_archive(items, root / 'out', job_id='grouped-01')
        assert built['counts'] == {'movies': 1, 'persons': 1, 'comments': 1, 'skipped': 0}


def test_cli_convert_douban_e2e_summary_and_strict_gate():
    with non_c_archive() as root:
        raw_json = root / 'douban-raw.json'
        raw_json.write_text(
            json.dumps([
                {
                    'douban_id': '1291561',
                    'name': '千与千寻',
                    'year': '2001',
                    'release_date': '2001-07-20(日本)',
                    'mins': '125分钟',
                    'genres': '剧情 / 动画 / 奇幻',
                    'languages': '日语',
                    'directors': '宫崎骏',
                    'director_ids': '宫崎骏:1054439',
                    'imdb_id': 'tt0245429',
                    'douban_score': '9.4',
                    'douban_votes': '2300000',
                },
                {
                    'person_id': '1054439',
                    'name': '宫崎骏',
                    'profession': '导演',
                },
            ], ensure_ascii=False),
            encoding='utf-8',
        )
        archives = root / 'archives'

        res = _run_cli([
            'convert-douban',
            '--input', str(raw_json),
            '--job-id', 'db-cli-01',
            '--archive', str(archives),
            '--strict',
            '--summary-only',
        ])
        assert res.returncode == 0, res.stderr
        assert len(res.stdout.splitlines()) < 30
        summary = json.loads(res.stdout)
        assert summary['contract'] == HUB_CONTRACT
        assert summary['status'] == 'READY_TO_COMMIT'
        assert summary['counts']['valid'] == 1
        assert summary['supplementary_counts'] == {'persons': 1, 'comments': 0}
        assert len(summary['commit_seal']) == 64

        # C: drive must be blocked
        bad_c = _run_cli([
            'convert-douban',
            '--input', str(raw_json),
            '--job-id', 'db-cli-bad',
            '--archive', r'C:\bad-douban',
        ])
        assert bad_c.returncode == 2
        assert 'ARCHIVE_ON_SYSTEM_DRIVE' in bad_c.stderr


def test_cli_convert_douban_strict_exits_3_on_rejected_or_review():
    with non_c_archive() as root:
        raw_json = root / 'douban-tv-only.json'
        raw_json.write_text(
            json.dumps([{'douban_id': '999', 'name': '仅电视剧', 'year': '2025', 'type': 'tv'}], ensure_ascii=False),
            encoding='utf-8',
        )
        res = _run_cli([
            'convert-douban',
            '--input', str(raw_json),
            '--job-id', 'db-cli-rej',
            '--archive', str(root / 'archives'),
            '--strict',
            '--summary-only',
        ])
        assert res.returncode == 3
        summary = json.loads(res.stdout)
        assert summary['status'] == 'REJECTED'
        assert summary['counts']['rejected'] == 1
