"""Fixed guest jobs; environments remain private, failures never print secrets."""
import os,sys,pwd,subprocess,shlex,json
from pathlib import Path
ROOT=Path('/opt/movieinsight-m6')
JOBS={'refresh':'refresh_trending','candidates':'collect_trending_candidates','retention':'purge_recommendation_feedback','audit':'audit_identity'}
def main():
    if os.geteuid()!=0 or len(sys.argv)!=2 or sys.argv[1] not in JOBS:raise ValueError('Fixed job required')
    job=sys.argv[1];release=(ROOT/'current').resolve(strict=True)
    if not release.is_relative_to(ROOT/'releases'):raise ValueError('Release required')
    env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','TZ')}
    env.update(PYTHONPATH=str(release),PYTHONDONTWRITEBYTECODE='1')
    user=subprocess.check_output(['systemctl','show','movieinsight-m6.service','--property=User','--value']).decode().strip()
    if not user or user=='root':raise ValueError('Dedicated application user required')
    account=pwd.getpwnam(user)
    if job=='refresh':
        env['MI2_TMDB_KEY_FILE']='/etc/movieinsight-m6/tmdb_key'
    else:
        for profile in (Path('/etc/movieinsight-m6/profiles/real/app.env'),Path('/etc/movieinsight-m7/app.env')):
            for line in profile.read_text().splitlines():
                if not line.strip() or line.lstrip().startswith('#'):continue
                key,sep,value=line.partition('=')
                if sep:
                    parsed=shlex.split(value);env[key]=parsed[0] if parsed else ''
    folder=Path('/var/lib/movieinsight-m6-realcopy/appdata/trending')
    folder.mkdir(parents=True,exist_ok=True)
    if folder.is_symlink():raise ValueError('Unsafe cache')
    os.chown(folder,account.pw_uid,account.pw_gid);folder.chmod(0o755)
    env['MI2_TRENDING_CACHE']=str(folder)
    def demote():
        os.initgroups(user,account.pw_gid);os.setgid(account.pw_gid);os.setuid(account.pw_uid)
    result=subprocess.run([str(ROOT/'venv/bin/python'),'-B',str(release/'ops/m9'/(JOBS[job]+'.py'))],env=env,cwd=release,preexec_fn=None if job=='refresh' else demote,capture_output=True,timeout=300)
    # Only emit validated JSON; tracebacks can contain URLs/configuration.
    for line in result.stdout.decode().splitlines():
        try:print(json.dumps(json.loads(line),ensure_ascii=False))
        except ValueError:pass
    if result.returncode:raise RuntimeError('Job failed')
if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps(dict(job_ok=False,error_type=type(e).__name__,secrets_printed=False)));sys.exit(1)
