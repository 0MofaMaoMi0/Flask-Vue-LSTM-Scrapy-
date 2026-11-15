#!/usr/bin/env python
"""Canonical local entry launcher for the current project (5174 + SSH HTTP tunnel).

What it does
------------
1. Reads only ``local_port`` and ``remote_target`` from the external tunnel
   settings file (``http-tunnel.json``). Every other key is ignored.
2. Validates that the target is a loopback address and that both ports are
   real, allowed ports. The read-only acceptance ports 1461 / 3575 and the old
   fallback 8091 are rejected.
3. If 5174 already answers as this app, reports REUSE and never starts a second
   dev server and never kills the existing process.
4. Otherwise requires ``GET /api/v1/health`` on the tunnel port to succeed and
   then starts the existing local Vite dependency with
   ``MI2_API_TARGET=http://127.0.0.1:<tunnel_port>``.

It performs GET requests only, to the health endpoint. It never logs in,
never sends cookies, never calls MySQL/SSH, never installs anything and never
writes outside the repository's ``.local-cache/startup`` tree; logs, TEMP and
cache stay on F: and are separate from the running Vite cache.
"""
from __future__ import annotations

import argparse
import json
import os
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
TUNNEL_JSON = Path("F:/M6-VM-control-20261106-183615/m6-implementation/http-tunnel.json")
MOVIE_WEB = REPO_ROOT / "movie-web"
NODE_EXE = Path("F:/Node/node.exe")
VITE_BIN = MOVIE_WEB / "node_modules/vite/bin/vite.js"
FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT = 5174
HEALTH_PATH = "/api/v1/health"
READONLY_PORTS = frozenset({1461, 3575})
OLD_FALLBACK_PORTS = frozenset({8091})
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


def load_settings(path: Path = TUNNEL_JSON) -> dict:
    """Read only local_port and remote_target; reject unsafe values."""
    if not path.is_file():
        raise LaunchError("隧道设置文件不存在: %s" % path, EXIT_CONFIG, {"path": str(path)})
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise LaunchError("隧道设置不是合法 JSON: %s" % exc, EXIT_CONFIG,
                          {"path": str(path)}) from None
    if type(doc) is not dict:
        raise LaunchError("隧道设置必须是 JSON 对象", EXIT_CONFIG)

    local_port = doc.get("local_port")
    if type(local_port) is not int or isinstance(local_port, bool) or not 1 <= local_port <= 65535:
        raise LaunchError("local_port 必须是 1..65535 的整数", EXIT_CONFIG,
                          {"local_port": local_port})
    if local_port in READONLY_PORTS:
        raise LaunchError("local_port 指向只读验收端口 %d，拒绝使用" % local_port,
                          EXIT_CONFIG, {"local_port": local_port})
    if local_port in OLD_FALLBACK_PORTS:
        raise LaunchError("local_port 指向旧回退端口 %d，拒绝使用" % local_port,
                          EXIT_CONFIG, {"local_port": local_port})
    if local_port == FRONTEND_PORT:
        raise LaunchError("local_port 不能是正常前端端口 5174", EXIT_CONFIG,
                          {"local_port": local_port})

    remote = doc.get("remote_target")
    if type(remote) is not str:
        raise LaunchError("remote_target 必须是 host:port 文本", EXIT_CONFIG,
                          {"remote_target": remote})
    host, sep, port_text = remote.rpartition(":")
    if not sep or not host or not port_text.isdigit():
        raise LaunchError("remote_target 必须是 host:port 文本", EXIT_CONFIG,
                          {"remote_target": remote})
    remote_port = int(port_text)
    if not 1 <= remote_port <= 65535:
        raise LaunchError("remote_target 端口越界", EXIT_CONFIG,
                          {"remote_target": remote})
    if remote_port in READONLY_PORTS or remote_port in OLD_FALLBACK_PORTS:
        raise LaunchError("remote_target 指向只读/旧端口 %d，拒绝使用" % remote_port,
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
        "frontend_base": "http://%s:%d" % (FRONTEND_HOST, FRONTEND_PORT),
        "api_target": "http://127.0.0.1:%d" % local_port,
    }


def tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def get_json_health(port: int, timeout: float = 5.0) -> dict:
    """GET-only health probe; no cookies, no auth, no proxy."""
    url = "http://%s:%d%s" % (FRONTEND_HOST if port == FRONTEND_PORT else "127.0.0.1",
                              port, HEALTH_PATH)
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
                            and data.get("status") == "ok")
    return result


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
        "node_exists": NODE_EXE.is_file(),
        "vite_exists": VITE_BIN.is_file(),
        "tunnel_settings_exists": Path(settings["path"]).is_file(),
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
    # Remove values that would silently redirect the frontend to the old stack.
    for key in ("VITE_API_TARGET", "MI2_TUNNEL_PORT"):
        env.pop(key, None)
    return env


def build_command() -> list[str]:
    return [str(NODE_EXE), str(VITE_BIN), "--host", FRONTEND_HOST,
            "--port", str(FRONTEND_PORT), "--strictPort"]


def describe(settings: dict) -> dict:
    return {
        "normal_ui": "%s/ (5174, Vite dev server)" % settings["frontend_base"],
        "api_target": settings["api_target"],
        "tunnel": "127.0.0.1:%d -> %s" % (settings["local_port"], settings["remote_target"]),
        "readonly_acceptance": "1461/3575 只读验收端口不参与本启动链路",
        "old_fallback": "8091 不被使用",
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
    log = log_path.open("ab")
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
        settings = load_settings(Path(args.tunnel_json) if args.tunnel_json else TUNNEL_JSON)
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
            "reuse_note": "复用前请确认其 MI2_API_TARGET 指向本次隧道端口 %d。"
                          % settings["local_port"],
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
    if not tcp_open("127.0.0.1", settings["local_port"]):
        print(json.dumps({
            "result": "BACKEND_UNAVAILABLE",
            "reason": "隧道本地端口 127.0.0.1:%d 未监听" % settings["local_port"],
            "tunnel": settings["remote_target"],
            "note": "未启动前端；不启动/重启隧道或后端，不回退 8091 或只读端口。",
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return EXIT_BACKEND
    health = get_json_health(settings["local_port"])
    if not health["ok"]:
        print(json.dumps({
            "result": "BACKEND_UNAVAILABLE",
            "reason": "隧道端口未通过 GET %s" % HEALTH_PATH,
            "detail": health,
            "note": "未启动前端；不启动/重启隧道或后端，不回退 8091 或只读端口。",
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return EXIT_BACKEND
    return start(settings, args)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="项目级正常入口：校验隧道设置并通过现有 Vite 启动 5174 前端（GET 健康检查）。",
        epilog=("示例：--check 离线校验；--health 只 GET 健康；--dry-run 只打印启动计划；"
                "默认真正启动。5174 已健康时只复用不重启。"))
    parser.add_argument("--check", action="store_true",
                        help="离线参数/文件校验，不发起任何网络请求")
    parser.add_argument("--health", action="store_true",
                        help="只 GET 隧道端口 /api/v1/health 并报告")
    parser.add_argument("--dry-run", action="store_true",
                        help="校验后端健康后只打印启动命令与环境，不启动进程")
    parser.add_argument("--timeout", type=float, default=90.0,
                        help="等待前端就绪的秒数，默认 90")
    parser.add_argument("--tunnel-json", default=None,
                        help="覆盖隧道设置路径（只读取 local_port/remote_target）")
    args = parser.parse_args(argv)

    if args.health:
        try:
            settings = load_settings(Path(args.tunnel_json) if args.tunnel_json else TUNNEL_JSON)
        except LaunchError as exc:
            print("配置错误：%s" % exc, file=sys.stderr)
            return exc.code
        health = get_json_health(settings["local_port"])
        print(json.dumps({"result": "HEALTH_OK" if health["ok"] else "HEALTH_FAILED",
                          "port": settings["local_port"], "health": health},
                         ensure_ascii=False, indent=2))
        return EXIT_OK if health["ok"] else EXIT_BACKEND
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
