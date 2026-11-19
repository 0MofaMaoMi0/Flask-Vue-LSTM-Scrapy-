"""Double-click entry: reuse the canonical launcher and open its verified URL."""
import os
import sys
import subprocess
import time
from pathlib import Path
import launch_frontend


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    no_browser = '--no-browser' in args
    if no_browser:
        args.remove('--no-browser')
    if not args:
        try:
            settings = launch_frontend.load_settings()
            port = settings['local_port']
            if not launch_frontend.tcp_open('127.0.0.1', port):
                runtime = launch_frontend.TUNNEL_JSON.parent
                if (runtime / 'stop-http-tunnel').exists():
                    print('Tunnel has an explicit stop marker; remove it only when intentionally resuming.', file=sys.stderr)
                    return 3
                supervisor = Path(__file__).with_name('tunnel_supervisor.py')
                print('Starting the configured Ubuntu SSH tunnel...')
                with (runtime / 'tunnel-supervisor-errors.txt').open('ab') as log:
                    process = subprocess.Popen([sys.executable, '-B', str(supervisor)], stdin=subprocess.DEVNULL,
                        stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
                for attempt in range(30):
                    if launch_frontend.tcp_open('127.0.0.1', port, timeout=.2):
                        break
                    if process.poll() is not None:
                        break
                    time.sleep(.5)
        except (OSError, launch_frontend.LaunchError) as error:
            print('Tunnel startup failed: ' + type(error).__name__, file=sys.stderr)
            return 3
    result = launch_frontend.main(args)
    if result:
        print('Startup failed. Check the message above; Ubuntu and the SSH tunnel must be available.', file=sys.stderr)
        return result
    if not args and not no_browser:
        url = 'http://%s:%d/home' % (launch_frontend.FRONTEND_HOST, launch_frontend.FRONTEND_PORT)
        try:
            os.startfile(url)
        except OSError as error:
            print('Project is ready. Open %s manually (%s).' % (url, type(error).__name__))
        else:
            print('Project is ready: ' + url)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
