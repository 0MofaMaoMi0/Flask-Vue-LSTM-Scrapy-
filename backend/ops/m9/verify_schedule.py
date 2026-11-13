"""Run only public metadata refresh and read-only candidate collection, never retention."""
import os,sys,subprocess,json
from pathlib import Path
if os.geteuid()!=0:raise SystemExit('ROOT_REQUIRED')
runner=Path(__file__).with_name('run_job.py')
for job in ('refresh','candidates','audit'):
    r=subprocess.run([sys.executable,'-B',str(runner),job],capture_output=True,timeout=360)
    print(r.stdout.decode(),end='')
    if r.returncode:
        print(json.dumps(dict(schedule_smoke_passed=False,failed_job=job,secrets_printed=False)));sys.exit(1)
print('M9_SCHEDULE_SMOKE_PASSED_NO_SECRETS_PRINTED')
