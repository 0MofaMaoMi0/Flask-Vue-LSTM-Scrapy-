"""One-time fixed guest installer for private profiles, notes and answer reactions.

No authentication/catalog DDL, no credential changes, no bulk user data writes.
"""
import json,os,pwd,re,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit,unquote

PROFILE=Path('/etc/movieinsight-m6/profiles/real')
SCHEMA='mi2_m7_assistant_20261107'
UUID='01287100-d9e6-11f1-9713-000c29c02265'
SCOPE=Path('/etc/movieinsight-personal')
TABLES={
 'user_profiles':('user_id,avatar_png,version,updated_at',
  'user_id BIGINT NOT NULL,avatar_png MEDIUMBLOB NULL,version INT NOT NULL,updated_at DATETIME NOT NULL,PRIMARY KEY(user_id)'),
 'movie_notes':('user_id,movie_id,note,version,updated_at',
  'user_id BIGINT NOT NULL,movie_id BIGINT NOT NULL,note VARCHAR(200) NOT NULL,version INT NOT NULL,updated_at DATETIME NOT NULL,PRIMARY KEY(user_id,movie_id)'),
 'assistant_feedback':('user_id,request_key,request_id,reaction,updated_at',
  'user_id BIGINT NOT NULL,request_key VARCHAR(64) NOT NULL,request_id VARCHAR(100) NOT NULL,reaction VARCHAR(8) NULL,updated_at DATETIME NOT NULL,PRIMARY KEY(user_id,request_key)'),
}
RUNNER='''#!/usr/bin/python3
"""Fixed low-privilege personal features check; owned synthetic users only."""
import os,json,pwd,subprocess,sys
from pathlib import Path
if os.geteuid()!=0 or sys.argv[1:]!=['check']:raise SystemExit('Fixed check only')
root=Path('/etc/movieinsight-m6/profiles/real');cfg=json.loads((root/'instance.json').read_text())
if Path('/sys/class/dmi/id/product_uuid').read_text().strip()!=cfg['vm_uuid']:raise SystemExit('VM identity mismatch')
def envfile(p):return dict(line.split('=',1) for line in p.read_text().splitlines() if line and '=' in line and not line.startswith('#'))
env=dict(PATH='/usr/bin:/bin',LANG='C.UTF-8',PYTHONDONTWRITEBYTECODE='1',**envfile(root/'app.env'),**envfile(Path('/etc/movieinsight-m7/app.env')))
account=pwd.getpwnam(cfg['app_user'])
def drop():os.setgroups([]);os.setgid(account.pw_gid);os.setuid(account.pw_uid)
try:
 p=subprocess.run(['/opt/movieinsight-m6/venv/bin/python','-B','-m','m6_checks.personal_features_mysql'],cwd='/opt/movieinsight-m6/current',env=env,preexec_fn=drop,capture_output=True,timeout=180)
 result=json.loads(p.stdout)
 assert set(result)<={'all_pass','checks','error_type','model_dispatches'} and isinstance(result['checks'],list)
 assert result.get('model_dispatches')==0
 if p.returncode:result['all_pass']=False
except Exception:result={'all_pass':False,'error_type':'PERSONAL_CHECK_RUNNER_FAILED'}
print(json.dumps(result));raise SystemExit(0 if result['all_pass'] else 1)
'''

def mysql(sql):
 p=subprocess.run(['/usr/bin/mysql','--protocol=socket','--batch','--raw','--skip-column-names'],input=sql.encode(),capture_output=True)
 if p.returncode:raise RuntimeError('MYSQL_FIXED_ACTION_FAILED')
 return p.stdout.decode().strip()

def main():
 if os.geteuid()!=0 or sys.argv[1:]:raise RuntimeError('ROOT_FIXED_ACTION_REQUIRED')
 cfg=json.loads((PROFILE/'instance.json').read_text())
 if Path('/sys/class/dmi/id/product_uuid').read_text().strip()!=cfg['vm_uuid']:raise RuntimeError('VM_IDENTITY_MISMATCH')
 if cfg['server_uuid']!=UUID or mysql('SELECT @@server_uuid;')!=UUID:raise RuntimeError('MYSQL_IDENTITY_MISMATCH')
 env=dict(line.split('=',1) for line in Path('/etc/movieinsight-m7/app.env').read_text().splitlines() if line and '=' in line)
 url=urlsplit(env['MI2_M7_DATABASE_URL']);user=unquote(url.username or '')
 if url.scheme!='mysql+pymysql' or url.hostname!='127.0.0.1' or url.port!=3306 or url.path!='/'+SCHEMA or url.query or not re.fullmatch(r'mi2m7[a-z0-9_]{1,24}',user):raise RuntimeError('AUXILIARY_SCOPE_MISMATCH')
 hosts=mysql("SELECT Host FROM mysql.user WHERE User='"+user+"';").splitlines()
 if not hosts or any(h not in ('127.0.0.1','localhost') for h in hosts):raise RuntimeError('RUNTIME_ACCOUNT_HOST_CONFLICT')
 helper=Path('/usr/local/sbin/mi2-personal-check');sudo=Path('/etc/sudoers.d/99-mi2-personal-check')
 rule='zhang ALL=(root) NOPASSWD: /usr/local/sbin/mi2-personal-check check\n'
 for p in (SCOPE,helper,sudo):
  if p.is_symlink():raise RuntimeError('UNSAFE_TARGET')
 if helper.exists() and 'Fixed low-privilege personal features check' not in helper.read_text():raise RuntimeError('RUNNER_PATH_CONFLICT')
 if sudo.exists() and sudo.read_text()!=rule:raise RuntimeError('SUDO_RULE_CONFLICT')
 pwd.getpwnam(cfg['app_user']);pwd.getpwnam('zhang')
 for name,(cols,ddl) in TABLES.items():
  found=mysql("SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema='"+SCHEMA+"' AND table_name='"+name+"' ORDER BY ORDINAL_POSITION;")
  if found and found.splitlines()!=cols.split(','):raise RuntimeError('EXISTING_PERSONAL_TABLE_CONFLICT')
 SCOPE.mkdir(mode=0o700,exist_ok=True);os.chown(SCOPE,0,0);SCOPE.chmod(0o700)
 backup=SCOPE/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');backup.mkdir(mode=0o700)
 for p in (helper,sudo):
  if p.exists():
   saved=backup/p.name;saved.write_bytes(p.read_bytes());saved.chmod(0o600)
 grants='\n'.join(mysql("SHOW GRANTS FOR '"+user+"'@'"+host+"';") for host in hosts)
 saved=backup/'previous-grants.txt';saved.write_text(grants);saved.chmod(0o600)
 for name,(cols,ddl) in TABLES.items():
  mysql('CREATE TABLE IF NOT EXISTS `'+SCHEMA+'`.`'+name+'` ('+ddl+') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;')
  for host in hosts:mysql("GRANT SELECT,INSERT,UPDATE,DELETE ON `"+SCHEMA+"`.`"+name+"` TO '"+user+"'@'"+host+"';")
 helper.write_text(RUNNER);os.chown(helper,0,0);helper.chmod(0o755)
 candidate=backup/'sudoers.tmp';candidate.write_text(rule);candidate.chmod(0o440)
 if subprocess.run(['/usr/sbin/visudo','-cf',str(candidate)],capture_output=True).returncode:raise RuntimeError('SUDO_PARSE_FAILED')
 sudo.write_bytes(candidate.read_bytes());os.chown(sudo,0,0);sudo.chmod(0o440)
 print(json.dumps(dict(auxiliary_tables=list(TABLES),application_access='three tables CRUD',frozen_tables_changed=False,secrets_printed=False)))
 print('PERSONAL_FEATURES_READY_NO_SECRETS_PRINTED')

if __name__=='__main__':
 try:main()
 except Exception as e:
  print(json.dumps(dict(installed=False,error_type=type(e).__name__,error_code=str(e) if isinstance(e,RuntimeError) else 'INSTALL_STOPPED_NO_RETRY',secrets_printed=False)));raise SystemExit(1)
