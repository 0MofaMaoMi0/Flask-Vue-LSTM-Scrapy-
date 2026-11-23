#!/usr/bin/env python
"""Canonical Windows local Flask backend launcher (127.0.0.1:8091 + DB SSH tunnel).

What it does
------------
1. Reads DB tunnel settings (``db-tunnel.json``) to obtain ``local_port`` (3306)
   and validates the target backend port (default ``8091``), rejecting
   read-only ports (1461 / 3575), the frontend port (5174), and 3306.
2. Ensures all Windows cache, temp, log, and state directories exist under
   ``.local-cache/`` on F:.
3. If ``127.0.0.1:<port>`` already passes both ``/api/v1/health`` and
   ``/api/v1/ready``, returns ``REUSE_EXISTING_BACKEND`` without killing or
   duplicating processes. If the port is occupied by an unhealthy process,
   exits with ``EXIT_PORT_CONFLICT`` (4).
4. Otherwise verifies DB tunnel TCP reachability on ``127.0.0.1:<db_port>``,
   loads ``敏感信息调用/ubuntu-db/backend.local.env`` strictly into the child
   process environment (never logged or printed), and spawns
   ``.local-cache/auth-venv/Scripts/python.exe -B -m app.m6_serve``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import launch_frontend

REPO_ROOT = launch_frontend.REPO_ROOT
BACKEND_DIR = REPO_ROOT / "backend"
M6_SERVE = BACKEND_DIR / "app" / "m6_serve.py"
DEFAULT_PYTHON_EXE = REPO_ROOT / ".local-cache" / "auth-venv" / "Scripts" / "python.exe"
ENV_FILE = REPO_ROOT.parent / "敏感信息调用" / "ubuntu-db" / "backend.local.env"
TUNNEL_JSON = launch_frontend.TUNNEL_JSON

BACKEND_HOST = launch_frontend.BACKEND_HOST
DEFAULT_BACKEND_PORT = launch_frontend.DEFAULT_BACKEND_PORT
DEFAULT_DB_PORT = 3306
HEALTH_PATH = launch_frontend.HEALTH_PATH
READY_PATH = launch_frontend.READY_PATH

LOCAL_CACHE = REPO_ROOT / ".local-cache"
CACHE_ROOT = launch_frontend.CACHE_ROOT
TMP = launch_frontend.TMP
LOGS = launch_frontend.LOGS
STATE = launch_frontend.STATE
MEDIA_CACHE = LOCAL_CACHE / "media"
TRENDING_CACHE = LOCAL_CACHE / "trending"
BACKEND_CACHE_ROOT = LOCAL_CACHE / "backend"
BACKEND_CATALOG_CACHE = BACKEND_CACHE_ROOT / "catalog"
BACKEND_ADMIN_CACHE = BACKEND_CACHE_ROOT / "admin"
BACKEND_REC_CACHE = BACKEND_CACHE_ROOT / "rec"
BACKEND_STATE_DIR = BACKEND_CACHE_ROOT / "state"

EXIT_OK = launch_frontend.EXIT_OK
EXIT_CONFIG = launch_frontend.EXIT_CONFIG
EXIT_BACKEND = launch_frontend.EXIT_BACKEND
EXIT_PORT_CONFLICT = launch_frontend.EXIT_PORT_CONFLICT
EXIT_START_FAILED = launch_frontend.EXIT_START_FAILED

LaunchError = launch_frontend.LaunchError
tcp_open = launch_frontend.tcp_open
get_json_health = launch_frontend.get_json_health
probe_backend_health = launch_frontend.probe_backend_health
now_iso = launch_frontend.now_iso

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _python_exe() -> Path:
    env_py = os.environ.get("MI2_BACKEND_PYTHON_EXE", "").strip()
    return Path(env_py) if env_py else DEFAULT_PYTHON_EXE


def ensure_dirs() -> None:
    """Create all Windows-local cache, temp, log, and state directories on F:."""
    for path in (
        TMP,
        LOGS,
        STATE,
        MEDIA_CACHE,
        TRENDING_CACHE,
        BACKEND_CATALOG_CACHE,
        BACKEND_ADMIN_CACHE,
        BACKEND_REC_CACHE,
        BACKEND_STATE_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def load_settings(tunnel_path: Path = TUNNEL_JSON, port: int | None = None) -> dict:
    """Validate backend port and DB tunnel settings cleanly."""
    return launch_frontend.load_settings(tunnel_path, backend_port=port)


def load_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse KEY=VALUE pairs from backend.local.env without logging values."""
    if not path.is_file():
        raise LaunchError("后端环境文件不存在: %s" % path, EXIT_CONFIG, {"path": str(path)})
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise LaunchError("读取后端环境文件失败: %s" % type(exc).__name__,
                          EXIT_CONFIG, {"path": str(path)}) from None

    parsed: dict[str, str] = {}
    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not _ENV_KEY_RE.match(key):
            # Never echo raw lines or values in exceptions/logs.
            raise LaunchError(
                "环境文件第 %d 行格式无效" % lineno,
                EXIT_CONFIG,
                {"path": str(path), "line": lineno},
            )
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        parsed[key] = value
    return parsed


def build_env(env_vars: dict[str, str], port: int = DEFAULT_BACKEND_PORT,
              base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Construct child process environment with forced local cache and bind settings."""
    env = dict(os.environ if base_env is None else base_env)
    env.update(env_vars)
    env["TEMP"] = str(TMP)
    env["TMP"] = str(TMP)
    env["TMPDIR"] = str(TMP)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["MI2_HOST"] = BACKEND_HOST
    env["MI2_PORT"] = str(port)
    env.setdefault("MI2_ALLOWED_ORIGINS", "http://127.0.0.1:5174")
    env.setdefault("MI2_MEDIA_CACHE", str(MEDIA_CACHE))
    env.setdefault("MI2_TRENDING_CACHE", str(TRENDING_CACHE))
    env.setdefault("MI2_CATALOG_CACHE_DIR", str(BACKEND_CATALOG_CACHE))
    env.setdefault("MI2_ADMIN_CACHE_DIR", str(BACKEND_ADMIN_CACHE))
    env.setdefault("MI2_RECOMMENDATION_CACHE_DIR", str(BACKEND_REC_CACHE))
    env.setdefault("MI2_M6_STATE_DIR", str(BACKEND_STATE_DIR))
    return env


def build_command() -> list[str]:
    return [str(_python_exe()), "-B", "-m", "app.m6_serve"]


def describe(settings: dict, env_path: Path = ENV_FILE) -> dict:
    """Return strictly non-secret metadata for plans and diagnostics."""
    return {
        "backend": "%s (Windows local Flask)" % settings["api_target"],
        "db_tunnel": "127.0.0.1:%d -> %s" % (settings["local_port"], settings["remote_target"]),
        "env_file": str(env_path),
        "readonly_acceptance": "1461/3575 只读验收端口不参与本启动链路",
        "logs": str(LOGS),
        "temp": str(TMP),
        "state": str(STATE),
        "cache_dirs": {
            "media": str(MEDIA_CACHE),
            "trending": str(TRENDING_CACHE),
            "catalog": str(BACKEND_CATALOG_CACHE),
            "admin": str(BACKEND_ADMIN_CACHE),
            "rec": str(BACKEND_REC_CACHE),
            "state": str(BACKEND_STATE_DIR),
        },
    }


def build_dry_run_plan(settings: dict, env_path: Path = ENV_FILE) -> dict:
    """Build a non-secret start plan suitable for --dry-run output."""
    return {
        "result": "DRY_RUN",
        "action": "START_BACKEND",
        "cwd": str(BACKEND_DIR),
        "command": build_command(),
        "env_non_secret": {
            "MI2_HOST": BACKEND_HOST,
            "MI2_PORT": str(settings["backend_port"]),
            "TEMP": str(TMP),
            "TMP": str(TMP),
            "TMPDIR": str(TMP),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        **describe(settings, env_path),
    }


def build_state_payload(pid: int, log_path: Path, settings: dict,
                        ready: bool, exit_code: int | None) -> dict:
    """Build non-secret launch state JSON for backend-launch.json."""
    return {
        "at": now_iso(),
        "pid": pid,
        "log": str(log_path),
        "backend": settings["api_target"],
        "db_tunnel": "127.0.0.1:%d" % settings["local_port"],
        "ready": bool(ready),
        "exit_code": exit_code,
    }


def check_prerequisites(settings: dict, env_path: Path = ENV_FILE) -> dict:
    checks = {
        "backend_dir_exists": BACKEND_DIR.is_dir(),
        "m6_serve_exists": M6_SERVE.is_file(),
        "python_exists": _python_exe().is_file(),
        "tunnel_settings_exists": Path(settings["path"]).is_file(),
        "env_file_exists": env_path.is_file(),
        "db_tunnel_listening": tcp_open("127.0.0.1", settings["local_port"]),
        "backend_port_listening": tcp_open(BACKEND_HOST, settings["backend_port"]),
    }
    checks["all_ok"] = all(checks[key] for key in (
        "backend_dir_exists",
        "m6_serve_exists",
        "python_exists",
        "tunnel_settings_exists",
        "env_file_exists",
    ))
    return checks


def probe_backend(port: int) -> dict:
    if not tcp_open(BACKEND_HOST, port):
        return {"state": "NOT_RUNNING", "port": port}
    ok, health, ready = probe_backend_health(port)
    if ok:
        return {"state": "HEALTHY", "port": port, "health": health, "ready": ready}
    if health["status"] is None and ready["status"] is None:
        return {"state": "PORT_OCCUPIED_NO_HTTP", "port": port, "health": health, "ready": ready}
    return {"state": "RESPONDING_UNHEALTHY", "port": port, "health": health, "ready": ready}


def wait_ready(port: int, timeout_s: float, process: subprocess.Popen | None = None) -> dict:
    deadline = time.time() + timeout_s
    last_health = None
    last_ready = None
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            return {
                "ok": False,
                "error": "PROCESS_EXITED",
                "exit_code": process.returncode,
                "health": last_health,
                "ready": last_ready,
            }
        remaining = max(0.5, deadline - time.time())
        probe_timeout = min(3.0, remaining)
        last_health = get_json_health(port, timeout=probe_timeout,
                                      path=HEALTH_PATH, expected_status="ok")
        last_ready = get_json_health(port, timeout=probe_timeout,
                                     path=READY_PATH, expected_status="ready")
        if last_health["ok"] and last_ready["ok"]:
            return {"ok": True, "health": last_health, "ready": last_ready}
        time.sleep(0.5)
    return {"ok": False, "error": "TIMEOUT", "health": last_health, "ready": last_ready}


def start(settings: dict, args, env_path: Path = ENV_FILE) -> int:
    ensure_dirs()
    if not _python_exe().is_file() or not M6_SERVE.is_file() or not env_path.is_file():
        print("配置错误：后端执行文件、入口脚本或环境文件缺失", file=sys.stderr)
        return EXIT_CONFIG

    if args.dry_run:
        print(json.dumps(build_dry_run_plan(settings, env_path), ensure_ascii=False, indent=2))
        return EXIT_OK

    env_vars = load_env_file(env_path)
    env = build_env(env_vars, port=settings["backend_port"])
    command = build_command()
    log_path = LOGS / ("backend-%s.log" % datetime.now().strftime("%Y%m%dT%H%M%S"))
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            cwd=str(BACKEND_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                           | getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    ready = wait_ready(settings["backend_port"], args.timeout, process=process)
    state = build_state_payload(
        pid=process.pid,
        log_path=log_path,
        settings=settings,
        ready=bool(ready.get("ok")),
        exit_code=process.poll(),
    )
    (STATE / "backend-launch.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not ready.get("ok"):
        print("启动失败：本机后端未在 %.0fs 内通过 %s 与 %s（PID %d，日志 %s）"
              % (args.timeout, HEALTH_PATH, READY_PATH, process.pid, log_path),
              file=sys.stderr)
        return EXIT_START_FAILED
    print(json.dumps({
        "result": "STARTED",
        "pid": process.pid,
        "backend": settings["api_target"],
        "db_tunnel": "127.0.0.1:%d" % settings["local_port"],
        "log": str(log_path),
    }, ensure_ascii=False, indent=2))
    return EXIT_OK


def run(args) -> int:
    env_path = Path(args.env_file) if args.env_file else ENV_FILE
    tunnel_path = Path(args.tunnel_json) if args.tunnel_json else TUNNEL_JSON
    try:
        settings = load_settings(tunnel_path, port=args.port)
    except LaunchError as exc:
        print("配置错误：%s" % exc, file=sys.stderr)
        print(json.dumps(exc.detail, ensure_ascii=False), file=sys.stderr)
        return exc.code

    if args.check:
        ensure_dirs()
        checks = check_prerequisites(settings, env_path=env_path)
        report = {
            "result": "CHECK_OK" if checks["all_ok"] else "CHECK_FAILED",
            "settings": settings,
            "checks": checks,
            "backend_state": {"state": "LISTENING" if checks["backend_port_listening"]
                              else "NOT_RUNNING"},
            "network_calls": "NONE (offline parameter check; no HTTP request)",
            **describe(settings, env_path),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return EXIT_OK if checks["all_ok"] else EXIT_CONFIG

    backend = probe_backend(settings["backend_port"])
    if backend["state"] == "HEALTHY":
        print(json.dumps({
            "result": "REUSE_EXISTING_BACKEND",
            "backend": settings["api_target"],
            "note": "本机后端 %d 已通过 /api/v1/health 与 /api/v1/ready，未重复启动、未结束任何进程。"
                    % settings["backend_port"],
            **describe(settings, env_path),
        }, ensure_ascii=False, indent=2))
        return EXIT_OK
    if backend["state"] in ("RESPONDING_UNHEALTHY", "PORT_OCCUPIED_NO_HTTP"):
        print(json.dumps({
            "result": "PORT_CONFLICT",
            "backend": settings["api_target"],
            "backend_state": backend["state"],
            "health": backend.get("health"),
            "ready": backend.get("ready"),
            "note": "端口 %d 被占用但未通过健康与就绪检查；不结束占用进程，也不更换端口。"
                    % settings["backend_port"],
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return EXIT_PORT_CONFLICT

    if not tcp_open("127.0.0.1", settings["local_port"]):
        print(json.dumps({
            "result": "DB_TUNNEL_UNAVAILABLE",
            "reason": "DB 隧道本地端口 127.0.0.1:%d 未监听" % settings["local_port"],
            "db_tunnel": "127.0.0.1:%d -> %s" % (settings["local_port"], settings["remote_target"]),
            "note": "未启动后端；请先确认 Ubuntu DB SSH 隧道已建立。",
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return EXIT_BACKEND

    try:
        return start(settings, args, env_path=env_path)
    except LaunchError as exc:
        print("配置错误：%s" % exc, file=sys.stderr)
        print(json.dumps(exc.detail, ensure_ascii=False), file=sys.stderr)
        return exc.code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="本机 Flask 后端启动器：通过 DB SSH 隧道连接远程 MySQL，本地监听 127.0.0.1:8091。",
        epilog=("示例：--check 离线校验；--health 只 GET 健康与就绪；--dry-run 只打印启动计划；"
                "默认启动并等待 /api/v1/health 与 /api/v1/ready 就绪。"))
    parser.add_argument("--check", action="store_true",
                        help="离线参数/文件校验，不发起任何网络请求，不读取密钥值")
    parser.add_argument("--health", action="store_true",
                        help="只 GET 本机后端 /api/v1/health 与 /api/v1/ready 并报告")
    parser.add_argument("--dry-run", action="store_true",
                        help="校验前置条件后只打印启动计划，不启动进程，不读取密钥值")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="等待后端就绪的秒数，默认 30")
    parser.add_argument("--port", type=int, default=DEFAULT_BACKEND_PORT,
                        help="本机 Flask 后端端口，默认 8091")
    parser.add_argument("--tunnel-json", default=None,
                        help="覆盖 DB 隧道设置路径（只读取 local_port/remote_target）")
    parser.add_argument("--env-file", default=None,
                        help="覆盖 backend.local.env 路径（默认指向 敏感信息调用/ubuntu-db/backend.local.env）")
    args = parser.parse_args(argv)

    if args.health:
        tunnel_path = Path(args.tunnel_json) if args.tunnel_json else TUNNEL_JSON
        try:
            settings = load_settings(tunnel_path, port=args.port)
        except LaunchError as exc:
            print("配置错误：%s" % exc, file=sys.stderr)
            return exc.code
        ok, health, ready = probe_backend_health(settings["backend_port"])
        print(json.dumps({
            "result": "HEALTH_OK" if ok else "HEALTH_FAILED",
            "port": settings["backend_port"],
            "health": health,
            "ready": ready,
        }, ensure_ascii=False, indent=2))
        return EXIT_OK if ok else EXIT_BACKEND
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
