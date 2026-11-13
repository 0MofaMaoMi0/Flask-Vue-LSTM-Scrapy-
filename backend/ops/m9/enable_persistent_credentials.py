"""Fixed guest installer: encrypted credentials table, protected master key and test runner."""
import json,os,pwd,re,secrets,subprocess,sys,hashlib
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlsplit,unquote

UUID='01287100-d9e6-11f1-9713-000c29c02265'
SCHEMA='mi2_m7_assistant_20261107'
ROOT=Path('/etc/movieinsight-credentials')
MASTER=ROOT/'master.key'
ENV=Path('/etc/movieinsight-m7/app.env')
PROFILE=Path('/etc/movieinsight-m6/profiles/real')

def mysql(sql):
    p=subprocess.run(['/usr/bin/mysql','--protocol=socket','--batch','--raw','--skip-column-names'],input=sql.encode(),capture_output=True)
    if p.returncode:raise RuntimeError('MYSQL_FIXED_ACTION_FAILED')
    return p.stdout.decode().strip()

def main():
    if os.geteuid()!=0 or sys.argv[1:]:raise RuntimeError('ROOT_FIXED_ACTION_REQUIRED')
    cfg=json.loads((PROFILE/'instance.json').read_text())
    if Path('/sys/class/dmi/id/product_uuid').read_text().strip()!=cfg['vm_uuid']:raise RuntimeError('VM_IDENTITY_MISMATCH')
    if mysql('SELECT @@server_uuid;')!=UUID:raise RuntimeError('MYSQL_IDENTITY_MISMATCH')
    raw=ENV.read_text();env=dict(line.split('=',1) for line in raw.splitlines() if line and '=' in line)
    url=urlsplit(env['MI2_M7_DATABASE_URL']);user=unquote(url.username or '')
    if url.scheme!='mysql+pymysql' or url.hostname!='127.0.0.1' or url.port!=3306 or url.path!='/'+SCHEMA or not re.fullmatch(r'mi2m7[a-z0-9_]{1,24}',user):raise RuntimeError('M7_SCOPE_MISMATCH')
    if env.get('MI2_CREDENTIALS_MASTER_FILE',str(MASTER))!=str(MASTER):raise RuntimeError('MASTER_PATH_CONFLICT')
    if ROOT.is_symlink() or MASTER.is_symlink() or ENV.is_symlink():raise RuntimeError('UNSAFE_TARGET')
    account=pwd.getpwnam(cfg['app_user'])
    exists=mysql("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='"+SCHEMA+"' AND table_name='provider_credentials';")=='1'
    if exists:
        columns=mysql("SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema='"+SCHEMA+"' AND table_name='provider_credentials' ORDER BY ORDINAL_POSITION;").splitlines()
        if columns!=['user_id','provider','key_version','nonce','ciphertext','generation','verification','created_at','updated_at']:raise RuntimeError('EXISTING_TABLE_CONFLICT')
        if mysql('SELECT COUNT(*) FROM `'+SCHEMA+'`.provider_credentials;')!='0' and not MASTER.is_file():raise RuntimeError('EXISTING_CREDENTIALS_MASTER_MISSING')
    hosts=mysql("SELECT Host FROM mysql.user WHERE User='"+user+"';").splitlines()
    if not hosts or any(h not in ('localhost','127.0.0.1') for h in hosts):raise RuntimeError('RUNTIME_ACCOUNT_HOST_CONFLICT')
    ROOT.mkdir(mode=0o750,exist_ok=True);os.chown(ROOT,0,account.pw_gid);os.chmod(ROOT,0o750)
    if MASTER.exists():
        if len(MASTER.read_bytes())!=32:raise RuntimeError('INVALID_EXISTING_MASTER')
    else:
        fd=os.open(MASTER,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o400)
        try:os.write(fd,secrets.token_bytes(32));os.fsync(fd)
        finally:os.close(fd)
    os.chown(MASTER,account.pw_uid,account.pw_gid);os.chmod(MASTER,0o400)
    backups=ROOT/'configuration-backups';backups.mkdir(mode=0o700,exist_ok=True)
    backup=backups/('m7-app.env.'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));backup.write_text(raw);os.chmod(backup,0o600)
    mysql('''CREATE TABLE IF NOT EXISTS `'''+SCHEMA+'''`.provider_credentials (
      user_id BIGINT NOT NULL, provider VARCHAR(24) NOT NULL,
      key_version INT NOT NULL, nonce BINARY(12) NOT NULL,
      ciphertext BLOB NOT NULL, generation VARCHAR(32) NOT NULL,
      verification VARCHAR(32) NOT NULL, created_at DATETIME NOT NULL,
      updated_at DATETIME NOT NULL, PRIMARY KEY(user_id,provider)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;''')
    for host in hosts:mysql("GRANT SELECT,INSERT,UPDATE,DELETE ON `"+SCHEMA+"`.provider_credentials TO '"+user+"'@'"+host+"';")
    if 'MI2_CREDENTIALS_MASTER_FILE' not in env:
        tmp=ENV.with_suffix('.credentials.tmp');tmp.write_text(raw.rstrip()+'\nMI2_CREDENTIALS_MASTER_FILE='+str(MASTER)+'\n');os.chmod(tmp,0o600);os.replace(tmp,ENV)
    runner='''#!/usr/bin/python3
"""Fixed low-privilege synthetic credential lifecycle check; no caller commands."""
import os,json,pwd,subprocess,sys
from pathlib import Path
if os.geteuid()!=0 or sys.argv[1:]!=['check']:raise SystemExit('Fixed check action only')
root=Path('/etc/movieinsight-m6/profiles/real');cfg=json.loads((root/'instance.json').read_text())
if Path('/sys/class/dmi/id/product_uuid').read_text().strip()!=cfg['vm_uuid']:raise SystemExit('VM identity mismatch')
def envfile(p):return dict(line.split('=',1) for line in p.read_text().splitlines() if line)
env=dict(PATH='/usr/bin:/bin',LANG='C.UTF-8',PYTHONDONTWRITEBYTECODE='1',**envfile(root/'app.env'),**envfile(Path('/etc/movieinsight-m7/app.env')))
account=pwd.getpwnam(cfg['app_user'])
def drop():os.setgroups([]);os.setgid(account.pw_gid);os.setuid(account.pw_uid)
p=subprocess.run(['/opt/movieinsight-m6/venv/bin/python','-B','-m','m6_checks.persistent_credentials_mysql'],cwd='/opt/movieinsight-m6/current',env=env,preexec_fn=drop,capture_output=True)
try:
 result=json.loads(p.stdout);assert set(result)<= {'all_pass','checks','error_type'}
except Exception:result={'all_pass':False,'error_type':'PERSISTENT_CHECK_RUNNER_FAILED'}
print(json.dumps(result));raise SystemExit(0 if result['all_pass'] else 1)
'''
    target=Path('/usr/local/sbin/mi2-credentials-check')
    if target.exists() and 'Fixed low-privilege synthetic credential lifecycle' not in target.read_text():raise RuntimeError('RUNNER_PATH_CONFLICT')
    target.write_text(runner);os.chmod(target,0o755);os.chown(target,0,0)
    pwd.getpwnam('zhang')
    rule='zhang ALL=(root) NOPASSWD: /usr/local/sbin/mi2-credentials-check check\n'
    sudo=Path('/etc/sudoers.d/96-mi2-credentials-check')
    if sudo.exists() and sudo.read_text()!=rule:raise RuntimeError('SUDO_RULE_CONFLICT')
    tmp=sudo.with_suffix('.tmp');tmp.write_text(rule);os.chmod(tmp,0o440)
    if subprocess.run(['/usr/sbin/visudo','-cf',str(tmp)],capture_output=True).returncode:raise RuntimeError('SUDO_PARSE_FAILED')
    os.replace(tmp,sudo)
    print(json.dumps(dict(credential_table_ready=True,master_key_protected=True,runtime_access='credential table CRUD only',frozen_tables_changed=False,secrets_printed=False)))
    print('PERSISTENT_CREDENTIALS_READY_NO_SECRETS_PRINTED')

if __name__=='__main__':
    try:main()
    except Exception as e:print(json.dumps({'ready':False,'error_type':type(e).__name__,'error_code':str(e) if isinstance(e,RuntimeError) else 'INSTALL_STOPPED_NO_RETRY'}));raise SystemExit(1)
