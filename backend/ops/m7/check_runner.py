"""Fixed guest M7 verification runner; no caller-supplied command or paths."""
import os,json,pwd,subprocess,sys
from pathlib import Path
if os.geteuid()!=0 or sys.argv[1:]!=['check']:raise SystemExit('Fixed check action only')
root=Path('/etc/movieinsight-m6/profiles/real');cfg=json.loads((root/'instance.json').read_text())
if Path('/sys/class/dmi/id/product_uuid').read_text().strip()!=cfg['vm_uuid']:raise SystemExit('VM identity mismatch')
def envfile(p):return dict(line.split('=',1) for line in p.read_text().splitlines() if line)
env=dict(PATH='/usr/bin:/bin',LANG='C.UTF-8',PYTHONDONTWRITEBYTECODE='1',**envfile(root/'app.env'),**envfile(Path('/etc/movieinsight-m7/app.env')))
account=pwd.getpwnam(cfg['app_user'])
def drop():os.setgroups([]);os.setgid(account.pw_gid);os.setuid(account.pw_uid)
p=subprocess.run(['/opt/movieinsight-m6/venv/bin/python','-B','-m','m6_checks.m7_mysql_checks'],cwd='/opt/movieinsight-m6/current',env=env,preexec_fn=drop,capture_output=True)
# Only a fixed JSON evidence object may leave the runner; never raw exceptions.
try:
 result=json.loads(p.stdout);assert set(result)<= {'all_pass','checks','error_type'}
except Exception:result={'all_pass':False,'error_type':'M7_CHECK_RUNNER_FAILED'}
print(json.dumps(result))
raise SystemExit(0 if result['all_pass'] else 1)
