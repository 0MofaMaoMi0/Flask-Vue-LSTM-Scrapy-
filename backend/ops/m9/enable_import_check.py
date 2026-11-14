"""One-time creation of an empty dedicated MySQL test schema and fixed checker.
Does not modify application grants, catalog models, real catalog rows or host OS.
"""
import os,json,secrets,pwd,subprocess,sys
from pathlib import Path
from sqlalchemy import create_engine,text
from sqlalchemy.engine import URL
ROOT=Path('/opt/movieinsight-m6');SCOPE=Path('/etc/movieinsight-importcheck')
SCHEMA='mi2_m6_importcheck_20261113';USER='mi2importcheck'
def main():
 if os.geteuid()!=0:raise RuntimeError('ROOT_REQUIRED')
 sys.path.insert(0,str(ROOT/'current'))
 cfg=json.loads(Path('/etc/movieinsight-m6/profiles/real/instance.json').read_text())
 if Path('/sys/class/dmi/id/product_uuid').read_text().strip()!=cfg['vm_uuid']:raise RuntimeError('VM_MISMATCH')
 engine=create_engine('mysql+pymysql://root@localhost/mysql',connect_args={'unix_socket':'/var/run/mysqld/mysqld.sock'},hide_parameters=True)
 with engine.connect() as c:
  if c.scalar(text('SELECT @@server_uuid'))!=cfg['server_uuid']:raise RuntimeError('SERVER_MISMATCH')
  known=(SCOPE/'owner.json').is_file()
  users=c.execute(text('SELECT Host FROM mysql.user WHERE User=:u'),{'u':USER}).scalars().all()
  if users and (not known or set(users)!={'127.0.0.1'}):raise RuntimeError('UNKNOWN_EXISTING_ACCOUNT')
  exists=c.scalar(text('SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=:s'),{'s':SCHEMA})
  if exists and not (SCOPE/'owner.json').is_file():raise RuntimeError('UNKNOWN_EXISTING_SCHEMA')
 account=pwd.getpwnam(cfg['app_user']);SCOPE.mkdir(mode=0o750,exist_ok=True);os.chown(SCOPE,0,account.pw_gid)
 marker=SCOPE/'owner.json'
 expected=dict(schema=SCHEMA,server_uuid=cfg['server_uuid'],purpose='unified-import-checks-v1')
 if marker.exists() and json.loads(marker.read_text())!=expected:raise RuntimeError('OWNER_MISMATCH')
 marker.write_text(json.dumps(expected));marker.chmod(0o600)
 config=SCOPE/'check.json'
 if config.exists():test=json.loads(config.read_text())
 else:
  password=secrets.token_urlsafe(32)
  test=dict(M6_SCHEMA=SCHEMA,M6_APP_USER=USER,M6_SERVER_UUID=cfg['server_uuid'],url=URL.create('mysql+pymysql',username=USER,password=password,host='127.0.0.1',port=3306,database=SCHEMA).render_as_string(hide_password=False),state='/var/lib/movieinsight-m6-realcopy/appdata/import-checks')
  config.write_text(json.dumps(test));os.chown(config,account.pw_uid,account.pw_gid);config.chmod(0o400)
 from sqlalchemy.engine import make_url
 password=make_url(test['url']).password
 with engine.begin() as c:
  c.execute(text('CREATE DATABASE IF NOT EXISTS `'+SCHEMA+'` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci'))
  found=c.scalar(text("SELECT COUNT(*) FROM mysql.user WHERE User=:u"),{'u':USER})
  if not found:c.execute(text("CREATE USER '"+USER+"'@'127.0.0.1' IDENTIFIED BY :pwd"),{'pwd':password})
  c.execute(text('GRANT SELECT,INSERT,UPDATE,DELETE ON `'+SCHEMA+"`.* TO '"+USER+"'@'127.0.0.1'"))
 test_engine=create_engine('mysql+pymysql://root@localhost/'+SCHEMA,connect_args={'unix_socket':'/var/run/mysqld/mysqld.sock'},hide_parameters=True)
 from catalog.models import CatalogBase
 CatalogBase.metadata.create_all(test_engine)
 with test_engine.begin() as c:c.execute(text('CREATE TABLE IF NOT EXISTS `person_assets` LIKE `'+cfg['schema']+'`.`person_assets`'))
 state=Path(test['state']);state.mkdir(mode=0o700,parents=True,exist_ok=True);os.chown(state,account.pw_uid,account.pw_gid)
 helper=Path('/usr/local/sbin/mi2-import-check')
 helper.write_text('''#!/usr/bin/python3
import os,sys,json,pwd,subprocess
from pathlib import Path
if os.geteuid()!=0 or sys.argv[1:]!=['check']:raise SystemExit('Fixed check only')
cfg=json.loads(Path('/etc/movieinsight-m6/profiles/real/instance.json').read_text())
if Path('/sys/class/dmi/id/product_uuid').read_text().strip()!=cfg['vm_uuid']:raise SystemExit('VM mismatch')
a=pwd.getpwnam(cfg['app_user'])
def drop():os.setgroups([]);os.setgid(a.pw_gid);os.setuid(a.pw_uid)
p=subprocess.run(['/opt/movieinsight-m6/venv/bin/python','-B','-m','m6_checks.admin_imports_mysql'],cwd='/opt/movieinsight-m6/current',env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1','MI2_IMPORT_CHECK_CONFIG':'/etc/movieinsight-importcheck/check.json'},preexec_fn=drop,capture_output=True)
try:
 result=json.loads(p.stdout);assert set(result)<={'all_pass','checks','error_type','model_dispatches'}
except Exception:result={'all_pass':False,'error_type':'CHECK_RUNNER_FAILED'}
print(json.dumps(result));sys.exit(0 if result['all_pass'] else 1)
''');helper.chmod(0o755);os.chown(helper,0,0)
 candidate=SCOPE/'sudoers.tmp';candidate.write_text('zhang ALL=(root) NOPASSWD: /usr/local/sbin/mi2-import-check check\n');candidate.chmod(0o440)
 subprocess.run(['/usr/sbin/visudo','-cf',str(candidate)],capture_output=True,check=True)
 target=Path('/etc/sudoers.d/98-mi2-import-check');target.write_bytes(candidate.read_bytes());target.chmod(0o440);os.chown(target,0,0);candidate.unlink()
 print('UNIFIED_IMPORT_CHECK_READY_NO_SECRETS_PRINTED')
if __name__=='__main__':
 try:main()
 except Exception as e:print(json.dumps(dict(installed=False,error_type=type(e).__name__,secrets_printed=False)));sys.exit(1)
