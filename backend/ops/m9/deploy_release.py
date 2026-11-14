from pathlib import Path
import base64
import json
import subprocess
import sys
import tarfile

backend = Path(__file__).resolve().parents[2]
control = Path(sys.argv[1]).resolve(strict=True)
archive = control/'m6-implementation/deploy.tar'
with tarfile.open(archive, 'w') as tar:
    for directory in ['app', 'catalog', 'migrations', 'm6_checks', 'assistant', 'analytics', 'm8_checks', 'ops']:
        for path in (backend/directory).rglob('*'):
            if path.is_file() and path.suffix in ['.py', '.json'] and '__pycache__' not in path.parts:
                tar.add(path, arcname=path.relative_to(backend).as_posix())
    for name in ['requirements.txt', 'alembic.ini', 'admin_api.py', 'media_api.py', 'admin_dashboard.py']:
        tar.add(backend/name, arcname=name)
args = ['C:/Windows/System32/OpenSSH/ssh.exe', '-F', 'none', '-i', str(control/'private/m6_ed25519'),
        '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'HostKeyAlias=192.168.147.131',
        '-o', 'UserKnownHostsFile='+str(control/'private/known_hosts'), 'zhang@192.168.147.50']
source = "import pathlib,base64\np=pathlib.Path.home()/'mi2-m6-deploy.tar'\np.write_bytes(base64.b64decode("+repr(base64.b64encode(archive.read_bytes()).decode())+"));p.chmod(0o600)"
subprocess.run(args+['python3 -'], input=source.encode(), check=True)
result = subprocess.run(args+['sudo -n /usr/local/sbin/mi2-m6-operations deploy'], capture_output=True, check=True)
print(result.stdout.decode())
