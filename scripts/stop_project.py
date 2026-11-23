#!/usr/bin/env python
"""Safe, strict-ownership stopper for MovieInsight 2.0 Windows local stack.

Stops ONLY verified project processes in strict teardown order:
    1. ``supervisor`` (``tunnel_supervisor.py``) — awaited to exit first so it
       cannot respawn ``ssh.exe``;
    2. ``ssh`` (Ubuntu DB SSH tunnel on ``127.0.0.1:<db_port>``);
    3. ``backend`` (Windows local Flask ``app.m6_serve`` on ``127.0.0.1:<backend_port>``);
    4. ``frontend`` (Vite dev server on ``127.0.0.1:5174``).

Any listening socket or stale state PID failing strict ownership checks is
marked ``SKIPPED_UNOWNED`` and NEVER terminated. Never reads ``敏感信息调用/``,
never writes to ``C:``, and never creates ``stop-db-tunnel`` so the next
``启动项目.cmd`` run works cleanly.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import launch_frontend

REPO_ROOT = launch_frontend.REPO_ROOT
TUNNEL_JSON = launch_frontend.TUNNEL_JSON
STATE = launch_frontend.STATE
FRONTEND_HOST = launch_frontend.FRONTEND_HOST
FRONTEND_PORT = launch_frontend.FRONTEND_PORT
BACKEND_HOST = launch_frontend.BACKEND_HOST
DEFAULT_BACKEND_PORT = launch_frontend.DEFAULT_BACKEND_PORT
DEFAULT_DB_PORT = 3306

EXIT_OK = launch_frontend.EXIT_OK
EXIT_CONFIG = launch_frontend.EXIT_CONFIG
EXIT_STOP_FAILED = 6

STOP_ORDER = ("supervisor", "ssh", "backend", "frontend")
SUPERVISOR_STATE_NAME = "tunnel-supervisor.json"
BACKEND_STATE_NAME = "backend-launch.json"
FRONTEND_STATE_NAME = "last-launch.json"
STOP_MARKER_NAME = "stop-db-tunnel"
REPO_MARKER_TOKEN = "flask-vue-lstm-scrapy"

DEFAULT_GRACE_TIMEOUT_S = 2.0
DEFAULT_FORCE_TIMEOUT_S = 5.0

_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_TIMEOUT = 0x00000102
_STILL_ACTIVE = 259


class StopError(RuntimeError):
    """Structured error raised when runtime inspection or configuration fails."""

    def __init__(self, message: str, code: int = EXIT_STOP_FAILED, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


def _norm_text(value: Any) -> str:
    """Normalize paths/command lines for case-insensitive slash-agnostic matching."""
    return str(value or "").replace("\\", "/").casefold()


def _normalize_pid(value: Any) -> int | None:
    """Return positive int PID or None (rejecting bool and invalid types)."""
    if type(value) is int and not isinstance(value, bool) and value > 0:
        return value
    return None


def _is_path_inside(candidate_path: Any, root_path: Path) -> bool:
    """Return True if candidate_path lies inside root_path on boundary."""
    norm_candidate = _norm_text(candidate_path).rstrip("/")
    norm_root = _norm_text(root_path).rstrip("/")
    if not norm_candidate or not norm_root:
        return False
    return norm_candidate == norm_root or norm_candidate.startswith(norm_root + "/")


def _contains_marker(text_norm: str, marker: str, allow_slash_prefix: bool) -> bool:
    """Check whether marker appears in normalized command text with token boundaries."""
    cleaned = marker.rstrip("/")
    if not cleaned:
        return False
    prefix = r"(?:^|[/\s\"'=])" if allow_slash_prefix else r"(?:^|[\s\"'=])"
    pattern = prefix + re.escape(cleaned) + r"(?:$|[/\s\"'])"
    return bool(re.search(pattern, text_norm))


def _has_repo_or_state_marker(
    cmd_norm: str,
    repo_root: Path,
    state_dir: Path | None = None,
) -> bool:
    """Return True if cmd_norm references this repo or configured tunnel state directory."""
    if _contains_marker(cmd_norm, REPO_MARKER_TOKEN, allow_slash_prefix=True):
        return True
    if _contains_marker(cmd_norm, _norm_text(repo_root), allow_slash_prefix=False):
        return True
    if state_dir is not None and _contains_marker(
        cmd_norm, _norm_text(state_dir), allow_slash_prefix=False
    ):
        return True
    return False


def _extract_role_state_pids(role: str, state_pids: Any) -> set[int]:
    """Extract known state-file PIDs for the given role from a set, list, or dict."""
    if state_pids is None:
        return set()
    result: set[int] = set()
    if isinstance(state_pids, Mapping):
        role_key_map = {
            "supervisor": ("supervisor", "supervisor_pid"),
            "ssh": ("ssh", "ssh_pid"),
            "backend": ("backend", "backend_pid"),
            "frontend": ("frontend", "frontend_pid"),
        }
        matched_keys = [k for k in role_key_map.get(role, (role,)) if k in state_pids]
        values = [state_pids[k] for k in matched_keys] if matched_keys else list(state_pids.values())
        for val in values:
            if isinstance(val, Iterable) and not isinstance(val, (str, bytes, Mapping)):
                for item in val:
                    pid = _normalize_pid(item)
                    if pid is not None:
                        result.add(pid)
            else:
                pid = _normalize_pid(val)
                if pid is not None:
                    result.add(pid)
        return result
    if isinstance(state_pids, Iterable) and not isinstance(state_pids, (str, bytes)):
        for item in state_pids:
            pid = _normalize_pid(item)
            if pid is not None:
                result.add(pid)
    return result


def _is_owned_supervisor(cmd_norm: str, repo_root: Path, state_dir: Path | None) -> bool:
    return bool(
        re.search(r"(?:^|[/\s\"'])tunnel_supervisor\.py(?:$|[\s\"'])", cmd_norm)
        and _has_repo_or_state_marker(cmd_norm, repo_root, state_dir)
    )


def _is_owned_ssh(
    cmd_norm: str,
    repo_root: Path,
    state_dir: Path | None,
    db_port: int,
) -> bool:
    return bool(
        re.search(r"(?:^|[/\s\"'])m6_ed25519(?:$|[\s\"'])", cmd_norm)
        and re.search(r"(?:^|[\s\"'=]|-l)127\.0\.0\.1:%d:" % int(db_port), cmd_norm)
        and _has_repo_or_state_marker(cmd_norm, repo_root, state_dir)
    )


def _has_backend_module_token(cmd_norm: str) -> bool:
    return bool(re.search(r"(?:^|[\s\"'])app\.m6_serve(?:$|[\s\"'])", cmd_norm))


def _is_owned_backend(
    pid: int | None,
    ppid: int | None,
    cmd_norm: str,
    exe_norm: str,
    repo_root: Path,
    role_state_pids: set[int],
    proc_map: Mapping[int, Mapping[str, Any]] | None,
) -> bool:
    if not _has_backend_module_token(cmd_norm):
        return False
    if _is_path_inside(exe_norm, repo_root):
        return True
    if pid is not None and pid in role_state_pids:
        return True
    # Windows auth-venv launcher stub spawns a child python.exe under Program Files
    # while backend-launch.json records the stub PID. Verify parent compatibility.
    if ppid is not None:
        parent = proc_map.get(ppid) if proc_map is not None else None
        if ppid in role_state_pids:
            if proc_map is None:
                return True
            if parent is not None and (
                _is_path_inside(parent.get("ExecutablePath"), repo_root)
                or _has_backend_module_token(_norm_text(parent.get("CommandLine")))
            ):
                return True
        if parent is not None and (
            _is_path_inside(parent.get("ExecutablePath"), repo_root)
            and _has_backend_module_token(_norm_text(parent.get("CommandLine")))
        ):
            return True
    return False


def _is_owned_frontend(cmd_norm: str, repo_root: Path, frontend_port: int) -> bool:
    return bool(
        re.search(r"(?:^|[/\s\"'])vite\.js(?:$|[\s\"'])", cmd_norm)
        and re.search(r"(?:^|\s)--port(?:\s+|=)%d(?:$|[\s\"'])" % int(frontend_port), cmd_norm)
        and _has_repo_or_state_marker(cmd_norm, repo_root, state_dir=None)
    )


def is_owned_process(
    role: str,
    proc_info: Mapping[str, Any] | None,
    repo_root: Path = REPO_ROOT,
    state_dir: Path | None = None,
    state_pids: Any = None,
    db_port: int = DEFAULT_DB_PORT,
    frontend_port: int = FRONTEND_PORT,
    proc_map: Mapping[int, Mapping[str, Any]] | None = None,
) -> bool:
    """Strictly verify whether proc_info belongs to this project's role.

    Never trusts PID alone without command-line/executable corroboration so a
    recycled OS PID or unrelated user service is never terminated.
    """
    if not isinstance(proc_info, Mapping):
        return False
    cmd_norm = _norm_text(proc_info.get("CommandLine"))
    if not cmd_norm:
        return False
    exe_norm = _norm_text(proc_info.get("ExecutablePath"))
    pid = _normalize_pid(proc_info.get("ProcessId"))
    ppid = _normalize_pid(proc_info.get("ParentProcessId"))
    role_state_pids = _extract_role_state_pids(role, state_pids)

    if role == "supervisor":
        return _is_owned_supervisor(cmd_norm, repo_root, state_dir)
    if role == "ssh":
        return _is_owned_ssh(cmd_norm, repo_root, state_dir, db_port)
    if role == "backend":
        return _is_owned_backend(pid, ppid, cmd_norm, exe_norm, repo_root, role_state_pids, proc_map)
    if role == "frontend":
        return _is_owned_frontend(cmd_norm, repo_root, frontend_port)
    return False


def _read_json_dict(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, str(exc)[:160])
    if type(doc) is not dict:
        return None, "Expected JSON object"
    return doc, None


def read_state_files(tunnel_path: Path, state_dir: Path | None = None) -> dict[str, Any]:
    """Read supervisor/backend/frontend state JSON files without side effects."""
    resolved_state_dir = state_dir if state_dir is not None else STATE
    tunnel_state_dir = tunnel_path.parent
    supervisor_path = tunnel_state_dir / SUPERVISOR_STATE_NAME
    backend_path = resolved_state_dir / BACKEND_STATE_NAME
    frontend_path = resolved_state_dir / FRONTEND_STATE_NAME
    stop_marker_path = tunnel_state_dir / STOP_MARKER_NAME

    warnings: list[dict[str, str]] = []
    role_pids: dict[str, set[int]] = {role: set() for role in STOP_ORDER}
    files: dict[str, dict[str, Any]] = {}

    sup_doc, sup_err = _read_json_dict(supervisor_path)
    if sup_err:
        warnings.append({"file": str(supervisor_path), "error": sup_err})
    if sup_doc:
        for key, role in (("supervisor_pid", "supervisor"), ("ssh_pid", "ssh")):
            pid = _normalize_pid(sup_doc.get(key))
            if pid is not None:
                role_pids[role].add(pid)
    files["supervisor"] = {"path": str(supervisor_path), "exists": supervisor_path.is_file()}

    be_doc, be_err = _read_json_dict(backend_path)
    if be_err:
        warnings.append({"file": str(backend_path), "error": be_err})
    if be_doc:
        pid = _normalize_pid(be_doc.get("pid"))
        if pid is not None:
            role_pids["backend"].add(pid)
    files["backend"] = {"path": str(backend_path), "exists": backend_path.is_file()}

    fe_doc, fe_err = _read_json_dict(frontend_path)
    if fe_err:
        warnings.append({"file": str(frontend_path), "error": fe_err})
    if fe_doc:
        pid = _normalize_pid(fe_doc.get("pid"))
        if pid is not None:
            role_pids["frontend"].add(pid)
    files["frontend"] = {"path": str(frontend_path), "exists": frontend_path.is_file()}

    return {
        "tunnel_state_dir": str(tunnel_state_dir),
        "supervisor_state_path": str(supervisor_path),
        "stop_marker_path": str(stop_marker_path),
        "stop_marker_exists": stop_marker_path.is_file(),
        "role_pids": {k: sorted(v) for k, v in role_pids.items()},
        "files": files,
        "warnings": warnings,
    }


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def query_windows_runtime(
    ports: Iterable[int],
    extra_pids: Iterable[int] = (),
    timeout_s: float = 15.0,
) -> dict[str, list[dict[str, Any]]]:
    """Query listening TCP sockets and Win32_Process metadata via UTF-8 PowerShell."""
    port_list = sorted({int(p) for p in ports if 1 <= int(p) <= 65535})
    pid_list = sorted({int(p) for p in extra_pids if _normalize_pid(p) is not None})
    ps_ports = "@(" + ", ".join(str(p) for p in port_list) + ")"
    ps_pids = "@(" + ", ".join(str(p) for p in pid_list) + ")"
    ps_script = "\n".join([
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)",
        f"$targetPorts = {ps_ports}",
        f"$extraPids = {ps_pids}",
        "$conns = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |",
        "    Where-Object { $targetPorts -contains [int]$_.LocalPort } |",
        "    Select-Object LocalAddress, @{N='LocalPort';E={[int]$_.LocalPort}}, @{N='OwningProcess';E={[int]$_.OwningProcess}})",
        "$allProcs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |",
        "    Select-Object @{N='ProcessId';E={[int]$_.ProcessId}}, @{N='ParentProcessId';E={[int]$_.ParentProcessId}}, Name, ExecutablePath, CommandLine)",
        "$seedPids = @($extraPids + @($conns | ForEach-Object { $_.OwningProcess })) | Sort-Object -Unique",
        "$directProcs = @($allProcs | Where-Object { $seedPids -contains $_.ProcessId })",
        "$parentPids = @($directProcs | ForEach-Object { $_.ParentProcessId } | Where-Object { $_ -gt 0 })",
        "$wantPids = @($seedPids + $parentPids) | Sort-Object -Unique",
        "$procs = @($allProcs | Where-Object { $wantPids -contains $_.ProcessId })",
        "@{ connections = $conns; processes = $procs } | ConvertTo-Json -Depth 4",
    ])
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StopError(
            "查询 Windows 进程或端口失败: %s" % type(exc).__name__,
            EXIT_STOP_FAILED,
            {"error": str(exc)[:300]},
        ) from None
    stdout_text = proc.stdout.decode("utf-8", "replace").strip()
    stderr_text = proc.stderr.decode("utf-8", "replace").strip()
    if proc.returncode != 0 or not stdout_text:
        raise StopError(
            "PowerShell 进程检查返回错误 (exit=%d)" % proc.returncode,
            EXIT_STOP_FAILED,
            {"returncode": proc.returncode, "stderr": stderr_text[:400]},
        )
    try:
        doc = json.loads(stdout_text)
    except ValueError as exc:
        raise StopError(
            "解析 PowerShell 进程输出失败: %s" % exc,
            EXIT_STOP_FAILED,
            {"sample": stdout_text[:200]},
        ) from None
    if type(doc) is not dict:
        raise StopError("PowerShell 进程输出格式无效", EXIT_STOP_FAILED)
    return {
        "connections": _as_dict_list(doc.get("connections")),
        "processes": _as_dict_list(doc.get("processes")),
    }


def _add_candidate(
    buckets: dict[str, dict[int, set[str]]],
    role: str,
    pid: Any,
    source: str,
) -> None:
    valid_pid = _normalize_pid(pid)
    if valid_pid is not None:
        buckets[role].setdefault(valid_pid, set()).add(source)


def _format_candidate_record(
    role: str,
    pid: int,
    sources: set[str],
    status: str,
    proc_info: Mapping[str, Any] | None,
    reason: str,
) -> dict[str, Any]:
    cmd = str((proc_info or {}).get("CommandLine") or "")
    if len(cmd) > 500:
        cmd = cmd[:497] + "..."
    return {
        "role": role,
        "pid": pid,
        "status": status,
        "sources": sorted(sources),
        "name": (proc_info or {}).get("Name"),
        "parent_pid": _normalize_pid((proc_info or {}).get("ParentProcessId")),
        "executable_path": (proc_info or {}).get("ExecutablePath"),
        "command_line": cmd or None,
        "reason": reason,
    }


def _sort_role_candidates(role: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order backend candidates parent-before-child so tree termination exits cleanly."""
    if role != "backend" or len(items) <= 1:
        return sorted(items, key=lambda x: x["pid"])
    owned_pids = {item["pid"] for item in items if item["status"] == "OWNED"}
    return sorted(
        items,
        key=lambda x: (
            1 if (x["status"] == "OWNED" and x.get("parent_pid") in owned_pids) else 0,
            x["pid"],
        ),
    )


def collect_candidates(
    settings: Mapping[str, Any],
    state_info: Mapping[str, Any],
    runtime: Mapping[str, Any],
    repo_root: Path | None = None,
    tunnel_state_dir: Path | None = None,
) -> dict[str, Any]:
    """Collect, deduplicate, and strictly verify candidate PIDs across all 4 roles."""
    resolved_repo_root = repo_root if repo_root is not None else REPO_ROOT
    db_port = int(settings["local_port"])
    backend_port = int(settings["backend_port"])
    state_dir_path = tunnel_state_dir or Path(state_info["tunnel_state_dir"])

    proc_map: dict[int, dict[str, Any]] = {}
    for proc in runtime.get("processes", ()):
        pid = _normalize_pid(proc.get("ProcessId"))
        if pid is not None:
            proc_map[pid] = dict(proc)

    port_to_role = {
        db_port: "ssh",
        backend_port: "backend",
        FRONTEND_PORT: "frontend",
    }
    port_listeners: dict[int, list[dict[str, Any]]] = {p: [] for p in port_to_role}
    buckets: dict[str, dict[int, set[str]]] = {role: {} for role in STOP_ORDER}

    role_state_map: dict[str, set[int]] = {}
    raw_role_pids = state_info.get("role_pids", {})
    source_name_by_role = {
        "supervisor": f"state:{SUPERVISOR_STATE_NAME}",
        "ssh": f"state:{SUPERVISOR_STATE_NAME}",
        "backend": f"state:{BACKEND_STATE_NAME}",
        "frontend": f"state:{FRONTEND_STATE_NAME}",
    }
    for role in STOP_ORDER:
        pids = {_normalize_pid(p) for p in raw_role_pids.get(role, ())}
        role_state_map[role] = {p for p in pids if p is not None}
        for pid in role_state_map[role]:
            _add_candidate(buckets, role, pid, source_name_by_role[role])

    for conn in runtime.get("connections", ()):
        port = conn.get("LocalPort")
        if port not in port_to_role:
            continue
        pid = _normalize_pid(conn.get("OwningProcess"))
        port_listeners[port].append({
            "local_address": conn.get("LocalAddress"),
            "local_port": port,
            "pid": pid,
        })
        if pid is not None:
            _add_candidate(buckets, port_to_role[port], pid, f"port:{port}")

    # Fallback: if an owned ssh process has a live owned tunnel_supervisor parent
    # not listed in tunnel-supervisor.json, include that parent so it stops first.
    for ssh_pid in list(buckets["ssh"]):
        ssh_proc = proc_map.get(ssh_pid)
        if not ssh_proc:
            continue
        if not is_owned_process(
            "ssh", ssh_proc, repo_root=resolved_repo_root, state_dir=state_dir_path,
            state_pids=role_state_map, db_port=db_port, frontend_port=FRONTEND_PORT,
            proc_map=proc_map,
        ):
            continue
        ppid = _normalize_pid(ssh_proc.get("ParentProcessId"))
        if ppid is not None and ppid not in buckets["supervisor"]:
            parent_proc = proc_map.get(ppid)
            if parent_proc and is_owned_process(
                "supervisor", parent_proc, repo_root=resolved_repo_root, state_dir=state_dir_path,
                state_pids=role_state_map, db_port=db_port, frontend_port=FRONTEND_PORT,
                proc_map=proc_map,
            ):
                _add_candidate(buckets, "supervisor", ppid, f"parent_of_ssh:{ssh_pid}")

    # Evaluate ownership first so we know which PIDs legitimately belong to each role.
    preliminary_owned: set[int] = set()
    eval_cache: dict[tuple[str, int], bool] = {}
    for role in STOP_ORDER:
        for pid in buckets[role]:
            proc = proc_map.get(pid)
            owned = bool(proc) and is_owned_process(
                role,
                proc,
                repo_root=resolved_repo_root,
                state_dir=state_dir_path,
                state_pids=role_state_map,
                db_port=db_port,
                frontend_port=FRONTEND_PORT,
                proc_map=proc_map,
            )
            eval_cache[(role, pid)] = owned
            if owned:
                preliminary_owned.add(pid)

    by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in STOP_ORDER}
    owned_by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in STOP_ORDER}
    skipped_unowned: list[dict[str, Any]] = []
    not_running: list[dict[str, Any]] = []

    for role in STOP_ORDER:
        items: list[dict[str, Any]] = []
        for pid, sources in buckets[role].items():
            proc = proc_map.get(pid)
            if proc is None:
                rec = _format_candidate_record(
                    role, pid, sources, "NOT_RUNNING", None,
                    "PID recorded in state file is not running",
                )
                not_running.append(rec)
                items.append(rec)
                continue
            if eval_cache[(role, pid)]:
                rec = _format_candidate_record(
                    role, pid, sources, "OWNED", proc,
                    "Matched strict ownership rules",
                )
                items.append(rec)
                continue
            # If a dead state PID was recycled by another owned role (not a port listener),
            # mark it stale rather than flagging a false unowned skip.
            if pid in preliminary_owned and not any(s.startswith("port:") for s in sources):
                rec = _format_candidate_record(
                    role, pid, sources, "NOT_RUNNING", proc,
                    "Stale state PID now belongs to another project role",
                )
                not_running.append(rec)
                items.append(rec)
                continue
            rec = _format_candidate_record(
                role, pid, sources, "SKIPPED_UNOWNED", proc,
                "Failed strict ownership verification; process left untouched",
            )
            skipped_unowned.append(rec)
            items.append(rec)
        sorted_items = _sort_role_candidates(role, items)
        by_role[role] = sorted_items
        owned_by_role[role] = [item for item in sorted_items if item["status"] == "OWNED"]

    return {
        "by_role": by_role,
        "owned_by_role": owned_by_role,
        "skipped_unowned": skipped_unowned,
        "not_running": not_running,
        "port_listeners": port_listeners,
    }


def is_pid_alive(pid: int) -> bool:
    """Return True if a Windows process PID is still running."""
    valid_pid = _normalize_pid(pid)
    if valid_pid is None or os.name != "nt":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(
        _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, valid_pid
    )
    if not handle:
        err = ctypes.get_last_error()
        if err == 87:  # ERROR_INVALID_PARAMETER -> no such process
            return False
        if err == 5:   # ERROR_ACCESS_DENIED -> exists, protected
            return True
        return False
    try:
        wait_res = kernel32.WaitForSingleObject(handle, 0)
        if wait_res == _WAIT_TIMEOUT:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return exit_code.value == _STILL_ACTIVE
            return True
        return False
    finally:
        kernel32.CloseHandle(handle)


def _run_taskkill(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=10.0)
    output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    return proc.returncode, output.strip()[:300]


def _wait_until_dead(pid: int, is_alive_fn, timeout_s: float, sleep_fn) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        if not is_alive_fn(pid):
            return True
        if time.monotonic() >= deadline:
            return False
        sleep_fn(min(0.15, max(0.01, deadline - time.monotonic())))


def terminate_pid(
    pid: int,
    is_alive_fn=None,
    run_cmd_fn=None,
    grace_timeout: float = DEFAULT_GRACE_TIMEOUT_S,
    force_timeout: float = DEFAULT_FORCE_TIMEOUT_S,
    sleep_fn=time.sleep,
) -> dict[str, Any]:
    """Terminate a verified project PID tree gracefully first, forcing /F only if needed."""
    alive = is_alive_fn or is_pid_alive
    runner = run_cmd_fn or _run_taskkill

    if not alive(pid):
        return {"pid": pid, "status": "ALREADY_EXITED", "detail": "Exited before termination"}

    grace_rc, grace_out = runner(["taskkill", "/PID", str(pid), "/T"])
    wait_window = grace_timeout if grace_rc == 0 else min(grace_timeout, 0.25)
    if _wait_until_dead(pid, alive, wait_window, sleep_fn):
        return {"pid": pid, "status": "STOPPED_GRACEFUL", "taskkill_rc": grace_rc}

    force_rc, force_out = runner(["taskkill", "/PID", str(pid), "/T", "/F"])
    if _wait_until_dead(pid, alive, force_timeout, sleep_fn):
        return {"pid": pid, "status": "STOPPED_FORCED", "taskkill_rc": force_rc}

    return {
        "pid": pid,
        "status": "FAILED_TO_STOP",
        "grace_rc": grace_rc,
        "grace_output": grace_out,
        "force_rc": force_rc,
        "force_output": force_out,
    }


def _default_port_checker(port: int) -> bool:
    return launch_frontend.tcp_open("127.0.0.1", int(port), timeout=0.4)


def _evaluate_ports(
    settings: Mapping[str, Any],
    skipped_unowned: list[dict[str, Any]],
    port_checker,
) -> dict[str, dict[str, Any]]:
    ports_map = {
        "db": int(settings["local_port"]),
        "backend": int(settings["backend_port"]),
        "frontend": FRONTEND_PORT,
    }
    unowned_ports: set[int] = set()
    for item in skipped_unowned:
        for src in item.get("sources", ()):
            if src.startswith("port:"):
                unowned_ports.add(int(src.split(":", 1)[1]))

    result: dict[str, dict[str, Any]] = {}
    for label, port in ports_map.items():
        listening = bool(port_checker(port))
        if not listening:
            status = "FREE"
        elif port in unowned_ports:
            status = "OCCUPIED_UNOWNED"
        else:
            status = "STILL_LISTENING"
        result[label] = {"port": port, "listening": listening, "status": status}
    return result


def build_check_report(
    settings: Mapping[str, Any],
    tunnel_path: Path,
    state_dir: Path | None = None,
    port_checker=None,
) -> dict[str, Any]:
    """Build offline prerequisite/state report for --check without side effects."""
    resolved_state_dir = state_dir if state_dir is not None else STATE
    checker = port_checker or _default_port_checker
    state_info = read_state_files(tunnel_path, state_dir=resolved_state_dir)
    ports_listening = {
        "db": bool(checker(settings["local_port"])),
        "backend": bool(checker(settings["backend_port"])),
        "frontend": bool(checker(FRONTEND_PORT)),
    }
    return {
        "result": "CHECK_OK",
        "settings": dict(settings),
        "stop_order": list(STOP_ORDER),
        "state_files": state_info["files"],
        "state_pids": state_info["role_pids"],
        "state_warnings": state_info["warnings"],
        "stop_marker_exists": state_info["stop_marker_exists"],
        "ports_listening": ports_listening,
        "network_calls": "NONE (offline parameter and loopback check; zero kills)",
        "side_effects": "NONE",
    }


def run_stop(
    settings: Mapping[str, Any],
    tunnel_path: Path,
    dry_run: bool = False,
    state_dir: Path | None = None,
    repo_root: Path | None = None,
    runtime_inspector=None,
    terminator=None,
    port_checker=None,
) -> tuple[int, dict[str, Any]]:
    """Inspect and optionally stop owned project services in strict STOP_ORDER."""
    resolved_state_dir = state_dir if state_dir is not None else STATE
    resolved_repo_root = repo_root if repo_root is not None else REPO_ROOT
    state_info = read_state_files(tunnel_path, state_dir=resolved_state_dir)
    target_ports = (
        int(settings["local_port"]),
        int(settings["backend_port"]),
        FRONTEND_PORT,
    )
    extra_pids = [
        pid
        for pids in state_info["role_pids"].values()
        for pid in pids
    ]
    inspector = runtime_inspector or query_windows_runtime
    runtime = inspector(target_ports, extra_pids)

    tunnel_state_dir = tunnel_path.parent
    collected = collect_candidates(
        settings,
        state_info,
        runtime,
        repo_root=resolved_repo_root,
        tunnel_state_dir=tunnel_state_dir,
    )
    checker = port_checker or _default_port_checker

    if dry_run:
        would_stop = [
            item
            for role in STOP_ORDER
            for item in collected["owned_by_role"][role]
        ]
        dry_checker = port_checker or (
            lambda p: bool(collected["port_listeners"].get(p))
        )
        ports_status = _evaluate_ports(settings, collected["skipped_unowned"], dry_checker)
        return EXIT_OK, {
            "result": "DRY_RUN",
            "stop_order": list(STOP_ORDER),
            "would_stop": would_stop,
            "skipped_unowned": collected["skipped_unowned"],
            "not_running": collected["not_running"],
            "ports": ports_status,
            "state_warnings": state_info["warnings"],
            "cleanup_plan": {
                "would_remove_tunnel_supervisor_json": (
                    Path(state_info["supervisor_state_path"]).is_file()
                    and not any(
                        item["role"] in ("supervisor", "ssh")
                        for item in collected["skipped_unowned"]
                    )
                ),
                "will_create_stop_marker": False,
            },
            "side_effects": "NONE (dry-run only; zero processes terminated)",
        }

    kill_fn = terminator or (lambda pid, role: terminate_pid(pid))
    stopped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for role in STOP_ORDER:
        for item in collected["owned_by_role"][role]:
            outcome = dict(kill_fn(item["pid"], role))
            entry = {
                "role": role,
                "pid": item["pid"],
                "sources": item["sources"],
                "name": item.get("name"),
                **outcome,
            }
            if outcome.get("status") == "FAILED_TO_STOP":
                failed.append(entry)
            else:
                stopped.append(entry)

    ports_status = _evaluate_ports(settings, collected["skipped_unowned"], checker)

    tunnel_failed = any(entry["role"] in ("supervisor", "ssh") for entry in failed)
    tunnel_unowned = any(
        item["role"] in ("supervisor", "ssh") for item in collected["skipped_unowned"]
    )
    tunnel_clean = (
        not tunnel_failed
        and not tunnel_unowned
        and not ports_status["db"]["listening"]
    )
    sup_state_path = Path(state_info["supervisor_state_path"])
    sup_removed = False
    if tunnel_clean and sup_state_path.is_file():
        sup_state_path.unlink(missing_ok=True)
        sup_removed = True

    owned_port_still_listening = any(
        info["status"] == "STILL_LISTENING" for info in ports_status.values()
    )

    if failed or owned_port_still_listening:
        result_code = "STOP_FAILED"
        exit_code = EXIT_STOP_FAILED
    elif not stopped and not collected["skipped_unowned"]:
        result_code = "NOTHING_RUNNING"
        exit_code = EXIT_OK
    elif collected["skipped_unowned"]:
        result_code = "STOPPED_WITH_UNOWNED_SKIPPED"
        exit_code = EXIT_OK
    else:
        result_code = "STOPPED"
        exit_code = EXIT_OK

    return exit_code, {
        "result": result_code,
        "stop_order": list(STOP_ORDER),
        "stopped": stopped,
        "skipped_unowned": collected["skipped_unowned"],
        "failed": failed,
        "not_running": collected["not_running"],
        "ports": ports_status,
        "state_warnings": state_info["warnings"],
        "tunnel_supervisor_json_removed": sup_removed,
        "stop_marker_created": False,
    }


def _ensure_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", None) or "").lower()
        if enc and enc not in ("utf-8", "utf8") and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv=None) -> int:
    _ensure_utf8_streams()
    parser = argparse.ArgumentParser(
        description="安全停止 MovieInsight 2.0（Ubuntu DB 隧道 -> 本机 Flask 后端 -> Vite 5174 前端）。",
        epilog="示例：--check 离线校验；--dry-run 仅枚举并输出停止计划，不结束任何进程。",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="离线校验配置与状态文件路径，零副作用、不结束任何进程",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="枚举候选 PID 与严格归属判定结果，但不结束任何进程",
    )
    parser.add_argument(
        "--tunnel-json", default=None,
        help="覆盖 db-tunnel.json 路径（同时定位同目录 tunnel-supervisor.json）",
    )
    parser.add_argument(
        "--backend-port", type=int, default=None,
        help="覆盖本机 Flask 后端端口（默认 8091 或 MI2_BACKEND_PORT）",
    )
    args = parser.parse_args(argv)

    tunnel_path = Path(args.tunnel_json) if args.tunnel_json else TUNNEL_JSON
    try:
        settings = launch_frontend.load_settings(
            tunnel_path,
            backend_port=args.backend_port,
        )
    except launch_frontend.LaunchError as exc:
        print(json.dumps({
            "result": "CONFIG_ERROR",
            "error": str(exc),
            "detail": exc.detail,
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return exc.code

    if args.check:
        report = build_check_report(settings, tunnel_path)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return EXIT_OK

    try:
        rc, report = run_stop(settings, tunnel_path, dry_run=args.dry_run)
    except StopError as exc:
        print(json.dumps({
            "result": "STOP_ERROR",
            "error": str(exc),
            "detail": exc.detail,
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return exc.code

    stream = sys.stdout if rc == EXIT_OK else sys.stderr
    print(json.dumps(report, ensure_ascii=False, indent=2), file=stream)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
