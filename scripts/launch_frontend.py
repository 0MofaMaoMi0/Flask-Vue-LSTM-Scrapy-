#!/usr/bin/env python
"""Canonical local entry launcher for the current project (5174 + local 8091 backend).

What it does
------------
1. Reads only ``local_port`` and ``remote_target`` from the external DB tunnel
   settings file (``db-tunnel.json``). Every other key is ignored.
2. Validates that the tunnel target is loopback and that tunnel/backend ports
   are valid, rejecting read-only acceptance ports (1461 / 3575), frontend
   port (5174), and the DB tunnel port (3306) as HTTP backend targets.
3. If 5174 already answers as this app, reports REUSE and never starts a second
   dev server and never kills the existing process.
4. Otherwise requires ``GET /api/v1/health`` and ``GET /api/v1/ready`` on the
   local Windows backend (default ``127.0.0.1:8091``) to succeed and then starts
   the existing local Vite dependency with
   ``MI2_API_TARGET=http://127.0.0.1:<backend_port>``.

It performs GET requests only, to health/readiness endpoints. It never logs in,
never sends cookies, never calls MySQL/SSH, never installs anything and never
writes outside the repository's ``.local-cache/startup`` tree; logs, TEMP and
cache stay on F: and are separate from the running Vite cache.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
TUNNEL_JSON = REPO_ROOT.parent / "运行配置" / "Ubuntu" / "db-tunnel.json"
MOVIE_WEB = REPO_ROOT / "movie-web"
DEFAULT_NODE_EXE = Path("F:/Node/node.exe")


def resolve_node_exe() -> Path:
    env_node = os.environ.get("MI2_NODE_EXE", "").strip()
    if env_node:
        return Path(env_node)
    if DEFAULT_NODE_EXE.is_file():
        return DEFAULT_NODE_EXE
    found = shutil.which("node")
    return Path(found) if found else DEFAULT_NODE_EXE


def _node_exe() -> Path:
    env_node = os.environ.get("MI2_NODE_EXE", "").strip()
    return Path(env_node) if env_node else NODE_EXE


NODE_EXE = resolve_node_exe()
VITE_BIN = MOVIE_WEB / "node_modules/vite/bin/vite.js"
FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT = 5174
BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 8091
HEALTH_PATH = "/api/v1/health"
READY_PATH = "/api/v1/ready"
READONLY_PORTS = frozenset({1461, 3575})
FORBIDDEN_BACKEND_PORTS = READONLY_PORTS | frozenset({FRONTEND_PORT, 3306})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
CACHE_ROOT = REPO_ROOT / ".local-cache" / "startup"
TMP = CACHE_ROOT / "tmp"
LOGS = CACHE_ROOT / "logs"
STATE = CACHE_ROOT / "state"
CACHE = CACHE_ROOT / "node-compile"

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_BACKEND = 3
EXIT_PORT_CONFLICT = 4
EXIT_START_FAILED = 5


class LaunchError(RuntimeError):
    def __init__(self, message: str, code: int = EXIT_CONFIG, detail=None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def ensure_dirs() -> None:
    for path in (TMP, LOGS, STATE, CACHE):
        path.mkdir(parents=True, exist_ok=True)


def _validate_port_int(value, name: str) -> int:
    if type(value) is not int or isinstance(value, bool) or not 1 <= value <= 65535:
        raise LaunchError("%s 必须是 1..65535 的整数" % name, EXIT_CONFIG, {name: value})
    return value


def resolve_backend_port(backend_port: int | None = None) -> int:
    """Resolve and validate the local Windows Flask backend HTTP port."""
    if backend_port is not None:
        port = _validate_port_int(backend_port, "backend_port")
    else:
        raw = os.environ.get("MI2_BACKEND_PORT", "").strip()
        if not raw:
            port = DEFAULT_BACKEND_PORT
        elif not (raw.isascii() and raw.isdigit()):
            raise LaunchError("MI2_BACKEND_PORT 必须是 1..65535 的整数", EXIT_CONFIG,
                              {"MI2_BACKEND_PORT": raw})
        else:
            port = _validate_port_int(int(raw), "MI2_BACKEND_PORT")

    if port in READONLY_PORTS:
        raise LaunchError("backend_port 指向只读验收端口 %d，拒绝使用" % port,
                          EXIT_CONFIG, {"backend_port": port})
    if port == FRONTEND_PORT:
        raise LaunchError("backend_port 不能是正常前端端口 5174", EXIT_CONFIG,
                          {"backend_port": port})
    if port in FORBIDDEN_BACKEND_PORTS:
        raise LaunchError("backend_port 不能使用保留端口 %d" % port, EXIT_CONFIG,
                          {"backend_port": port})
    return port


def load_settings(path: Path = TUNNEL_JSON, backend_port: int | None = None) -> dict:
    """Read only local_port and remote_target from db-tunnel.json; reject unsafe values."""
    resolved_backend_port = resolve_backend_port(backend_port)
    if not path.is_file():
        raise LaunchError("隧道设置文件不存在: %s" % path, EXIT_CONFIG, {"path": str(path)})
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise LaunchError("隧道设置不是合法 JSON: %s" % exc, EXIT_CONFIG,
                          {"path": str(path)}) from None
    if type(doc) is not dict:
        raise LaunchError("隧道设置必须是 JSON 对象", EXIT_CONFIG)

    local_port = _validate_port_int(doc.get("local_port"), "local_port")
    if local_port in READONLY_PORTS:
        raise LaunchError("local_port 指向只读验收端口 %d，拒绝使用" % local_port,
                          EXIT_CONFIG, {"local_port": local_port})
    if local_port == FRONTEND_PORT:
        raise LaunchError("local_port 不能是正常前端端口 5174", EXIT_CONFIG,
                          {"local_port": local_port})
    if local_port == resolved_backend_port:
        raise LaunchError("local_port 不能与本机后端端口 %d 冲突" % resolved_backend_port,
                          EXIT_CONFIG, {"local_port": local_port,
                                        "backend_port": resolved_backend_port})

    remote = doc.get("remote_target")
    if type(remote) is not str:
        raise LaunchError("remote_target 必须是 host:port 文本", EXIT_CONFIG,
                          {"remote_target": remote})
    host, sep, port_text = remote.rpartition(":")
    if not sep or not host or not (port_text.isascii() and port_text.isdigit()):
        raise LaunchError("remote_target 必须是 host:port 文本", EXIT_CONFIG,
                          {"remote_target": remote})
    remote_port = int(port_text)
    if not 1 <= remote_port <= 65535:
        raise LaunchError("remote_target 端口越界", EXIT_CONFIG,
                          {"remote_target": remote})
    if remote_port in READONLY_PORTS or remote_port == FRONTEND_PORT:
        raise LaunchError("remote_target 指向禁止端口 %d，拒绝使用" % remote_port,
                          EXIT_CONFIG, {"remote_target": remote})
    if host not in LOOPBACK_HOSTS:
        raise LaunchError("remote_target 必须是回环地址，当前为 %r" % host,
                          EXIT_CONFIG, {"remote_target": remote})

    return {
        "path": str(path),
        "local_port": local_port,
        "remote_target": remote,
        "remote_host": host,
        "remote_port": remote_port,
        "ignored_keys": sorted(set(doc) - {"local_port", "remote_target"}),
        "backend_host": BACKEND_HOST,
        "backend_port": resolved_backend_port,
        "frontend_base": "http://%s:%d" % (FRONTEND_HOST, FRONTEND_PORT),
        "api_target": "http://%s:%d" % (BACKEND_HOST, resolved_backend_port),
    }


def tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def get_json_health(port: int, timeout: float = 5.0,
                    path: str = HEALTH_PATH, expected_status: str = "ok") -> dict:
    """GET-only probe for /api/v1/health or /api/v1/ready; no cookies, no auth, no proxy."""
    host = FRONTEND_HOST if port == FRONTEND_PORT else BACKEND_HOST
    url = "http://%s:%d%s" % (host, port, path)
    started = time.perf_counter()
    request = urllib.request.Request(url, method="GET", headers={
        "User-Agent": "m10-normal-entry-launcher/1.0",
        "Accept": "application/json",
    })
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    result = {
        "url": url, "ok": False, "status": None, "error": None,
        "elapsed_ms": None, "body": None,
    }
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(4000)
            result["status"] = response.status
            result["body"] = body.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        result["status"] = exc.code
        result["error"] = "HTTPError"
        result["body"] = exc.read(4000).decode("utf-8", "replace")
    except Exception as exc:
        result["error"] = type(exc).__name__
        result["error_text"] = str(exc)[:300]
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    if result["status"] == 200 and result["body"]:
        try:
            doc = json.loads(result["body"])
        except ValueError:
            doc = None
        if type(doc) is dict:
            result["json"] = doc
            data = doc.get("data")
            result["ok"] = (doc.get("code") in (0, "OK")
                            and type(data) is dict
                            and data.get("status") == expected_status)
    return result


def probe_backend_health(port: int, timeout: float = 5.0) -> tuple[bool, dict, dict]:
    """Check both /api/v1/health and /api/v1/ready on the local backend port."""
    health = get_json_health(port, timeout=timeout, path=HEALTH_PATH, expected_status="ok")
    ready = get_json_health(port, timeout=timeout, path=READY_PATH, expected_status="ready")
    return bool(health["ok"] and ready["ok"]), health, ready


def probe_frontend() -> dict:
    if not tcp_open(FRONTEND_HOST, FRONTEND_PORT):
        return {"state": "NOT_RUNNING", "port": FRONTEND_PORT}
    health = get_json_health(FRONTEND_PORT)
    if health["ok"]:
        return {"state": "HEALTHY", "port": FRONTEND_PORT, "health": health}
    if health["status"] is None:
        return {"state": "PORT_OCCUPIED_NO_HTTP", "port": FRONTEND_PORT, "health": health}
    return {"state": "RESPONDING_UNHEALTHY", "port": FRONTEND_PORT, "health": health}


def check_prerequisites(settings: dict) -> dict:
    checks = {
        "movie_web_exists": MOVIE_WEB.is_dir(),
        "node_exists": _node_exe().is_file(),
        "vite_exists": VITE_BIN.is_file(),
        "tunnel_settings_exists": Path(settings["path"]).is_file(),
        "db_tunnel_listening": tcp_open("127.0.0.1", settings["local_port"]),
        "backend_port_listening": tcp_open(BACKEND_HOST, settings["backend_port"]),
        "frontend_port_listening": tcp_open(FRONTEND_HOST, FRONTEND_PORT),
    }
    checks["all_ok"] = all(checks[key] for key in (
        "movie_web_exists", "node_exists", "vite_exists", "tunnel_settings_exists"))
    return checks


def build_env(settings: dict) -> dict:
    env = dict(os.environ)
    env["MI2_API_TARGET"] = settings["api_target"]
    env["TEMP"] = str(TMP)
    env["TMP"] = str(TMP)
    env["TMPDIR"] = str(TMP)
    env["NODE_COMPILE_CACHE"] = str(CACHE)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Remove values that would silently redirect the frontend to legacy targets.
    for key in ("VITE_API_TARGET", "MI2_TUNNEL_PORT"):
        env.pop(key, None)
    return env


def build_command() -> list[str]:
    return [str(_node_exe()), str(VITE_BIN), "--host", FRONTEND_HOST,
            "--port", str(FRONTEND_PORT), "--strictPort"]


def describe(settings: dict) -> dict:
    return {
        "normal_ui": "%s/ (5174, Vite dev server)" % settings["frontend_base"],
        "api_target": settings["api_target"],
        "backend": "%s:%d (Windows local Flask)" % (
            settings["backend_host"], settings["backend_port"]),
        "db_tunnel": "127.0.0.1:%d -> %s" % (settings["local_port"], settings["remote_target"]),
        "readonly_acceptance": "1461/3575 只读验收端口不参与本启动链路",
        "logs": str(LOGS),
        "temp": str(TMP),
    }


def wait_ready(timeout_s: float) -> dict:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = get_json_health(FRONTEND_PORT)
        if last["ok"]:
            return last
        time.sleep(1.0)
    return last or {"ok": False, "error": "TIMEOUT"}


def start(settings: dict, args) -> int:
    ensure_dirs()
    command = build_command()
    env = build_env(settings)
    plan = {
        "action": "START_FRONTEND",
        "cwd": str(MOVIE_WEB),
        "command": command,
        "env": {k: env[k] for k in ("MI2_API_TARGET", "TEMP", "TMP", "TMPDIR",
                                    "NODE_COMPILE_CACHE")},
    }
    if args.dry_run:
        print(json.dumps({"result": "DRY_RUN", **plan, **describe(settings)},
                         ensure_ascii=False, indent=2))
        return EXIT_OK

    log_path = LOGS / ("frontend-%s.log" % datetime.now().strftime("%Y%m%dT%H%M%S"))
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command, cwd=str(MOVIE_WEB), env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                           | getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        )
    ready = wait_ready(args.timeout)
    state = {
        "at": now_iso(), "pid": process.pid, "log": str(log_path),
        "mi2_api_target": env["MI2_API_TARGET"], "ready": ready["ok"],
        "frontend": settings["frontend_base"], "exit_code": process.poll(),
    }
    (STATE / "last-launch.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not ready["ok"]:
        print("启动失败：前端未在 %.0fs 内通过 %s%s（PID %d，日志 %s）"
              % (args.timeout, settings["frontend_base"], HEALTH_PATH,
                 process.pid, log_path), file=sys.stderr)
        return EXIT_START_FAILED
    print(json.dumps({
        "result": "STARTED", "pid": process.pid,
        "frontend": settings["frontend_base"] + "/",
        "api_target": settings["api_target"], "log": str(log_path),
    }, ensure_ascii=False, indent=2))
    return EXIT_OK


def run(args) -> int:
    try:
        settings = load_settings(
            Path(args.tunnel_json) if args.tunnel_json else TUNNEL_JSON,
            backend_port=args.backend_port,
        )
    except LaunchError as exc:
        print("配置错误：%s" % exc, file=sys.stderr)
        print(json.dumps(exc.detail, ensure_ascii=False), file=sys.stderr)
        return exc.code

    if args.check:
        checks = check_prerequisites(settings)
        report = {
            "result": "CHECK_OK" if checks["all_ok"] else "CHECK_FAILED",
            "settings": settings, "checks": checks,
            "frontend": {"state": "LISTENING" if checks["frontend_port_listening"]
                         else "NOT_RUNNING"},
            "network_calls": "NONE (offline parameter check; no HTTP request)",
            **describe(settings),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return EXIT_OK if checks["all_ok"] else EXIT_CONFIG

    frontend = probe_frontend()
    if frontend["state"] == "HEALTHY":
        print(json.dumps({
            "result": "REUSE_EXISTING_FRONTEND",
            "frontend": settings["frontend_base"] + "/",
            "note": "5174 已通过 /api/v1/health，未重复启动、未结束任何进程。",
            "reuse_note": "复用前请确认其 MI2_API_TARGET 指向本机后端端口 %d。"
                          % settings["backend_port"],
            **describe(settings),
        }, ensure_ascii=False, indent=2))
        return EXIT_OK
    if frontend["state"] in ("RESPONDING_UNHEALTHY", "PORT_OCCUPIED_NO_HTTP"):
        print(json.dumps({
            "result": "PORT_CONFLICT",
            "frontend": settings["frontend_base"],
            "frontend_state": frontend["state"],
            "detail": frontend.get("health"),
            "note": "5174 被占用但未通过健康检查；不结束占用进程，也不另起端口。",
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return EXIT_PORT_CONFLICT
    if not tcp_open(BACKEND_HOST, settings["backend_port"]):
        print(json.dumps({
            "result": "BACKEND_UNAVAILABLE",
            "reason": "本机后端端口 %s:%d 未监听" % (BACKEND_HOST, settings["backend_port"]),
            "db_tunnel": "127.0.0.1:%d -> %s" % (settings["local_port"], settings["remote_target"]),
            "note": "未启动前端；请先启动本机后端（依赖 DB 隧道），不回退只读端口。",
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return EXIT_BACKEND
    ok, health, ready = probe_backend_health(settings["backend_port"])
    if not ok:
        print(json.dumps({
            "result": "BACKEND_UNAVAILABLE",
            "reason": "本机后端未通过 GET %s 或 %s" % (HEALTH_PATH, READY_PATH),
            "health": health,
            "ready": ready,
            "note": "未启动前端；请先确认本机后端与 DB 隧道就绪，不回退只读端口。",
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return EXIT_BACKEND
    return start(settings, args)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="项目级正常入口：校验配置并启动 5174 前端（代理至本机 8091 后端）。",
        epilog=("示例：--check 离线校验；--health 只 GET 健康；--dry-run 只打印启动计划；"
                "默认真正启动。5174 已健康时只复用不重启。"))
    parser.add_argument("--check", action="store_true",
                        help="离线参数/文件校验，不发起任何网络请求")
    parser.add_argument("--health", action="store_true",
                        help="只 GET 本机后端 /api/v1/health 与 /api/v1/ready 并报告")
    parser.add_argument("--dry-run", action="store_true",
                        help="校验后端健康后只打印启动命令与环境，不启动进程")
    parser.add_argument("--timeout", type=float, default=90.0,
                        help="等待前端就绪的秒数，默认 90")
    parser.add_argument("--tunnel-json", default=None,
                        help="覆盖 DB 隧道设置路径（只读取 local_port/remote_target）")
    parser.add_argument("--backend-port", type=int, default=None,
                        help="覆盖本机 Flask 后端端口（默认 8091 或 MI2_BACKEND_PORT）")
    args = parser.parse_args(argv)

    if args.health:
        try:
            settings = load_settings(
                Path(args.tunnel_json) if args.tunnel_json else TUNNEL_JSON,
                backend_port=args.backend_port,
            )
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
