"""Fixed, guest-only M6 operations. Installed root-owned; never prints secrets."""
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import subprocess
import sys
import tarfile

REQUESTED = sys.argv[1] if len(sys.argv) == 2 else ''
CONFIG_ROOT = Path('/etc/movieinsight-m6')
active = CONFIG_ROOT/'active-profile'
profile = 'real' if REQUESTED.startswith('real-') else active.read_text().strip() if active.exists() else 'synthetic'
if profile not in ('synthetic','real'):raise SystemExit('Invalid fixed profile')
CONFIG = CONFIG_ROOT/('profiles/real/instance.json' if profile=='real' else 'instance.json')
cfg = json.loads(CONFIG.read_text())
root = Path(cfg['root'])
current = root / 'current'
venv = root / 'venv'
state = Path(cfg['state'])


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if result.returncode:
        # Dependency/service diagnostics contain no injected credentials.
        raise RuntimeError('Command failed: ' + Path(args[0]).name)
    return result.stdout.strip()


def sql(query):
    return run(['/usr/bin/mysql', '--protocol=socket', '-N'], input=query)


def identity():
    if Path('/sys/class/dmi/id/product_uuid').read_text().strip() != cfg['vm_uuid']:
        raise RuntimeError('VM identity mismatch')
    if sql('SELECT @@server_uuid;') != cfg['server_uuid']:
        raise RuntimeError('MySQL identity mismatch')


def envfile(name):
    return dict(line.split('=', 1) for line in (CONFIG.parent / name).read_text().splitlines() if line)


def child(args, user, env):
    account = pwd.getpwnam(user)
    def drop():
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    return run(args, env=dict(PATH='/usr/bin:/bin', LANG='C.UTF-8', HOME=account.pw_dir,
                             **env, PYTHONDONTWRITEBYTECODE='1'),
               cwd=current, preexec_fn=drop)


def deploy():
    archive = Path(cfg['archive'])
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    release = root / 'releases' / digest
    if not release.exists():
        release.mkdir(parents=True, mode=0o755)
        with tarfile.open(archive) as tar:
            members = tar.getmembers()
            if len(members) > 300 or sum(m.size for m in members) > 5_000_000:
                raise RuntimeError('Release exceeds bounds')
            for member in members:
                path = Path(member.name)
                if (not member.isfile() or path.is_absolute() or '..' in path.parts
                        or path.suffix not in {'.py', '.json', '.ini', '.txt'}
                        or any(p.startswith('.') for p in path.parts)):
                    raise RuntimeError('Unexpected release member')
                target = release / path
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with target.open('xb') as out:
                    shutil.copyfileobj(tar.extractfile(member), out)
                target.chmod(0o644)
    # No process writes application code. Preserve all earlier releases.
    temporary = root / 'next-release'
    if temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(release, target_is_directory=True)
    temporary.replace(current)
    print(json.dumps({'release_sha256': digest}))


def initialize():
    state.mkdir(mode=0o751,parents=True,exist_ok=True)
    marker = state / 'application-provisioned.json'
    if marker.exists():
        raise RuntimeError('Already provisioned; audit before retry')
    import re
    for name in [cfg['schema'], cfg['activity_schema']]:
        if not re.fullmatch(r'mi2_m6_[a-z0-9_]{8,48}', name):
            raise RuntimeError('Invalid dedicated schema')
        if sql("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='" + name + "';") != '0':
            raise RuntimeError('Schema exists; refusing overwrite')
    for user in [cfg['app_user'], cfg['migration_user']]:
        if not re.fullmatch(r'mi2[a-z0-9_]{1,29}', user):
            raise RuntimeError('Invalid dedicated account')
        if sql("SELECT COUNT(*) FROM mysql.user WHERE User='" + user + "';") != '0':
            raise RuntimeError('Account exists; refusing password reset')
        try:
            pwd.getpwnam(user)
        except KeyError:
            run(['/usr/sbin/useradd', '--system', '--no-create-home', '--shell', '/usr/sbin/nologin', user])
    for schema in [cfg['schema'], cfg['activity_schema']]:
        sql('CREATE DATABASE `' + schema + '` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;')
    common = {'MI2_M6_ISOLATED': 'true', 'MI2_M6_SCHEMA': cfg['schema'],
              'MI2_M6_ACTIVITY_SCHEMA': cfg['activity_schema'],
              'MI2_M6_APP_USER': cfg['app_user'], 'MI2_M6_MIGRATION_USER': cfg['migration_user'],
              'MI2_M6_SERVER_UUID': cfg['server_uuid'], 'MI2_COOKIE_SECURE': 'false',
              'MI2_ALLOWED_ORIGINS': ','.join(cfg['origins'])}
    for user, filename, key, rights in [
        (cfg['app_user'], 'app.env', 'MI2_DATABASE_URL', 'SELECT,INSERT,UPDATE,DELETE'),
        (cfg['migration_user'], 'migration.env', 'MI2_MIGRATION_DATABASE_URL', 'SELECT,INSERT,UPDATE,DELETE,CREATE,ALTER,INDEX,REFERENCES')]:
        password = secrets.token_urlsafe(36)
        sql("CREATE USER '" + user + "'@'127.0.0.1' IDENTIFIED WITH caching_sha2_password BY '" + password + "';")
        for schema in [cfg['schema'], cfg['activity_schema']]:
            sql('GRANT ' + rights + ' ON `' + schema + "`.* TO '" + user + "'@'127.0.0.1';")
        env = dict(common)
        env[key] = 'mysql+pymysql://' + user + ':' + password + '@127.0.0.1:3306/' + cfg['schema']
        if filename == 'migration.env':
            env['MI2_M6_DDL_LEDGER'] = str(state / 'migration' / '0002-catalog.jsonl')
        path = CONFIG.parent / filename
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            handle.write(''.join(k + '=' + v + '\n' for k, v in env.items()))
    for directory, user in [(state/'migration', cfg['migration_user']), (state/'appdata', cfg['app_user'])]:
        directory.mkdir(mode=0o750, exist_ok=True)
        account = pwd.getpwnam(user)
        os.chown(directory, account.pw_uid, account.pw_gid)
    # Dedicated state parent is traversable, not writable/readable, by service users.
    state.chmod(0o751)
    marker.write_text(json.dumps({'schema': cfg['schema'], 'app_user': cfg['app_user']}))
    marker.chmod(0o600)
    print(json.dumps({'initialized': True, 'schema': cfg['schema'], 'secrets_printed': False}))


def ssh_harden():
    path = Path('/etc/ssh/sshd_config.d/00-m6-hardening.conf')
    backup = state / 'sshd-before-p1.conf'
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
        backup.chmod(0o600)
    # Password stays enabled until Navicat is switched to its key-based login.
    path.write_text('PasswordAuthentication yes\nKbdInteractiveAuthentication no\nPermitRootLogin no\nAllowUsers zhang\nX11Forwarding no\nAllowAgentForwarding no\nAllowTcpForwarding local\nPermitTunnel no\nPermitOpen 127.0.0.1:3306 localhost:3306 127.0.0.1:5000\n')
    try:
        run(['/usr/sbin/sshd', '-t'])
        run(['/usr/bin/systemctl', 'reload', 'ssh'])
    except Exception:
        path.write_bytes(backup.read_bytes())
        run(['/usr/sbin/sshd', '-t'])
        run(['/usr/bin/systemctl', 'reload', 'ssh'])
        raise
    print(json.dumps({'permitopen_restricted': True, 'password_retained_for_navicat': True}))


def setup_audit():
    user=cfg.get('audit_user','mi2audit')
    import re
    if not re.fullmatch(r'mi2[a-z0-9_]{1,29}',user):raise RuntimeError('Invalid audit identity')
    path=CONFIG.parent/'audit.env'
    if path.exists():
        raise RuntimeError('Audit credentials already provisioned; no reset')
    if sql("SELECT COUNT(*) FROM mysql.user WHERE User='"+user+"';")!='0':
        raise RuntimeError('Audit account exists; no reuse')
    password=secrets.token_urlsafe(36)
    sql("CREATE USER '"+user+"'@'127.0.0.1' IDENTIFIED WITH caching_sha2_password BY '"+password+"';")
    for schema in [cfg['schema'],cfg['activity_schema']]:
        sql('GRANT SELECT ON `'+schema+"`.* TO '"+user+"'@'127.0.0.1';")
    with path.open('x') as handle:
        path.chmod(0o600)
        handle.write('MI2_M6_AUDIT_USER='+user+'\nMI2_AUDIT_DATABASE_URL=mysql+pymysql://'+user+':'+password+'@127.0.0.1:3306/'+cfg['schema']+'\n')
    cfg['audit_user']=user
    CONFIG.write_text(json.dumps(cfg,indent=2))
    print(json.dumps({'audit_account':user,'permissions':'schema SELECT only','secrets_printed':False}))


def prepare_real_profile():
    if profile!='synthetic':raise RuntimeError('Prepare from synthetic profile only')
    target=CONFIG_ROOT/'profiles/real'
    target.mkdir(parents=True,mode=0o700,exist_ok=True)
    if (target/'instance.json').exists():raise RuntimeError('Real profile already prepared; audit before retry')
    real=dict(cfg,schema='mi2_m6_realcopy_20261107',activity_schema='mi2_m6_realactivity_20261107',
        app_user='mi2realapp',migration_user='mi2realmigrate',audit_user='mi2realaudit',
        state='/var/lib/movieinsight-m6-realcopy',
        approved_source_stage='/home/zhang/approved-m5-static.sqlite3',
        approved_source_sha256='e9909e362c2223a480fbae11e589039fe430d02cd8360aed15d8889a3a66eeee')
    (target/'instance.json').write_text(json.dumps(real,indent=2));(target/'instance.json').chmod(0o600)
    print(json.dumps({'real_profile_prepared':True,'schema':real['schema']}))


def stage_real_source():
    if profile!='real':raise RuntimeError('Real profile required')
    original=Path(cfg['approved_source_stage'])
    if original.is_symlink() or original.stat().st_nlink!=1:raise RuntimeError('Linked source rejected')
    payload=original.read_bytes()
    if hashlib.sha256(payload).hexdigest()!=cfg['approved_source_sha256']:raise RuntimeError('Unapproved real source hash')
    destination=state/'sources';destination.mkdir(mode=0o750,exist_ok=True)
    group=pwd.getpwnam(cfg['app_user']).pw_gid
    os.chown(destination,0,group)
    target=destination/'approved-m5-static.sqlite3'
    descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o640)
    with os.fdopen(descriptor,'wb') as handle:
        handle.write(payload);handle.flush();os.fsync(handle.fileno())
    os.chown(target,0,group)
    print(json.dumps({'approved_static_source_staged':True,'sha256':cfg['approved_source_sha256'],'bytes':len(payload)}))


def create_backup():
    # M9 recovery v2 delegated; fixed root-owned release, no caller paths.
    print(run([str(venv/"bin/python"), "-B", str(current/"ops/m9/recovery.py"), "backup"]))


def test_restore():
    # M9 recovery v2 delegated; fixed root-owned release, no caller paths.
    print(run([str(venv/"bin/python"), "-B", str(current/"ops/m9/recovery.py"), "restore-test"]))


identity()
action = REQUESTED.removeprefix('real-')
if action == 'deploy':
    deploy()
elif action == 'initialize':
    initialize()
elif action == 'ssh-harden':
    ssh_harden()
elif action == 'migrate':
    print(child([str(venv/'bin/python'), '-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'head'], cfg['migration_user'], envfile('migration.env')))
    print(json.dumps({'migration_complete': True}))
elif action == 'dependencies':
    if not venv.exists():
        run(['/usr/bin/python3', '-m', 'venv', str(venv)])
    run([str(venv/'bin/python'), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(current/'requirements.txt')])
    print(json.dumps({'dependencies_installed': True}))
elif action == 'check':
    env=envfile('app.env')
    if (CONFIG.parent/'audit.env').exists():env.update(envfile('audit.env'))
    print(child([str(venv/'bin/python'), '-B', 'm6_checks/live_suite.py'], cfg['app_user'], env))
elif action == 'setup-audit':
    setup_audit()
elif action == 'prepare-real-profile':
    prepare_real_profile()
elif action == 'stage-source':
    stage_real_source()
elif action == 'import-static':
    if profile!='real':raise RuntimeError('Real profile required')
    env=dict(envfile('app.env'),**envfile('audit.env'))
    env.update(MI2_APPROVED_SOURCE_PATH=str(state/'sources/approved-m5-static.sqlite3'),
               MI2_APPROVED_SOURCE_SHA256=cfg['approved_source_sha256'],MI2_M6_STATE_DIR=str(state/'appdata'))
    print(child([str(venv/'bin/python'),'-B','-m','catalog.m6_import_cli'],cfg['app_user'],env))
elif action == 'activate':
    if profile!='real':raise RuntimeError('Real profile required')
    # Keep the old synthetic profile and all its schemas/credentials intact.
    active.write_text('real\n');active.chmod(0o600)
    print(json.dumps({'active_profile':'real','synthetic_profile_preserved':True}))
elif action == 'start-app':
    unit = Path('/etc/systemd/system/movieinsight-m6.service')
    unit.write_text('[Unit]\nDescription=MovieInsight M6 isolated test application\nAfter=mysql.service\nWants=mysql.service\n[Service]\nType=simple\nUser='+cfg['app_user']+'\nWorkingDirectory='+str(current)+'\nEnvironmentFile='+str(CONFIG.parent/'app.env')+'\nEnvironment=PYTHONDONTWRITEBYTECODE=1\nExecStart='+str(venv/'bin/python')+' -B -m app.m6_serve\nRestart=on-failure\nNoNewPrivileges=true\nPrivateTmp=true\nProtectHome=true\nProtectSystem=strict\nReadWritePaths='+str(state/'appdata')+'\n[Install]\nWantedBy=multi-user.target\n')
    run(['/usr/bin/systemctl', 'daemon-reload'])
    run(['/usr/bin/systemctl', 'enable', '--now', 'movieinsight-m6'])
    run(['/usr/bin/systemctl', 'restart', 'movieinsight-m6'])
    print(json.dumps({'service_started': True}))
elif action == 'restart-app':
    run(['/usr/bin/systemctl', 'restart', 'movieinsight-m6'])
    print(json.dumps({'service_restarted': True}))
elif action == 'restart-mysql':
    run(['/usr/bin/systemctl','restart','mysql'])
    print(json.dumps({'mysql_restarted':True}))
elif action == 'backup':
    create_backup()
elif action == 'restore-test':
    test_restore()
elif action == 'status':
    print(json.dumps({'profile':profile,'schema':cfg['schema'],'audit_ready':(CONFIG.parent/'audit.env').exists(),'server_uuid': sql('SELECT @@server_uuid;'), 'bind': sql('SELECT @@bind_address;'),
                      'schema_tables': sql("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='"+cfg['schema']+"';")}))
else:
    raise SystemExit('Unsupported fixed action')
