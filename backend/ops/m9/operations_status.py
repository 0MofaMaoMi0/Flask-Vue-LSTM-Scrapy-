"""Fixed guest diagnostics and one cron-daemon rehearsal; no database writes."""
import json,os,re,sys,subprocess
from datetime import datetime,timezone,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from ops.m9.recovery import OPS,ROOT,PROFILE,SCHEMAS,SERVER_UUID,connect,identity,private,envfile,query,validate_contracts,tree_manifest

def ancestors():
    result=[];pid=os.getppid()
    for _ in range(12):
        try:
            name=Path('/proc',str(pid),'comm').read_text().strip();result.append(name)
            status=Path('/proc',str(pid),'status').read_text();pid=int(re.search(r'^PPid:\s+(\d+)',status,re.M)[1])
            if pid<1:break
        except (OSError,ValueError,TypeError):break
    return result

def append_event(job,state,**extra):
    OPS.mkdir(mode=0o700,exist_ok=True)
    chain=ancestors()
    event=dict(at=datetime.now(timezone.utc).isoformat(),job=job,state=state,origin='CRON' if 'cron' in chain else 'MANUAL',pid=os.getpid(),parents=chain,probe_id=os.environ.get('MI2_M9_PROBE_ID'),**extra)
    path=OPS/'scheduled-events.jsonl'
    fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:f.write(json.dumps(event)+'\n');f.flush();os.fsync(f.fileno())

def probe_allowed(job):
    token=os.environ.get('MI2_M9_PROBE_ID')
    if token is None:return True
    if not re.fullmatch('[a-f0-9]{32}',token) or job not in ('refresh','candidates'):return False
    plan=json.loads(private(OPS/'cron-probe.json').read_text())
    if plan['id']!=token or datetime.now(timezone.utc)>datetime.fromisoformat(plan['expires_at']):return False
    events=read_events()
    return not any(e.get('probe_id')==token and e.get('job')==job and e.get('state') in ('STARTED','SUCCEEDED','FAILED') for e in events)

def read_events():
    path=OPS/'scheduled-events.jsonl'
    if not path.exists():return []
    private(path)
    with path.open('rb') as f:
        size=path.stat().st_size
        if size>1_000_000:f.seek(size-1_000_000);f.readline()
        lines=f.read().decode().splitlines()
    result=[]
    for line in lines:
        try:result.append(json.loads(line))
        except ValueError:continue
    return result[-100:]

def finish_probe(job):
    if job!='candidates' or not os.environ.get('MI2_M9_PROBE_ID'):return
    path=Path('/etc/cron.d/mi2-m9-once')
    if path.exists() and not path.is_symlink() and path.stat().st_uid==0:path.unlink()

def inspect():
    with connect() as c:
        cfg=identity(c);contract=validate_contracts(c)
    state=Path(cfg['state']);env=envfile(PROFILE/'app.env');appdata=state/'appdata'
    media=Path(env.get('MI2_MEDIA_CACHE',str(appdata)))
    schedules=[]
    cron=Path('/etc/cron.d/mi2-m9')
    if cron.exists():
        for line in cron.read_text().splitlines():
            fields=line.split()
            if len(fields)>=7 and 'run_job.py' in line:
                job=next((x for x in ('refresh','candidates','retention') if 'run_job.py '+x in line),None)
                schedules.append(dict(fields=fields[:5],user=fields[5],job=job))
    channels={};folder=appdata/'trending'
    for name in ('popular','trending_day','trending_week','pending_tmdb'):
        p=folder/(name+'.json')
        if p.is_file() and not p.is_symlink():
            try:
                d=json.loads(p.read_text());channels[name]=dict(modified_at=datetime.fromtimestamp(p.stat().st_mtime,timezone.utc).isoformat(),captured_at=d.get('captured_at') or d.get('generated_at'),count=len(d.get('items',d.get('pending',{}))))
            except (OSError,ValueError,TypeError):channels[name]=dict(status='UNREADABLE')
    recovery_error=OPS/'last-recovery-error.json'
    error=json.loads(private(recovery_error).read_text()) if recovery_error.exists() else None
    recovery_result=OPS/'last-recovery-result.json'
    last_result=json.loads(private(recovery_result).read_text()) if recovery_result.exists() else None
    return dict(server_uuid=SERVER_UUID,schemas={s:len(t) for s,t in contract.items()},timezone=subprocess.check_output(['timedatectl','show','--property=Timezone','--value']).decode().strip(),cron_active=subprocess.run(['systemctl','is-active','--quiet','cron']).returncode==0,schedules=schedules,events=read_events(),channels=channels,media_cache_within_appdata=media.is_relative_to(appdata),application_files=len(tree_manifest(appdata)),last_recovery_result=last_result,historical_recovery_error=error,secrets_printed=False)

def schedule_probe():
    import secrets
    with connect() as c:identity(c)
    OPS.mkdir(mode=0o700,exist_ok=True)
    path=Path('/etc/cron.d/mi2-m9-once')
    if path.exists():raise RuntimeError('CRON_PROBE_EXISTS_PRESERVED')
    # UTC fields are converted to the installed guest timezone by astimezone().
    now=datetime.now(timezone.utc);times=[(now+timedelta(minutes=2)).replace(second=0,microsecond=0),(now+timedelta(minutes=7)).replace(second=0,microsecond=0)]
    token=secrets.token_hex(16);text='SHELL=/bin/sh\nPATH=/usr/sbin:/usr/bin:/sbin:/bin\n'
    for when,job in zip(times,('refresh','candidates')):
        local=when.astimezone();text+=f'{local.minute} {local.hour} {local.day} {local.month} * root MI2_M9_PROBE_ID={token} /opt/movieinsight-m6/venv/bin/python -B /opt/movieinsight-m6/current/ops/m9/run_job.py {job} >> /var/log/mi2-trending.log 2>&1\n'
    plan=dict(id=token,scheduled_at=[t.isoformat() for t in times],expires_at=(now+timedelta(hours=1)).isoformat())
    target=OPS/'cron-probe.json';target.write_text(json.dumps(plan));target.chmod(0o600)
    with path.open('x') as f:f.write(text)
    path.chmod(0o644)
    return dict(cron_probe_scheduled=True,**plan,production_schedule_unchanged=True,secrets_printed=False)

if __name__=='__main__':
    try:
        if os.geteuid()!=0 or sys.argv[1:] not in (['inspect'],['cron-probe']):raise RuntimeError('FIXED_ROOT_ACTION_REQUIRED')
        print(json.dumps(inspect() if sys.argv[1]=='inspect' else schedule_probe()))
    except Exception as e:
        print(json.dumps(dict(passed=False,error_type=type(e).__name__,error_code=str(e) if type(e) is RuntimeError and re.fullmatch('[A-Z_]+',str(e)) else 'OPERATIONS_CHECK_STOPPED',secrets_printed=False)));sys.exit(1)
