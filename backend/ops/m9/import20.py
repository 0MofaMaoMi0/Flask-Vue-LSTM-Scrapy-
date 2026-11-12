SOURCE_HASH='7d338d3e586655ea060b7145487fb81d95322fffdd9f20f7c45f5842d08d733d'
CODE_HASH='8af39bae1a1f1a4a9c2927645d45e7558e0e8c570fe577ea4b94527e118a6514'
import os,json,pwd,hashlib,subprocess,shutil
from pathlib import Path
if os.geteuid()!=0:raise SystemExit('Run with guest sudo only')
config=Path('/etc/movieinsight-m6/profiles/real');cfg=json.loads((config/'instance.json').read_text())
if Path('/sys/class/dmi/id/product_uuid').read_text().strip()!=cfg['vm_uuid']:raise SystemExit('VM_MISMATCH')
server=subprocess.check_output(['/usr/bin/mysql','--protocol=socket','-NBe','SELECT @@server_uuid;']).decode().strip()
if server!='01287100-d9e6-11f1-9713-000c29c02265' or cfg['schema']!='mi2_m6_realcopy_20261107':raise SystemExit('TARGET_MISMATCH')
release=Path(cfg['root'])/'current'
if hashlib.sha256((release/'catalog/m9_append.py').read_bytes()).hexdigest()!=CODE_HASH:raise SystemExit('CODE_HASH_MISMATCH')
src=Path('/home/zhang/m9-movies-20.sqlite3')
if src.is_symlink() or hashlib.sha256(src.read_bytes()).hexdigest()!=SOURCE_HASH:raise SystemExit('SOURCE_HASH_MISMATCH')
account=pwd.getpwnam(cfg['app_user']);work=Path(cfg['state'])/'appdata/m9-capture-20-20261111'
if work.is_symlink():raise SystemExit('LINK_REJECTED')
work.mkdir(mode=0o750,exist_ok=True);os.chown(work,account.pw_uid,account.pw_gid)
target=work/'movies-20.sqlite3'
if target.exists():
 if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest()!=SOURCE_HASH:raise SystemExit('PRESERVE_EXISTING_SOURCE')
else:shutil.copyfile(src,target)
os.chown(target,account.pw_uid,account.pw_gid);target.chmod(0o400)
def envfile(p):return dict(line.split('=',1) for line in p.read_text().splitlines() if line)
env=dict(PATH='/usr/bin:/bin',LANG='C.UTF-8',PYTHONDONTWRITEBYTECODE='1',**envfile(config/'app.env'),**envfile(config/'audit.env'))
def drop():os.setgroups([]);os.setgid(account.pw_gid);os.setuid(account.pw_uid)
def invoke(dry):
 spec=work/'append-spec.json';spec.write_text(json.dumps(dict(path=str(target),sha=SOURCE_HASH,state_dir=str(work),expected_count=20,dry_run=dry)));spec.chmod(0o400);os.chown(spec,account.pw_uid,account.pw_gid)
 child=subprocess.run([str(Path(cfg['root'])/'venv/bin/python'),'-B','-m','catalog.m9_append'],cwd=release,env=dict(env,MI2_M9_APPEND_SPEC=str(spec)),preexec_fn=drop,capture_output=True)
 try:result=json.loads(child.stdout)
 except Exception:raise SystemExit('IMPORT_CHILD_FAILED_NO_SECRETS_PRINTED')
 print(json.dumps(result,ensure_ascii=False))
 if child.returncode or result.get('import_passed') is False:raise SystemExit('M9_IMPORT_STOPPED')
 return result
preflight=invoke(True)
if not preflight.get('replayed'):
 backup=subprocess.run(['/usr/local/sbin/mi2-m6-operations','backup'],capture_output=True)
 if backup.returncode:raise SystemExit('BACKUP_FAILED_NO_IMPORT')
 print(backup.stdout.decode())
result=invoke(False)
replay=invoke(False)
if replay.get('inserted')!=0 or replay.get('audit_passed') is not True:raise SystemExit('IDEMPOTENCE_FAILED')
print('M9_20_APPEND_SUCCESS_NO_SECRETS_PRINTED')
