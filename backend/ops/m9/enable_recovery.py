"""Guest-only installer: preserve fixed actions; replace only backup/restore bodies."""
import ast,hashlib,json,os,re,shutil,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path('/opt/movieinsight-m6')
OLD_OPERATIONS='61fe439c3c949b818f16ba8f95dbc728dfd17f26e72487e69d82aec3f3de492d'

def delegated_source(source):
    tree=ast.parse(source);lines=source.splitlines(keepends=True);changes=[]
    for name,action in [('create_backup','backup'),('test_restore','restore-test')]:
        matches=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name]
        if len(matches)!=1:raise RuntimeError('OPERATIONS_LAYOUT_MISMATCH')
        node=matches[0]
        replacement=f'def {name}():\n    # M9 recovery v2 delegated; fixed root-owned release, no caller paths.\n    print(run([str(venv/"bin/python"), "-B", str(current/"ops/m9/recovery.py"), "{action}"]))\n'
        changes.append((node.lineno-1,node.end_lineno,replacement))
    for start,end,replacement in sorted(changes,reverse=True):lines[start:end]=[replacement]
    value=''.join(lines);ast.parse(value)
    return value

def main():
    if os.geteuid()!=0 or sys.argv[1:]:raise RuntimeError('FIXED_ROOT_INSTALL_REQUIRED')
    release=(ROOT/'current').resolve(strict=True)
    if not release.is_relative_to(ROOT/'releases'):raise RuntimeError('RELEASE_SCOPE_MISMATCH')
    for part in ['recovery.py','operations_status.py','enable_recovery.py','run_job.py']:
        p=release/'ops/m9'/part;s=p.lstat()
        if p.is_symlink() or s.st_uid!=0 or s.st_mode&0o022:raise RuntimeError('UNTRUSTED_RELEASE')
    from ops.m9.recovery import connect,identity,validate_contracts,OPS
    with connect() as c:identity(c);contract=validate_contracts(c)
    old=ROOT/'operations.py';raw=old.read_bytes()
    if old.is_symlink() or old.stat().st_uid!=0 or old.stat().st_mode&0o022:raise RuntimeError('UNTRUSTED_OPERATIONS')
    text=raw.decode();new=delegated_source(text)
    if hashlib.sha256(raw).hexdigest()!=OLD_OPERATIONS and text!=new:raise RuntimeError('OPERATIONS_CHANGED_REVIEW_REQUIRED')
    helper=Path('/usr/local/sbin/mi2-m9-ops');sudo=Path('/etc/sudoers.d/100-mi2-m9-ops')
    rule='zhang ALL=(root) NOPASSWD: /usr/local/sbin/mi2-m9-ops inspect, /usr/local/sbin/mi2-m9-ops cron-probe\n'
    runner='#!/bin/sh\n[ "$#" = 1 ] || exit 2\ncase "$1" in inspect|cron-probe) ;; *) exit 2 ;; esac\nexec /opt/movieinsight-m6/venv/bin/python -B /opt/movieinsight-m6/current/ops/m9/operations_status.py "$1"\n'
    for p,expected in [(helper,runner),(sudo,rule)]:
        if p.is_symlink() or (p.exists() and p.read_text()!=expected):raise RuntimeError('FIXED_ENTRY_CONFLICT')
    backup=ROOT/'recovery-tool-backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');backup.mkdir(parents=True,mode=0o700)
    saved=backup/'operations.py';saved.write_bytes(raw);saved.chmod(0o600)
    candidate=backup/'sudoers';candidate.write_text(rule);candidate.chmod(0o440)
    subprocess.run(['/usr/sbin/visudo','-cf',str(candidate)],check=True,capture_output=True)
    tmp=old.with_suffix('.recovery-new');tmp.write_text(new);tmp.chmod(0o644);os.chown(tmp,0,0);os.replace(tmp,old)
    OPS.mkdir(mode=0o700,exist_ok=True);OPS.chmod(0o700);os.chown(OPS,0,0)
    helper.write_text(runner);helper.chmod(0o755);os.chown(helper,0,0)
    sudo.write_text(rule);sudo.chmod(0o440);os.chown(sudo,0,0)
    print(json.dumps(dict(installed=True,schemas={s:len(t) for s,t in contract.items()},database_writes=False,existing_credentials_and_permissions_unchanged=True,old_operations_preserved=True,secrets_printed=False)))
    print('M9_RECOVERY_OPERATIONS_READY_NO_SECRETS_PRINTED')

if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    try:main()
    except Exception as e:
        print(json.dumps(dict(installed=False,error_type=type(e).__name__,error_code=str(e) if type(e) is RuntimeError and re.fullmatch('[A-Z_]+',str(e)) else 'INSTALL_STOPPED',secrets_printed=False)));sys.exit(1)
