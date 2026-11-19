"""Project-local SSH forwarding with strict host pinning and reconnect."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import time


def resolve_ssh_exe() -> str:
    env_ssh = os.environ.get('MI2_SSH_EXE', '').strip()
    if env_ssh:
        return env_ssh
    default_ssh = Path('C:/Windows/System32/OpenSSH/ssh.exe')
    if default_ssh.is_file():
        return str(default_ssh)
    return shutil.which('ssh') or str(default_ssh)


SSH_EXE = resolve_ssh_exe()
state=Path(__file__).resolve().parents[2]/'运行配置'/'Ubuntu'
control=state
settings=json.loads((state/'http-tunnel.json').read_text())
args=[SSH_EXE,'-F','none','-i',str(control/'private/m6_ed25519'),
      '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ExitOnForwardFailure=yes',
      '-o','UpdateHostKeys=no','-o','ConnectTimeout=5','-o','ServerAliveInterval=10','-o','ServerAliveCountMax=3',
      '-o','HostKeyAlias='+settings['host_key_alias'],'-o','UserKnownHostsFile='+str(control/'private/known_hosts'),
      '-N','-L',f"127.0.0.1:{settings['local_port']}:127.0.0.1:5000",settings['ssh_user']+'@'+settings['ssh_host']]
attempt=0
try:
    while not (state/'stop-http-tunnel').exists():
        attempt+=1
        with (state/'http-tunnel-errors.txt').open('ab') as errors:
            process=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=errors,creationflags=subprocess.CREATE_NO_WINDOW)
            (state/'tunnel-supervisor.json').write_text(json.dumps({'supervisor_pid':os.getpid(),'ssh_pid':process.pid,'attempt':attempt,'local_port':settings['local_port']}))
            while process.poll() is None and not (state/'stop-http-tunnel').exists():time.sleep(.25)
            if process.poll() is None:process.terminate();process.wait(timeout=5)
        time.sleep(1)
finally:
    if 'process' in globals() and process.poll() is None:process.terminate()
