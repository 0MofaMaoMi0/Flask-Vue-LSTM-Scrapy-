"""Project-local DB SSH tunnel supervisor with strict host key pinning and reconnect."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parents[2] / '运行配置' / 'Ubuntu'
TUNNEL_JSON = STATE_DIR / 'db-tunnel.json'
STOP_MARKER = STATE_DIR / 'stop-db-tunnel'
ERROR_LOG = STATE_DIR / 'db-tunnel-errors.txt'
SUPERVISOR_STATE = STATE_DIR / 'tunnel-supervisor.json'
LOOPBACK_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})


def resolve_ssh_exe() -> str:
    """Locate the OpenSSH client binary without modifying system config."""
    env_ssh = os.environ.get('MI2_SSH_EXE', '').strip()
    if env_ssh:
        return env_ssh
    default_ssh = Path('C:/Windows/System32/OpenSSH/ssh.exe')
    if default_ssh.is_file():
        return str(default_ssh)
    return shutil.which('ssh') or str(default_ssh)


def _require_ssh_token(doc: dict, key: str) -> str:
    value = doc.get(key)
    if (not isinstance(value, str) or not value.strip()
            or value != value.strip() or value.startswith('-')
            or any(ch.isspace() for ch in value)):
        raise ValueError(f'Invalid {key} in tunnel config')
    return value


def load_tunnel_settings(path: Path = TUNNEL_JSON) -> dict:
    """Load and validate DB SSH tunnel parameters."""
    doc = json.loads(path.read_text(encoding='utf-8'))
    if type(doc) is not dict:
        raise ValueError('Tunnel config must be a JSON object')
    local_port = doc.get('local_port')
    if type(local_port) is not int or isinstance(local_port, bool) or not 1 <= local_port <= 65535:
        raise ValueError('Invalid local_port in tunnel config')
    remote_target = doc.get('remote_target')
    if not isinstance(remote_target, str):
        raise ValueError('Invalid remote_target in tunnel config')
    host, sep, port_text = remote_target.rpartition(':')
    if not sep or host not in LOOPBACK_HOSTS or not (port_text.isascii() and port_text.isdigit()):
        raise ValueError('remote_target must be loopback host:port')
    remote_port = int(port_text)
    if not 1 <= remote_port <= 65535:
        raise ValueError('remote_target port out of range')
    return {
        'local_port': local_port,
        'remote_target': remote_target,
        'ssh_host': _require_ssh_token(doc, 'ssh_host'),
        'ssh_user': _require_ssh_token(doc, 'ssh_user'),
        'host_key_alias': _require_ssh_token(doc, 'host_key_alias'),
    }


def build_ssh_args(settings: dict, ssh_exe: str | None = None,
                   control_dir: Path = STATE_DIR) -> list[str]:
    """Build strict host-pinned OpenSSH command arguments for DB port forwarding."""
    exe = ssh_exe or resolve_ssh_exe()
    return [
        exe,
        '-F', 'none',
        '-i', str(control_dir / 'private' / 'm6_ed25519'),
        '-o', 'BatchMode=yes',
        '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ExitOnForwardFailure=yes',
        '-o', 'UpdateHostKeys=no',
        '-o', 'ConnectTimeout=5',
        '-o', 'ServerAliveInterval=10',
        '-o', 'ServerAliveCountMax=3',
        '-o', 'HostKeyAlias=' + settings['host_key_alias'],
        '-o', 'UserKnownHostsFile=' + str(control_dir / 'private' / 'known_hosts'),
        '-N',
        '-L', f"127.0.0.1:{settings['local_port']}:{settings['remote_target']}",
        settings['ssh_user'] + '@' + settings['ssh_host'],
    ]


def main(argv=None) -> int:
    """Supervise the DB SSH tunnel until stop-db-tunnel marker is created."""
    parser = argparse.ArgumentParser(description='Maintain the Ubuntu DB SSH tunnel.')
    parser.add_argument('--tunnel-json', default=None,
                        help='Override path to db-tunnel.json')
    parsed = parser.parse_args(argv)
    tunnel_path = Path(parsed.tunnel_json) if parsed.tunnel_json else TUNNEL_JSON
    state_dir = tunnel_path.parent
    stop_marker = state_dir / 'stop-db-tunnel' if parsed.tunnel_json else STOP_MARKER
    error_log = state_dir / 'db-tunnel-errors.txt' if parsed.tunnel_json else ERROR_LOG
    supervisor_state = state_dir / 'tunnel-supervisor.json' if parsed.tunnel_json else SUPERVISOR_STATE

    settings = load_tunnel_settings(tunnel_path)
    args = build_ssh_args(settings, control_dir=state_dir)
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

    attempt = 0
    process = None
    try:
        while not stop_marker.exists():
            attempt += 1
            with error_log.open('ab') as errors:
                process = subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=errors,
                    creationflags=creationflags,
                )
                supervisor_state.write_text(json.dumps({
                    'supervisor_pid': os.getpid(),
                    'ssh_pid': process.pid,
                    'attempt': attempt,
                    'local_port': settings['local_port'],
                }), encoding='utf-8')
                while process.poll() is None and not stop_marker.exists():
                    time.sleep(0.25)
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
            if not stop_marker.exists():
                time.sleep(1)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
