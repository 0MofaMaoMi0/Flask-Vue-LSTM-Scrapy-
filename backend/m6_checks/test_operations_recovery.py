"""Offline safety checks; these never create a real database or change ACLs."""
import json
from datetime import date,datetime
from decimal import Decimal
import pytest
from ops.m9.recovery import contracts,validate_grant,canonical,normalize_ddl,rewrite_fk_dump,tree_manifest,snapshot_files
from ops.m9.enable_recovery import delegated_source

def test_contract_uses_current_metadata_including_all_auxiliary_tables():
    result=contracts();main,activity,aux=result.values()
    assert len(main)==21 and len(activity)==5 and len(aux)==9
    assert {'provider_credentials','user_profiles','movie_notes','assistant_feedback'}<=aux.keys()
    assert 'nonce' in aux['provider_credentials'] and 'avatar_png' in aux['user_profiles']

def test_grants_allow_current_crud_tables_but_never_extra_privileges_or_scopes():
    schema='mi2_m7_assistant_20261107';tables=set(contracts()[schema])
    for scope in ['`'+schema+'`.*','`'+schema+'`.`provider_credentials`']:
        validate_grant("GRANT SELECT, INSERT, UPDATE, DELETE ON "+scope+" TO 'mi2m7app'@'127.0.0.1'",schema,tables)
    validate_grant("GRANT USAGE ON *.* TO 'mi2m7app'@'127.0.0.1'",schema,tables)
    for scope,rights,tail in [('*.*','SELECT',''),('`other`.*','SELECT',''),('`'+schema+'`.`extra`','SELECT',''),('`'+schema+'`.*','CREATE',''),('`'+schema+'`.*','SELECT',' WITH GRANT OPTION')]:
        with pytest.raises(RuntimeError):validate_grant('GRANT '+rights+' ON '+scope+" TO 'mi2m7app'@'127.0.0.1'"+tail,schema,tables)

def test_binary_and_decimal_digest_serialization_is_lossless_and_deterministic():
    row=[b'\x00\xff\x1a',Decimal('1.2500'),datetime(2026,11,14),date(2026,11,14),None,'中文']
    encoded=json.dumps([canonical(v) for v in row],ensure_ascii=False)
    assert 'AP8a' in encoded and '1.2500' in encoded and '中文' in encoded and '"type": "date"' in encoded

def test_only_foreign_key_ddl_is_remapped_never_insert_values_or_comments():
    mapping={'mi2_m6_realcopy_20261107':'mi2_m6_restore_20261114_0'}
    fk='  CONSTRAINT `fk` FOREIGN KEY (`user_id`) REFERENCES `mi2_m6_realcopy_20261107`.`auth_users` (`id`)\n'
    data="INSERT INTO `example` VALUES ('REFERENCES `mi2_m6_realcopy_20261107`.');\n"
    comment='-- REFERENCES `mi2_m6_realcopy_20261107`.\n'
    result=rewrite_fk_dump(fk+data+comment,mapping)
    assert 'REFERENCES `mi2_m6_restore_20261114_0`.`auth_users`' in result
    assert data in result and comment in result
    assert normalize_ddl(fk,tuple(mapping))==normalize_ddl(result.splitlines(keepends=True)[0],tuple(mapping.values()))
    for sql in ('USE `protected`;','CREATE DATABASE `protected`;','DROP DATABASE `protected`;'):
        with pytest.raises(RuntimeError):rewrite_fk_dump(sql,mapping)

def test_application_files_are_copied_and_hash_checked_without_overwrite(tmp_path):
    src=tmp_path/'source';src.mkdir();(src/'nested').mkdir();(src/'nested'/'state.json').write_text('{"job":"synthetic"}')
    out=tmp_path/'copy';result=snapshot_files(src,out)
    assert tree_manifest(out)==result and len(result)==1
    with pytest.raises(FileExistsError):snapshot_files(src,out)

def test_charset_normalization_preserves_collation_and_nondefault_charsets_and_literals():
    before="CREATE TABLE `t` (\n  `name` varchar(64) COLLATE utf8mb4_bin NOT NULL,\n  `label` text DEFAULT 'CHARACTER SET utf8mb4 COLLATE arbitrary'\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"
    explicit=before.replace('`name` varchar(64) COLLATE','`name` varchar(64) CHARACTER SET utf8mb4 COLLATE')
    assert normalize_ddl(before,())==normalize_ddl(explicit,())
    assert normalize_ddl(before,())!=normalize_ddl(explicit.replace('CHARACTER SET utf8mb4 COLLATE utf8mb4_bin','CHARACTER SET latin1 COLLATE latin1_bin'),())
    assert "DEFAULT 'CHARACTER SET utf8mb4 COLLATE arbitrary'" in normalize_ddl(explicit,())
    assert normalize_ddl(before,())!=normalize_ddl(before.replace('COLLATE utf8mb4_bin NOT NULL','COLLATE utf8mb4_general_ci NOT NULL'),())

def test_installer_changes_only_the_two_existing_fixed_function_bodies():
    original="import sys\nCONSTANT='preserved'\ndef create_backup():\n    old_backup()\ndef test_restore():\n    old_restore()\ndef unrelated():\n    return 42\nidentity()\n"
    value=delegated_source(original)
    assert "CONSTANT='preserved'" in value and 'def unrelated():\n    return 42\nidentity()' in value
    assert value==delegated_source(value) and 'old_backup()' not in value

def test_cron_probe_expires_and_rejects_replayed_or_unrecognized_jobs(tmp_path,monkeypatch):
    from datetime import timezone,timedelta
    from ops.m9 import operations_status as status
    token='a'*32;monkeypatch.setattr(status,'OPS',tmp_path);monkeypatch.setattr(status,'private',lambda p:p)
    monkeypatch.setattr(status,'read_events',lambda:[])
    monkeypatch.setenv('MI2_M9_PROBE_ID',token)
    plan=dict(id=token,expires_at=(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat())
    path=tmp_path/'cron-probe.json';path.write_text(json.dumps(plan))
    assert status.probe_allowed('refresh') and not status.probe_allowed('retention')
    monkeypatch.setattr(status,'read_events',lambda:[dict(probe_id=token,job='refresh',state='STARTED')])
    assert not status.probe_allowed('refresh') and status.probe_allowed('candidates')
    path.write_text(json.dumps(dict(plan,expires_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())))
    assert not status.probe_allowed('candidates')
    monkeypatch.delenv('MI2_M9_PROBE_ID');assert status.probe_allowed('refresh')

def test_retention_dry_run_and_recent_click_preservation(tmp_path):
    from sqlalchemy import create_engine,insert,select
    from catalog.recommendation_feedback import tables
    from ops.m9.purge_recommendation_feedback import purge
    from datetime import timedelta
    engine=create_engine('sqlite://');imp,clicks=tables(None);imp.metadata.create_all(engine);now=datetime(2026,11,14);old=now-timedelta(days=181)
    with engine.begin() as c:
        for i in range(1,4):c.execute(insert(imp).values(impression_id=i,request_id=str(i),user_id=1,channel='test',board_version='test',context='visible-v2',position=1,shown_at=old))
        c.execute(insert(clicks).values(click_id=1,request_id='1',position=1,user_id=1,channel='test',clicked_at=old))
        c.execute(insert(clicks).values(click_id=2,request_id='2',position=1,user_id=1,channel='test',clicked_at=now))
    result=purge(engine,None,now=now,dry_run=True)
    assert result['clicks_deleted']==0 and result['impressions_eligible']==2
    result=purge(engine,None,now=now)
    assert result['clicks_deleted']==1 and result['impressions_deleted']==2
    with engine.connect() as c:assert list(c.execute(select(imp.c.request_id)).scalars())==['2']
    for invalid in (True,0,731):
        with pytest.raises(ValueError):purge(engine,None,invalid)
