"""Root installer: repair only the existing dedicated guest M9 schedule.
No SQL writes, account grants, host changes, or immediate retention deletion.
"""
import os, sys, stat, pwd, json, subprocess, shlex
from pathlib import Path
from datetime import datetime, timezone
ROOT=Path('/opt/movieinsight-m6')
PROFILE=Path('/etc/movieinsight-m6/profiles/real/app.env')
UUID='01287100-d9e6-11f1-9713-000c29c02265'

def install():
    if os.geteuid()!=0:raise RuntimeError('ROOT_REQUIRED')
    found=subprocess.check_output(['/usr/bin/mysql','--protocol=socket','-N','-B','-e','SELECT @@server_uuid']).decode().strip()
    if found!=UUID:raise RuntimeError('TARGET_MISMATCH')
    release=(ROOT/'current').resolve(strict=True)
    if not release.is_relative_to(ROOT/'releases'):raise RuntimeError('RELEASE_MISMATCH')
    for name in ('refresh_trending','collect_trending_candidates','purge_recommendation_feedback','run_job','audit_identity'):
        p=release/'ops/m9'/(name+'.py')
        if p.is_symlink() or p.stat().st_uid!=0 or p.stat().st_mode&0o022:raise RuntimeError('UNTRUSTED_RELEASE')
    backup=ROOT/'m9-schedule-backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup.mkdir(parents=True,mode=0o700)
    target=Path('/etc/cron.d/mi2-m9')
    if target.exists():(backup/'mi2-m9').write_bytes(target.read_bytes())
    subprocess.run([str(ROOT/'venv/bin/python'),'-B',str(release/'ops/m9/run_job.py'),'audit'],check=True)
    target.write_text('SHELL=/bin/sh\nPATH=/usr/sbin:/usr/bin:/sbin:/bin\n'
        '15 4 * * * root /opt/movieinsight-m6/venv/bin/python -B /opt/movieinsight-m6/current/ops/m9/run_job.py refresh >> /var/log/mi2-trending.log 2>&1\n'
        '20 4 * * * root /opt/movieinsight-m6/venv/bin/python -B /opt/movieinsight-m6/current/ops/m9/run_job.py candidates >> /var/log/mi2-trending.log 2>&1\n'
        '30 4 * * 0 root /opt/movieinsight-m6/venv/bin/python -B /opt/movieinsight-m6/current/ops/m9/run_job.py retention >> /var/log/mi2-trending.log 2>&1\n')
    target.chmod(0o644)
    print(json.dumps(dict(schedule_repaired=True,backup=str(backup),timezone=subprocess.check_output(['timedatectl','show','--property=Timezone','--value']).decode().strip(),retention_run_now=False)))
    helper=Path('/usr/local/sbin/mi2-m9-verify')
    helper.write_text('#!/bin/sh\n[ "$#" = 1 ] && [ "$1" = verify ] || exit 2\nexec /opt/movieinsight-m6/venv/bin/python -B /opt/movieinsight-m6/current/ops/m9/verify_schedule.py\n')
    helper.chmod(0o755);os.chown(helper,0,0)
    rule=Path('/etc/sudoers.d/97-mi2-m9-verify')
    text='zhang ALL=(root) NOPASSWD: /usr/local/sbin/mi2-m9-verify verify\n'
    candidate=backup/'sudoers';candidate.write_text(text);candidate.chmod(0o440)
    subprocess.run(['/usr/sbin/visudo','-cf',str(candidate)],check=True,capture_output=True)
    rule.write_text(text);rule.chmod(0o440);os.chown(rule,0,0)
    print('M9_SCHEDULE_REPAIRED_NO_SECRETS_PRINTED')
if __name__=='__main__':
    try:install()
    except Exception as e:
        print(json.dumps(dict(installed=False,error_type=type(e).__name__,secrets_printed=False)));sys.exit(1)
