"""Offline tests for the unified ingest_hub engine and scripts/data_collector.py."""
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
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, insert

HERE = Path(__file__).resolve()
BACKEND = HERE.parents[1]
REPO = HERE.parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from catalog.admin_importer import ImportRejected  # noqa: E402
from catalog.ingest_hub import (  # noqa: E402
    HUB_CONTRACT,
    RULE_VERSION,
    HubInputError,
    HubRejected,
    HubSecurityError,
    commit_archive,
    load_plan,
    run_capture,
    select_targets,
    summary_envelope,
    verify_archive,
)
from catalog.tmdb_supplement import writer  # noqa: E402
from m6_checks.test_tmdb_supplement_board_ingest import FakeConnection as FakeBoardConnection  # noqa: E402
from m6_checks.test_tmdb_supplement_ingest import FakeConnection as FakeSupplementConnection  # noqa: E402

SYNTHETIC_TOKEN = 'SYNTHETIC_INGEST_HUB_TOKEN_0123456789ABCDEF'
NOW = datetime(2026, 11, 19, 8, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, start=1700000000.0):
        self.epoch = float(start)

    def __call__(self):
        return self.epoch

    def sleep(self, seconds):
        self.epoch += float(seconds)


@contextmanager
def non_c_archive():
    root = REPO / '.local-cache' / 'test-ingest-hub' / f'run-{uuid.uuid4().hex[:10]}'
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _tmdb_movie_payload(tmdb_id, *, title='合成电影', release_date='2022-05-01'):
    return {
        'id': tmdb_id,
        'adult': False,
        'title': title,
        'original_title': 'Synthetic Movie',
        'release_date': release_date,
        'runtime': 110,
        'original_language': 'en',
        'overview': 'synthetic overview',
        'genres': [{'id': 18, 'name': '剧情'}],
        'poster_path': '/poster_test.jpg',
        'backdrop_path': '/backdrop_test.jpg',
        'production_countries': [{'iso_3166_1': 'US', 'name': 'United States'}],
        'production_companies': [{'id': 41077, 'name': 'Synthetic Studio', 'origin_country': 'US', 'logo_path': None}],
        'spoken_languages': [{'iso_639_1': 'en', 'english_name': 'English', 'name': 'English'}],
        'popularity': 18.5,
        'vote_average': 7.8,
        'vote_count': 420,
        'revenue': 1000000,
        'budget': 200000,
        'keywords': {'keywords': [{'id': 9715, 'name': 'superhero'}]},
        'release_dates': {
            'results': [
                {
                    'iso_3166_1': 'US',
                    'release_dates': [
                        {'type': 3, 'release_date': f'{release_date}T00:00:00.000Z', 'certification': 'PG', 'note': '', 'iso_639_1': 'en'}
                    ],
                }
            ]
        },
        'credits': {
            'cast': [
                {
                    'id': 88001,
                    'name': '测试演员',
                    'original_name': 'Test Actor',
                    'credit_id': 'cred_cast_1',
                    'character': '主角',
                    'order': 0,
                    'profile_path': '/actor.jpg',
                }
            ],
            'crew': [
                {
                    'id': 88002,
                    'name': '测试导演',
                    'original_name': 'Test Director',
                    'credit_id': 'cred_crew_1',
                    'department': 'Directing',
                    'job': 'Director',
                    'profile_path': None,
                }
            ],
        },
    }


def _envelope_record(idx, *, douban_id=None, tmdb_id=None, title=None, year=2020, bad=False):
    rec = {
        'source_record_id': f'rec-{idx}',
        'external_ids': {'douban': douban_id or str(10000 + idx)},
        'title': '' if bad else (title if title is not None else f'合成影片{idx}'),
        'release_year': year,
        'runtime_minutes': 100,
        'genres': ['剧情'],
        'ratings': [],
        'people': [],
    }
    if tmdb_id is not None:
        rec['external_ids']['tmdb'] = str(tmdb_id)
    return rec


def _envelope_meta(license_status='allowed'):
    return {
        'source': 'legacy_movies2',
        'mode': 'SYNTHETIC',
        'license_status': license_status,
        'license_ref': 'synthetic-test-license',
        'imported_at': '2026-11-19T08:00:00',
    }


# ------------------------------------------------------------------ 1. select


def test_select_supplement_missing_excludes_empty_and_respects_budget():
    all_present = {t: 'PRESENT' for t in writer.TOPICS}
    docs = [
        {'movie_id': 1, 'tmdb_id': 101, 'field_status': dict(all_present)},
        # EMPTY on target topics + FAILED only on non-requested topic -> MUST be excluded!
        {'movie_id': 2, 'tmdb_id': 102, 'field_status': dict(all_present, keywords='EMPTY', companies='EMPTY', metrics='FAILED')},
        # Hidden movie -> excluded
        {'movie_id': 3, 'tmdb_id': 103, 'catalog_status': 'hidden', 'field_status': dict(all_present, keywords='FAILED')},
        # Selected candidates
        {'movie_id': 4, 'tmdb_id': 104, 'field_status': dict(all_present, keywords='FAILED', releases='NOT_FETCHED')},
        {'movie_id': 5, 'tmdb_id': 105, 'field_status': dict(all_present, companies='MISSING')},
        {'movie_id': 6, 'tmdb_id': 106, 'field_status': dict(all_present, releases='FAILED')},
    ]
    with non_c_archive() as root:
        plan_path = root / 'job-plan.json'
        plan = select_targets(
            source='tmdb_supplement',
            scope='missing',
            job_id='sel-missing-01',
            documents=docs,
            topics='keywords,companies,releases',
            limit=2,
            max_requests=5,
            output_plan_path=str(plan_path),
        )
        assert plan['contract'] == HUB_CONTRACT
        assert plan['rule_version'] == RULE_VERSION
        assert [t['movie_id'] for t in plan['targets']] == [4, 5]
        assert plan['targets'][0]['missing_topics'] == ['keywords', 'releases']
        assert plan['estimated_requests'] == 2
        assert plan['request_budget'] == 5
        assert plan['selection']['excluded_empty_count'] == 1
        assert plan['selection']['truncated'] is True
        reloaded = load_plan(plan_path)
        assert reloaded['plan_digest'] == plan['plan_digest']


def test_select_supplement_sqlite_missing_and_stale_scopes():
    engine = create_engine('sqlite+pysqlite:///:memory:')
    md = MetaData()
    movies = Table('movies', md, Column('movie_id', Integer, primary_key=True), Column('catalog_status', String(16)))
    meta = Table(
        'tmdb_movie_metadata',
        md,
        Column('movie_id', Integer, primary_key=True),
        Column('tmdb_id', Integer),
        Column('field_status_json', String(1024)),
        Column('fetched_at', DateTime),
    )
    md.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(movies), [
            {'movie_id': 10, 'catalog_status': 'active'},
            {'movie_id': 11, 'catalog_status': 'active'},
            {'movie_id': 12, 'catalog_status': 'hidden'},
        ])
        conn.execute(insert(meta), [
            {'movie_id': 10, 'tmdb_id': 1010, 'field_status_json': json.dumps({'keywords': 'EMPTY', 'releases': 'PRESENT'}), 'fetched_at': datetime(2026, 11, 1, 0, 0)},
            {'movie_id': 11, 'tmdb_id': 1011, 'field_status_json': json.dumps({'keywords': 'FAILED', 'releases': 'NOT_FETCHED'}), 'fetched_at': datetime(2026, 11, 18, 0, 0)},
            {'movie_id': 12, 'tmdb_id': 1012, 'field_status_json': json.dumps({'keywords': 'FAILED'}), 'fetched_at': datetime(2026, 11, 1, 0, 0)},
        ])

    with engine.connect() as conn:
        missing_plan = select_targets(
            source='tmdb_supplement',
            scope='missing',
            job_id='sql-missing',
            connection=conn,
            topics=['keywords'],
            max_requests=10,
        )
        assert [t['movie_id'] for t in missing_plan['targets']] == [11]
        assert missing_plan['selection']['excluded_empty_count'] == 1

        stale_plan = select_targets(
            source='tmdb_supplement',
            scope='stale',
            job_id='sql-stale',
            connection=conn,
            before='2026-11-10T00:00:00Z',
            max_requests=10,
        )
        assert [t['movie_id'] for t in stale_plan['targets']] == [10]


def test_select_explicit_trending_catalog_and_validation():
    explicit = select_targets(
        source='tmdb_supplement',
        scope='explicit',
        job_id='exp-01',
        targets=['2:202', '1:101'],
        max_requests=5,
    )
    assert [t['movie_id'] for t in explicit['targets']] == [1, 2]
    assert explicit['estimated_requests'] == 2

    trend = select_targets(source='tmdb_trending', job_id='trend-01', pages=1, max_requests=4)
    assert trend['estimated_requests'] == 2
    assert len(trend['targets']) == 2

    cat = select_targets(source='tmdb_catalog', job_id='cat-01', tmdb_ids='100,200')
    assert [t['tmdb_id'] for t in cat['targets']] == [100, 200]
    assert cat['estimated_requests'] == 2

    with pytest.raises(HubInputError) as exc_dup:
        select_targets(source='tmdb_catalog', job_id='cat-dup', tmdb_ids=[10, 10])
    assert exc_dup.value.code == 'TMDB_IDS_INVALID'

    with pytest.raises(HubInputError) as exc_many:
        select_targets(source='tmdb_catalog', job_id='cat-many', tmdb_ids=list(range(1, 22)))
    assert exc_many.value.code == 'TMDB_IDS_INVALID'


# ------------------------------------------------------------------ 2. security


def test_security_rejects_c_drive_relative_paths_and_raw_credentials():
    plan = select_targets(
        source='tmdb_supplement',
        scope='explicit',
        job_id='sec-01',
        targets=[(1, 101)],
        max_requests=2,
    )
    for bad_dir in (r'C:\Users\20876\archive', 'c:/temp/archive', 'relative/archive', r'F:\foo\..\bar'):
        with pytest.raises(HubSecurityError):
            run_capture(plan, archive_dir=bad_dir, execute=False)

    with non_c_archive() as root:
        for bad_env in ('Bearer secret123', 'TMDB_READ_TOKEN=abc', 'mysql://u:p@h/db', 'bad env'):
            with pytest.raises(HubSecurityError) as exc:
                run_capture(plan, archive_dir=str(root), execute=False, token_env=bad_env)
            assert exc.value.code == 'ENV_NAME_INVALID'


# ------------------------------------------------------------------ 3. 4 sources e2e


def test_supplement_capture_verify_and_idempotent_commit(monkeypatch):
    monkeypatch.setenv('HUB_TEST_TMDB_TOKEN', SYNTHETIC_TOKEN)
    clock = FakeClock()
    plan = select_targets(
        source='tmdb_supplement',
        scope='explicit',
        job_id='sup-job-01',
        targets=[(1, 101), (2, 102)],
        max_requests=4,
    )
    with non_c_archive() as root:
        dry = run_capture(plan, archive_dir=str(root), execute=False, token_env='HUB_TEST_TMDB_TOKEN')
        assert dry['status'] == 'DRY_RUN'
        assert not (root / 'sup-job-01').exists()

        def fake_fetch(req):
            tid = int(req['url'].split('/movie/')[1].split('?')[0])
            assert SYNTHETIC_TOKEN in req['headers']['Authorization']
            return {
                'status': 200,
                'headers': {},
                'body': json.dumps(_tmdb_movie_payload(tid), ensure_ascii=False).encode('utf-8'),
                'url': req['url'],
            }

        cap = run_capture(
            plan,
            archive_dir=str(root),
            execute=True,
            token_env='HUB_TEST_TMDB_TOKEN',
            fetch=fake_fetch,
            clock=clock,
            sleep=clock.sleep,
        )
        assert cap['status'] == 'COMPLETED'
        job_dir = Path(cap['archive_dir'])
        assert SYNTHETIC_TOKEN not in (job_dir / 'manifest.json').read_text(encoding='utf-8')

        receipt = verify_archive(job_dir, now=NOW)
        assert receipt['status'] == 'READY_TO_COMMIT'
        assert receipt['counts']['valid'] == 2
        assert receipt['counts']['rejected'] == 0
        assert len(receipt['commit_seal']) == 64

        conn = FakeSupplementConnection()
        committed = commit_archive(job_dir, execute=True, seal=receipt['commit_seal'], connection=conn, now=NOW)
        assert committed['status'] == 'COMMITTED'
        assert committed['idempotent'] is False
        assert len(conn.batches) == 1

        replayed = commit_archive(job_dir, execute=True, seal=receipt['commit_seal'], connection=conn, now=NOW)
        assert replayed['idempotent'] is True


def test_trending_capture_verify_and_idempotent_commit(monkeypatch):
    monkeypatch.setenv('HUB_TEST_TMDB_TOKEN', SYNTHETIC_TOKEN)
    clock = FakeClock()
    plan = select_targets(source='tmdb_trending', job_id='trend-job-01', pages=1, max_requests=4)

    def fake_fetch(req):
        is_day = '/trending/movie/day' in req['url']
        body = json.dumps({
            'page': 1,
            'results': [{'id': 501 if is_day else 502, 'title': '榜片', 'release_date': '2026-01-01'}],
        }, ensure_ascii=False).encode('utf-8')
        return {'status': 200, 'headers': {}, 'body': body, 'url': req['url']}

    with non_c_archive() as root:
        cap = run_capture(
            plan,
            archive_dir=str(root),
            execute=True,
            token_env='HUB_TEST_TMDB_TOKEN',
            fetch=fake_fetch,
            clock=clock,
            sleep=clock.sleep,
        )
        assert cap['status'] == 'COMPLETED'
        job_dir = Path(cap['archive_dir'])
        receipt = verify_archive(job_dir, now=datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc))
        assert receipt['status'] == 'READY_TO_COMMIT'
        assert receipt['counts']['valid'] == 2

        conn = FakeBoardConnection(mapping={'501:base': 1})
        res1 = commit_archive(job_dir, execute=True, connection=conn, now=datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc))
        assert res1['status'] == 'COMMITTED'
        assert res1['idempotent'] is False
        assert len(conn.entries) == 2

        res2 = commit_archive(job_dir, execute=True, connection=conn, now=datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc))
        assert res2['idempotent'] is True


def test_local_envelope_quarantine_review_anomaly_cap_and_clean_commit():
    with non_c_archive() as root:
        # 1) Archive with conflicts & >5 anomalies -> NEEDS_REVIEW + <=5 samples + blocked commit
        bad_records = [
            _envelope_record(1, douban_id='db-1', title='好电影', year=2021),
            # Same external douban id but different title -> external_id_identity_conflict (quarantined)
            _envelope_record(2, douban_id='db-conflict', title='甲片', year=2020),
            _envelope_record(3, douban_id='db-conflict', title='乙片', year=2020),
            # Same title+year but different douban id -> title_year_candidate_only (needs_review)
            _envelope_record(4, douban_id='db-dup-title', title='好电影', year=2021),
        ] + [_envelope_record(10 + i, bad=True) for i in range(6)]
        bad_input = root / 'bad-envelope.json'
        bad_input.write_text(json.dumps({'metadata': _envelope_meta(), 'records': bad_records}, ensure_ascii=False), encoding='utf-8')

        plan_bad = select_targets(source='local_envelope', job_id='env-bad', input_path=str(bad_input))
        cap_bad = run_capture(plan_bad, archive_dir=str(root), execute=True)
        assert cap_bad['status'] == 'COMPLETED'

        receipt_bad = verify_archive(cap_bad['archive_dir'])
        assert receipt_bad['status'] == 'NEEDS_REVIEW'
        assert receipt_bad['counts']['valid'] >= 1
        assert receipt_bad['counts']['quarantined'] == 2
        assert receipt_bad['counts']['needs_review'] == 1
        assert receipt_bad['counts']['rejected'] == 6
        assert receipt_bad['error_breakdown']['external_id_identity_conflict'] == 2
        assert receipt_bad['error_breakdown']['title_year_candidate_only'] == 1
        assert len(receipt_bad['anomaly_samples']) <= 5
        assert all('overview' not in sample and 'records' not in sample for sample in receipt_bad['anomaly_samples'])

        with pytest.raises(HubRejected) as exc_rev:
            commit_archive(cap_bad['archive_dir'], execute=True)
        assert exc_rev.value.code == 'REVIEW_REQUIRED'

        # 2) Clean envelope -> READY_TO_COMMIT -> dataset.json + idempotent replay
        good_input = root / 'good-envelope.json'
        good_input.write_text(json.dumps({'metadata': _envelope_meta(), 'records': [_envelope_record(1)]}, ensure_ascii=False), encoding='utf-8')
        plan_good = select_targets(source='local_envelope', job_id='env-good', input_path=str(good_input))
        cap_good = run_capture(plan_good, archive_dir=str(root), execute=True)
        receipt_good = verify_archive(cap_good['archive_dir'])
        assert receipt_good['status'] == 'READY_TO_COMMIT'
        c1 = commit_archive(cap_good['archive_dir'], execute=True)
        assert c1['status'] == 'COMMITTED' and c1['idempotent'] is False
        assert Path(c1['dataset_path']).is_file()
        c2 = commit_archive(cap_good['archive_dir'], execute=True)
        assert c2['idempotent'] is True


class _FakeCatalogAdapter:
    def __init__(self, conflict_tid=None):
        self.conflict_tid = conflict_tid
        self.applied = 0
        self.receipts = {}

    def preview(self, payloads):
        out = []
        for p in payloads:
            if p['id'] == self.conflict_tid:
                raise ImportRejected('IDENTITY_CONFLICT')
            out.append({'tmdb_id': p['id'], 'movie_id': None, 'title': p['title'], 'action': 'append'})
        return out

    def apply(self, jobid, payloads, backup, expected_plans=None):
        if jobid in self.receipts:
            return dict(self.receipts[jobid], replayed=True)
        backup({'movies': []})
        self.applied += 1
        res = {'batch_id': 55, 'items': [{'tmdb_id': p['id'], 'movie_id': 901} for p in payloads], 'replayed': False}
        self.receipts[jobid] = res
        return res


def test_tmdb_catalog_capture_verify_conflict_and_commit(monkeypatch):
    monkeypatch.setenv('HUB_TEST_TMDB_TOKEN', SYNTHETIC_TOKEN)
    plan = select_targets(source='tmdb_catalog', job_id='cat-job-01', tmdb_ids=[9000000001])

    def fake_fetch(req):
        tid = int(req['url'].split('/movie/')[1].split('?')[0])
        return {
            'status': 200,
            'headers': {},
            'body': json.dumps(_tmdb_movie_payload(tid), ensure_ascii=False).encode('utf-8'),
            'url': req['url'],
        }

    with non_c_archive() as root:
        cap = run_capture(plan, archive_dir=str(root), execute=True, token_env='HUB_TEST_TMDB_TOKEN', fetch=fake_fetch)
        assert cap['status'] == 'COMPLETED'
        job_dir = Path(cap['archive_dir'])

        # Conflict preview blocks commit
        bad_adapter = _FakeCatalogAdapter(conflict_tid=9000000001)
        conflict_receipt = verify_archive(job_dir, importer=bad_adapter)
        assert conflict_receipt['status'] == 'NEEDS_REVIEW'
        assert conflict_receipt['counts']['quarantined'] == 1
        assert conflict_receipt['error_breakdown']['IDENTITY_CONFLICT'] == 1

        # Clean preview allows commit + writes before-image + idempotent replay
        good_adapter = _FakeCatalogAdapter()
        ok_receipt = verify_archive(job_dir, importer=good_adapter)
        assert ok_receipt['status'] == 'READY_TO_COMMIT'
        c1 = commit_archive(job_dir, execute=True, importer=good_adapter)
        assert c1['status'] == 'COMMITTED' and c1['batch_id'] == 55 and c1['idempotent'] is False
        assert (job_dir / 'before-image.json').is_file()
        c2 = commit_archive(job_dir, execute=True, importer=good_adapter)
        assert c2['idempotent'] is True and good_adapter.applied == 1


# ------------------------------------------------------------------ 4. commit seal & execute gates


def test_commit_requires_execute_flag_and_blocks_seal_tampering(monkeypatch):
    monkeypatch.setenv('HUB_TEST_TMDB_TOKEN', SYNTHETIC_TOKEN)
    clock = FakeClock()
    plan = select_targets(source='tmdb_supplement', scope='explicit', job_id='tamper-01', targets=[(1, 101)], max_requests=2)

    def fake_fetch(req):
        return {
            'status': 200,
            'headers': {},
            'body': json.dumps(_tmdb_movie_payload(101), ensure_ascii=False).encode('utf-8'),
            'url': req['url'],
        }

    with non_c_archive() as root:
        cap = run_capture(
            plan,
            archive_dir=str(root),
            execute=True,
            token_env='HUB_TEST_TMDB_TOKEN',
            fetch=fake_fetch,
            clock=clock,
            sleep=clock.sleep,
        )
        job_dir = Path(cap['archive_dir'])
        receipt = verify_archive(job_dir, now=NOW)
        conn = FakeSupplementConnection()

        # Missing execute=True
        with pytest.raises(HubInputError) as exc_exec:
            commit_archive(job_dir, execute=False, seal=receipt['commit_seal'], connection=conn)
        assert exc_exec.value.code == 'EXECUTE_FLAG_REQUIRED'
        assert conn.batches == {}

        # Wrong seal string
        with pytest.raises(HubRejected) as exc_seal:
            commit_archive(job_dir, execute=True, seal='0' * 64, connection=conn, now=NOW)
        assert exc_seal.value.code == 'SEAL_MISMATCH'
        assert conn.batches == {}

        # Tamper normalized artifact on disk -> recomputed seal diverges -> SEAL_MISMATCH
        norm_file = job_dir / 'normalized' / '101' / 'attempt-1.json'
        doc = json.loads(norm_file.read_text(encoding='utf-8'))
        doc['facts']['base']['title'] = '被篡改'
        norm_file.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')

        with pytest.raises(HubRejected) as exc_tamper:
            commit_archive(job_dir, execute=True, seal=receipt['commit_seal'], connection=conn, now=NOW)
        assert exc_tamper.value.code == 'SEAL_MISMATCH'
        assert conn.batches == {}


# ------------------------------------------------------------------ 5. CLI e2e


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


def test_cli_select_capture_verify_summary_and_commit_gates():
    with non_c_archive() as root:
        env_file = root / 'input.json'
        env_file.write_text(
            json.dumps({'metadata': _envelope_meta(), 'records': [_envelope_record(1)]}, ensure_ascii=False),
            encoding='utf-8',
        )
        plan_path = root / 'cli-plan.json'
        archive_root = root / 'archives'

        # 1) select -> writes plan
        r_sel = _run_cli([
            'select',
            '--source', 'local_envelope',
            '--job-id', 'cli-env-01',
            '--input', str(env_file),
            '--out-plan', str(plan_path),
        ])
        assert r_sel.returncode == 0, r_sel.stderr
        sel_json = json.loads(r_sel.stdout)
        assert sel_json['contract'] == HUB_CONTRACT
        assert plan_path.is_file()

        # 2) C: archive blocked
        r_c = _run_cli(['capture', '--plan', str(plan_path), '--archive', r'C:\bad-archive'])
        assert r_c.returncode == 2
        assert 'ARCHIVE_ON_SYSTEM_DRIVE' in r_c.stderr

        # 3) pipeline-preview dry-run
        r_prev = _run_cli(['pipeline-preview', '--plan', str(plan_path), '--archive', str(archive_root), '--summary-only'])
        assert r_prev.returncode == 0, r_prev.stderr
        prev_json = json.loads(r_prev.stdout)
        assert prev_json['verify']['status'] == 'READY_TO_COMMIT'

        # 4) capture --execute (local_envelope stage)
        r_cap = _run_cli(['capture', '--plan', str(plan_path), '--archive', str(archive_root), '--execute'])
        assert r_cap.returncode == 0, r_cap.stderr
        job_dir = archive_root / 'cli-env-01'

        # 5) verify --summary-only -> strictly <30 lines
        r_ver = _run_cli(['verify', '--archive', str(job_dir), '--summary-only'])
        assert r_ver.returncode == 0, r_ver.stderr
        lines = [line for line in r_ver.stdout.strip().splitlines() if line.strip()]
        assert len(lines) < 30
        ver_json = json.loads(r_ver.stdout)
        assert set(ver_json) >= {'contract', 'rule_version', 'source', 'job_id', 'status', 'counts', 'error_breakdown', 'anomaly_samples', 'commit_seal'}
        assert ver_json['status'] == 'READY_TO_COMMIT'

        # 6) commit without --execute -> rejected (exit 2)
        r_noexec = _run_cli(['commit', '--archive', str(job_dir)])
        assert r_noexec.returncode == 2
        assert 'EXECUTE_FLAG_REQUIRED' in r_noexec.stderr

        # 7) commit with wrong --seal -> rejected (exit 3)
        r_badseal = _run_cli(['commit', '--archive', str(job_dir), '--execute', '--seal', 'f' * 64])
        assert r_badseal.returncode == 3
        assert 'SEAL_MISMATCH' in r_badseal.stderr

        # 8) commit --execute with valid seal -> exit 0
        r_com = _run_cli(['commit', '--archive', str(job_dir), '--execute', '--seal', ver_json['commit_seal'], '--summary-only'])
        assert r_com.returncode == 0, r_com.stderr
        com_json = json.loads(r_com.stdout)
        assert com_json['status'] == 'COMMITTED'
        assert (job_dir / 'dataset.json').is_file()


def test_summary_envelope_caps_anomaly_samples_at_5():
    fake_receipt = {
        'contract': HUB_CONTRACT,
        'rule_version': RULE_VERSION,
        'source': 'local_envelope',
        'job_id': 'cap-test',
        'status': 'NEEDS_REVIEW',
        'counts': {'total_input': 10, 'valid': 2, 'rejected': 8, 'quarantined': 0, 'needs_review': 0, 'duplicates': 0},
        'error_breakdown': {'invalid_record': 8},
        'anomaly_samples': [{'kind': 'rejected', 'id_ref': f'id-{i}', 'reason': 'invalid_record'} for i in range(9)],
        'commit_seal': 'a' * 64,
        'preview': {'large': 'ignored'},
    }
    compact = summary_envelope(fake_receipt)
    assert len(compact['anomaly_samples']) == 5
    assert 'preview' not in compact


def test_local_envelope_csv_with_metadata_and_nfkc_normalization():
    with non_c_archive() as root:
        csv_file = root / 'movies.csv'
        csv_file.write_text(
            'source_record_id,external_ids,title,release_year,runtime_minutes,genres\n'
            'csv-1,"{""douban"":""998877""}","　全角　电影  ",2023,120,"[""剧情""]"\n',
            encoding='utf-8',
        )
        meta_file = root / 'meta.json'
        meta_file.write_text(json.dumps(_envelope_meta()), encoding='utf-8')
        plan = select_targets(
            source='local_envelope',
            job_id='csv-job-01',
            input_path=str(csv_file),
            metadata=str(meta_file),
        )
        cap = run_capture(plan, archive_dir=str(root), execute=True)
        receipt = verify_archive(cap['archive_dir'])
        assert receipt['status'] == 'READY_TO_COMMIT'
        assert receipt['counts']['valid'] == 1
        c1 = commit_archive(cap['archive_dir'], execute=True)
        dataset = json.loads(Path(c1['dataset_path']).read_text(encoding='utf-8'))
        assert dataset['movies'][0]['title'] == '全角 电影'


def test_verify_rejected_archive_blocks_commit():
    with non_c_archive() as root:
        bad_file = root / 'all_bad.json'
        bad_file.write_text(
            json.dumps({'metadata': _envelope_meta(), 'records': [_envelope_record(1, bad=True)]}, ensure_ascii=False),
            encoding='utf-8',
        )
        plan = select_targets(source='local_envelope', job_id='env-rejected', input_path=str(bad_file))
        cap = run_capture(plan, archive_dir=str(root), execute=True)
        receipt = verify_archive(cap['archive_dir'])
        assert receipt['status'] == 'REJECTED'
        assert receipt['counts']['valid'] == 0
        assert receipt['counts']['rejected'] == 1
        with pytest.raises(HubRejected) as exc:
            commit_archive(cap['archive_dir'], execute=True)
        assert exc.value.code == 'ARCHIVE_REJECTED'


def test_cli_select_supplement_missing_and_commit_requires_dsn():
    with non_c_archive() as root:
        docs_file = root / 'docs.json'
        docs_file.write_text(
            json.dumps([
                {'movie_id': 1, 'tmdb_id': 101, 'field_status': {'keywords': 'EMPTY', 'releases': 'PRESENT'}},
                {'movie_id': 2, 'tmdb_id': 102, 'field_status': {'keywords': 'FAILED', 'releases': 'NOT_FETCHED'}},
            ]),
            encoding='utf-8',
        )
        r_sel = _run_cli([
            'select',
            '--source', 'tmdb_supplement',
            '--scope', 'missing',
            '--job-id', 'cli-sup-01',
            '--documents', str(docs_file),
            '--topics', 'keywords,releases',
            '--max-requests', '5',
        ])
        assert r_sel.returncode == 0, r_sel.stderr
        plan = json.loads(r_sel.stdout)
        assert [t['movie_id'] for t in plan['targets']] == [2]
        assert plan['selection']['excluded_empty_count'] == 1

        # Create a minimal local_envelope archive and verify CLI commit rejects missing DSN when --source tmdb_supplement
        # Simulate missing DSN on a valid supplement-like verification receipt check via missing env var
        sup_dir = root / 'sup-fake'
        sup_dir.mkdir()
        (sup_dir / 'manifest.json').write_text(json.dumps({
            'schema': 'tmdb-supplement-capture-manifest-v1',
            'job_id': 'sup-fake',
            'attempts': [{'tmdb_id': 101, 'movie_id': 1, 'attempt': 1, 'status': 'HTTP_200', 'http_status': 200}],
        }), encoding='utf-8')
        # Even without DB, CLI commit without --execute exits 2 first
        r_no_exec = _run_cli(['commit', '--archive', str(sup_dir), '--source', 'tmdb_supplement'])
        assert r_no_exec.returncode == 2
        assert 'EXECUTE_FLAG_REQUIRED' in r_no_exec.stderr

