"""Own loopback process lifecycle + real browser test, no auth/MySQL imports."""
import json
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request
import uuid


def main(output, dataset):
    out = pathlib.Path(output).resolve()
    if not out.is_dir() or out.drive.upper() == 'C:':
        raise ValueError('Non-C output required')
    backend = pathlib.Path(__file__).resolve().parents[1]
    web = backend.parent / 'movie-web'
    db = out / 'demo-behavior.sqlite3'
    base = 'http://127.0.0.1:8876/api/catalog/'
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 8876))  # Refuse to reuse another process's port.
    checks = []
    def check(name, ok):
        checks.append({'case': name, 'pass': bool(ok)})
        if not ok:
            raise RuntimeError(name)
    def call(route, event=None):
        request = urllib.request.Request(base + route, headers={'X-Demo-User': 'demo-1', 'Content-Type': 'application/json'}, data=json.dumps(event).encode() if event else None)
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)
    processes = []
    process = None
    logfile = (out / 'own-server.log').open('wb')
    try:
        for phase in ['prepare', 'verify']:
            command = [sys.executable, '-B', '-m', 'catalog.demo_server', '--port', '8876', '--dataset', str(dataset), '--behavior-db', str(db)]
            if phase == 'prepare':
                command.append('--initialize-behavior')
            process = subprocess.Popen(command, cwd=backend, stdout=logfile, stderr=subprocess.STDOUT)
            processes.append(process.pid)
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('Owned server exited; inspect own-server.log')
                try:
                    call('demo-info')
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError('Owned server readiness timeout')
            if phase == 'prepare':
                event = dict(event_id=str(uuid.uuid4()), movie_id=1, kind='favorite', action='set', value=None, time='2026-11-05T00:00:00+00:00', source='SYNTHETIC_DEMO')
                check('HTTP first commit', not call('behavior', event)['data']['duplicate'])
                before = call('behavior')
                check('HTTP duplicate before restart', call('behavior', event)['data']['duplicate'])
                subprocess.run(['node', 'tools/persistence-browser-test.mjs', str(out), phase], cwd=web, check=True)
            else:
                check('new server process', processes[0] != processes[1])
                check('HTTP snapshot survives process restart', call('behavior') == before)
                check('HTTP same event after restart duplicate', call('behavior', event)['data']['duplicate'])
                subprocess.run(['node', 'tools/persistence-browser-test.mjs', str(out), phase], cwd=web, check=True)
            process.terminate()
            process.wait(timeout=10)
            process = None
        (out / 'restart-results.json').write_text(json.dumps({'checks': checks, 'owned_process_ids': processes, 'before': before, 'event': event, 'real_MySQL': 'NOT CONNECTED'}, indent=2), encoding='utf-8')
        print('Real restart integration', len(checks), 'PASS')
    finally:
        if process is not None:
            process.terminate()
            process.wait(timeout=10)
        logfile.close()


if __name__ == '__main__':
    main(*sys.argv[1:])
