"""Double-click entry: orchestrate DB SSH tunnel, local Flask 8091, Vite 5174, and browser."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import launch_backend
import launch_frontend

STOP_MARKER_NAME = "stop-db-tunnel"
SUPERVISOR_ERROR_LOG_NAME = "tunnel-supervisor-errors.txt"
DB_TUNNEL_WAIT_SECONDS = 15.0


def _ensure_db_tunnel(tunnel_path: Path, backend_port: int | None = None) -> int:
    """Ensure the configured DB SSH tunnel is listening on 127.0.0.1:<local_port>."""
    try:
        settings = launch_frontend.load_settings(tunnel_path, backend_port=backend_port)
    except launch_frontend.LaunchError as exc:
        print("DB tunnel configuration error: %s" % exc, file=sys.stderr)
        return exc.code

    db_port = settings["local_port"]
    if launch_frontend.tcp_open("127.0.0.1", db_port):
        return launch_frontend.EXIT_OK

    runtime = tunnel_path.parent
    if (runtime / STOP_MARKER_NAME).exists():
        print("DB tunnel has an explicit stop marker (%s); remove it only when intentionally resuming."
              % STOP_MARKER_NAME, file=sys.stderr)
        return launch_frontend.EXIT_BACKEND

    supervisor = HERE / "tunnel_supervisor.py"
    cmd = [sys.executable, "-B", str(supervisor)]
    if tunnel_path != launch_frontend.TUNNEL_JSON:
        cmd.extend(["--tunnel-json", str(tunnel_path)])

    print("Starting the configured Ubuntu DB SSH tunnel...")
    try:
        with (runtime / SUPERVISOR_ERROR_LOG_NAME).open("ab") as log:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except OSError as error:
        print("DB tunnel supervisor failed to start: " + type(error).__name__, file=sys.stderr)
        return launch_frontend.EXIT_BACKEND

    deadline = time.monotonic() + DB_TUNNEL_WAIT_SECONDS
    while time.monotonic() < deadline:
        if launch_frontend.tcp_open("127.0.0.1", db_port, timeout=0.2):
            return launch_frontend.EXIT_OK
        if process.poll() is not None:
            break
        time.sleep(0.5)

    if launch_frontend.tcp_open("127.0.0.1", db_port, timeout=0.2):
        return launch_frontend.EXIT_OK
    print("DB tunnel did not become reachable on 127.0.0.1:%d within %.0fs."
          % (db_port, DB_TUNNEL_WAIT_SECONDS), file=sys.stderr)
    return launch_frontend.EXIT_BACKEND


def _build_forwarded_args(parsed) -> tuple[list[str], list[str]]:
    backend_args: list[str] = []
    frontend_args: list[str] = []
    for flag, enabled in (("--check", parsed.check), ("--health", parsed.health), ("--dry-run", parsed.dry_run)):
        if enabled:
            backend_args.append(flag)
            frontend_args.append(flag)
    if parsed.tunnel_json:
        backend_args.extend(["--tunnel-json", parsed.tunnel_json])
        frontend_args.extend(["--tunnel-json", parsed.tunnel_json])
    if parsed.backend_port is not None:
        backend_args.extend(["--port", str(parsed.backend_port)])
        frontend_args.extend(["--backend-port", str(parsed.backend_port)])
    if parsed.timeout is not None:
        backend_args.extend(["--timeout", str(parsed.timeout)])
        frontend_args.extend(["--timeout", str(parsed.timeout)])
    return backend_args, frontend_args


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Orchestrate Ubuntu DB SSH tunnel, local Flask 8091, Vite 5174, and browser."
    )
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not open the browser after startup")
    parser.add_argument("--check", action="store_true",
                        help="Run offline prerequisite checks for backend and frontend")
    parser.add_argument("--health", action="store_true",
                        help="Run read-only health/ready checks for backend and frontend")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print start plans without starting processes")
    parser.add_argument("--tunnel-json", default=None,
                        help="Override path to db-tunnel.json")
    parser.add_argument("--timeout", type=float, default=None,
                        help="Override startup readiness timeout in seconds")
    parser.add_argument("--backend-port", type=int, default=None,
                        help="Override local Flask backend port (default 8091)")
    parsed = parser.parse_args(argv)

    backend_args, frontend_args = _build_forwarded_args(parsed)
    if parsed.check or parsed.health or parsed.dry_run:
        rc_backend = launch_backend.main(backend_args)
        rc_frontend = launch_frontend.main(frontend_args)
        return rc_backend if rc_backend != 0 else rc_frontend

    tunnel_path = Path(parsed.tunnel_json) if parsed.tunnel_json else launch_frontend.TUNNEL_JSON
    rc_tunnel = _ensure_db_tunnel(tunnel_path, backend_port=parsed.backend_port)
    if rc_tunnel != 0:
        print("Startup failed. Ubuntu VM and its DB SSH tunnel (127.0.0.1:3306) must be available.",
              file=sys.stderr)
        return rc_tunnel

    rc_backend = launch_backend.main(backend_args)
    if rc_backend != 0:
        print("Local backend startup failed. Check .local-cache/startup/logs/ for details.",
              file=sys.stderr)
        return rc_backend

    rc_frontend = launch_frontend.main(frontend_args)
    if rc_frontend != 0:
        print("Frontend startup failed. Check the message above and .local-cache/startup/logs/.",
              file=sys.stderr)
        return rc_frontend

    if not parsed.no_browser:
        url = "http://%s:%d/home" % (launch_frontend.FRONTEND_HOST, launch_frontend.FRONTEND_PORT)
        try:
            os.startfile(url)
        except OSError as error:
            print("Project is ready. Open %s manually (%s)." % (url, type(error).__name__))
        else:
            print("Project is ready: " + url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
