#!/usr/bin/env python
"""Offline self-test for Windows local launcher stack; zero network calls."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
REPO_ROOT = SCRIPTS_DIR.parent
BACKEND_DIR = REPO_ROOT / "backend"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline canonical launcher checks.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    out = Path(args.output).resolve()
    if out.drive.upper() == "C:":
        raise SystemExit("refusing C: output")

    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))

    fe = _load_module("canonical_launch_frontend", SCRIPTS_DIR / "launch_frontend.py")
    be = _load_module("canonical_launch_backend", SCRIPTS_DIR / "launch_backend.py")
    ts = _load_module("canonical_tunnel_supervisor", SCRIPTS_DIR / "tunnel_supervisor.py")
    lp = _load_module("canonical_launch_project", SCRIPTS_DIR / "launch_project.py")
    sp = _load_module("canonical_stop_project", SCRIPTS_DIR / "stop_project.py")
    from app import m6_serve

    checks = []

    def check(name, condition, detail=None):
        checks.append({"check": name, "pass": bool(condition), "detail": detail})
        if not condition:
            raise AssertionError(name)

    # 1. Canonical repo & F: cache directory checks
    check("repo_root_derived_from_script", fe.REPO_ROOT.name == "项目源码", str(fe.REPO_ROOT))
    check("movie_web_is_repo_child", fe.MOVIE_WEB == fe.REPO_ROOT / "movie-web", str(fe.MOVIE_WEB))
    check("backend_dir_is_repo_child", be.BACKEND_DIR == be.REPO_ROOT / "backend", str(be.BACKEND_DIR))
    check("tunnel_json_is_db_tunnel",
          fe.TUNNEL_JSON.name == "db-tunnel.json"
          and be.TUNNEL_JSON == fe.TUNNEL_JSON
          and ts.TUNNEL_JSON == fe.TUNNEL_JSON,
          str(fe.TUNNEL_JSON))
    check("cache_root_is_repo_local", fe.CACHE_ROOT == fe.REPO_ROOT / ".local-cache" / "startup",
          str(fe.CACHE_ROOT))

    cache_paths = (
        fe.TMP, fe.LOGS, fe.STATE, fe.CACHE,
        be.MEDIA_CACHE, be.TRENDING_CACHE,
        be.BACKEND_CATALOG_CACHE, be.BACKEND_ADMIN_CACHE,
        be.BACKEND_REC_CACHE, be.BACKEND_STATE_DIR,
    )
    check("startup_and_cache_paths_are_distinct", len({str(p) for p in cache_paths}) == len(cache_paths))
    check("startup_paths_avoid_vite_cache", all("node_modules" not in str(p) for p in cache_paths))
    check("startup_paths_on_f_drive", all(Path(p).drive.upper() == "F:" for p in cache_paths))

    be.ensure_dirs()
    check("backend_cache_dirs_created", all(Path(p).is_dir() for p in cache_paths))

    with tempfile.TemporaryDirectory(dir=str(fe.TMP)) as tmp_dir:
        tmp = Path(tmp_dir)

        def settings_doc(doc, backend_port=None):
            path = tmp / "db-tunnel.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            return fe.load_settings(path, backend_port=backend_port)

        def rejects(doc, backend_port=None):
            try:
                settings_doc(doc, backend_port=backend_port)
            except fe.LaunchError:
                return True
            return False

        # 2. db-tunnel.json & port validation
        valid = fe.load_settings()
        check("real_db_tunnel_settings_load", valid["local_port"] == 3306,
              {"local_port": valid["local_port"]})
        check("remote_target_is_loopback_3306",
              valid["remote_host"] == "127.0.0.1" and valid["remote_port"] == 3306,
              valid["remote_target"])
        check("api_target_is_local_backend_8091",
              valid["backend_port"] == 8091 and valid["api_target"] == "http://127.0.0.1:8091",
              valid["api_target"])
        extra = settings_doc({
            "local_port": valid["local_port"],
            "remote_target": valid["remote_target"],
            "health": {},
            "pid": 1,
        })
        check("only_two_settings_keys_used",
              extra["ignored_keys"] == ["health", "pid"], extra["ignored_keys"])

        check("reject_readonly_1461", rejects({"local_port": 1461,
                                               "remote_target": "127.0.0.1:3306"}))
        check("reject_readonly_3575", rejects({"local_port": 3575,
                                               "remote_target": "127.0.0.1:3306"}))
        check("reject_frontend_port_as_tunnel", rejects({"local_port": 5174,
                                                         "remote_target": "127.0.0.1:3306"}))
        check("reject_tunnel_colliding_with_backend_8091",
              rejects({"local_port": 8091, "remote_target": "127.0.0.1:3306"}))
        check("reject_non_int_port", rejects({"local_port": "3306",
                                              "remote_target": "127.0.0.1:3306"}))
        check("reject_bool_port", rejects({"local_port": True,
                                           "remote_target": "127.0.0.1:3306"}))
        check("reject_port_out_of_range", rejects({"local_port": 70000,
                                                   "remote_target": "127.0.0.1:3306"}))
        check("reject_non_loopback_remote", rejects({"local_port": 3306,
                                                     "remote_target": "192.168.147.50:3306"}))
        check("reject_bad_remote_format", rejects({"local_port": 3306,
                                                   "remote_target": "127.0.0.1"}))
        check("reject_readonly_remote_port", rejects({"local_port": 3306,
                                                      "remote_target": "127.0.0.1:1461"}))
        check("reject_frontend_remote_port", rejects({"local_port": 3306,
                                                      "remote_target": "127.0.0.1:5174"}))

        for bad_be_port in (1461, 3575, 5174, 3306, 70000, True):
            check("reject_backend_port_%s" % bad_be_port,
                  rejects({"local_port": 3306, "remote_target": "127.0.0.1:3306"},
                          backend_port=bad_be_port))

        old_env_port = os.environ.get("MI2_BACKEND_PORT")
        try:
            os.environ["MI2_BACKEND_PORT"] = "8092"
            overridden = fe.load_settings()
            check("backend_port_env_override_8092",
                  overridden["backend_port"] == 8092
                  and overridden["api_target"] == "http://127.0.0.1:8092")
        finally:
            if old_env_port is None:
                os.environ.pop("MI2_BACKEND_PORT", None)
            else:
                os.environ["MI2_BACKEND_PORT"] = old_env_port

        # 3. tunnel_supervisor.py offline contract checks
        check("tunnel_supervisor_stop_marker", ts.STOP_MARKER.name == "stop-db-tunnel")
        check("tunnel_supervisor_error_log", ts.ERROR_LOG.name == "db-tunnel-errors.txt")
        ts_cfg = ts.load_tunnel_settings()
        check("tunnel_supervisor_loads_db_tunnel",
              ts_cfg["local_port"] == 3306 and ts_cfg["remote_target"] == "127.0.0.1:3306")
        ssh_args = ts.build_ssh_args(ts_cfg, ssh_exe="ssh.exe")
        check("tunnel_supervisor_ssh_pinned_and_forwarded",
              "StrictHostKeyChecking=yes" in ssh_args
              and "ExitOnForwardFailure=yes" in ssh_args
              and any(a.startswith("UserKnownHostsFile=") for a in ssh_args)
              and any(a.startswith("HostKeyAlias=") for a in ssh_args)
              and "127.0.0.1:3306:127.0.0.1:3306" in ssh_args
              and f"{ts_cfg['ssh_user']}@{ts_cfg['ssh_host']}" in ssh_args)

        # 4. launch_backend.py offline checks (mock env in tmp; NEVER touch 敏感信息调用/)
        secret_sentinel = "SYNTHETIC_SECRET_TOKEN_DO_NOT_LEAK_9911"
        mock_env = tmp / "backend.local.env"
        mock_env.write_text(
            "\n".join([
                "# synthetic test env",
                "MI2_M6_ISOLATED=true",
                f"MI2_DATABASE_URL='mysql+pymysql://mi2app:{secret_sentinel}@127.0.0.1:3306/mi2_m6_mock'",
                f'export MI2_M7_DATABASE_URL="mysql+pymysql://mi2m7:{secret_sentinel}#2@127.0.0.1:3306/mi2_m7_mock"',
                "MI2_PORT=5000",
                "MI2_HOST=0.0.0.0",
            ]),
            encoding="utf-8",
        )
        parsed_env = be.load_env_file(mock_env)
        check("backend_env_file_parses_cleanly",
              parsed_env["MI2_M6_ISOLATED"] == "true"
              and secret_sentinel in parsed_env["MI2_DATABASE_URL"]
              and parsed_env["MI2_M7_DATABASE_URL"].endswith("/mi2_m7_mock"))

        bad_env = tmp / "bad.env"
        bad_env.write_text(f"BAD LINE {secret_sentinel}\n", encoding="utf-8")
        bad_msg = ""
        try:
            be.load_env_file(bad_env)
        except be.LaunchError as exc:
            bad_msg = str(exc) + json.dumps(exc.detail)
        check("backend_bad_env_line_no_secret_in_error",
              bool(bad_msg) and secret_sentinel not in bad_msg)

        child_env = be.build_env(parsed_env, port=8091)
        check("backend_env_forces_loopback_8091",
              child_env["MI2_HOST"] == "127.0.0.1" and child_env["MI2_PORT"] == "8091")
        check("backend_env_forces_temp_and_bytecode",
              child_env["TEMP"] == str(be.TMP)
              and child_env["TMP"] == str(be.TMP)
              and child_env["TMPDIR"] == str(be.TMP)
              and child_env["PYTHONDONTWRITEBYTECODE"] == "1")
        check("backend_env_sets_f_drive_caches",
              all(Path(child_env[k]).drive.upper() == "F:"
                  for k in ("MI2_MEDIA_CACHE", "MI2_TRENDING_CACHE",
                            "MI2_CATALOG_CACHE_DIR", "MI2_ADMIN_CACHE_DIR",
                            "MI2_RECOMMENDATION_CACHE_DIR", "MI2_M6_STATE_DIR")))
        check("backend_env_passes_secret_to_child_only",
              secret_sentinel in child_env["MI2_DATABASE_URL"])

        be_plan = be.build_dry_run_plan(valid, env_path=mock_env)
        be_state = be.build_state_payload(4242, be.LOGS / "backend-test.log", valid, True, None)
        be_dump = json.dumps({"plan": be_plan, "state": be_state}, ensure_ascii=False)
        check("backend_no_secret_leak_in_plan_and_state",
              secret_sentinel not in be_dump and "mysql+pymysql" not in be_dump)
        check("backend_plan_has_no_c_drive",
              "C:" not in json.dumps(be_plan, ensure_ascii=False))

        be_cmd = be.build_command()
        check("backend_command_uses_auth_venv_m6_serve",
              Path(be_cmd[0]) == be.DEFAULT_PYTHON_EXE
              and be.DEFAULT_PYTHON_EXE.is_file()
              and be_cmd[1:] == ["-B", "-m", "app.m6_serve"]
              and be.M6_SERVE.is_file())

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            be_rc = be.main(["--check", "--env-file", str(mock_env)])
        check("backend_offline_check_passes_without_secrets",
              be_rc == 0
              and '"CHECK_OK"' in buf.getvalue()
              and secret_sentinel not in buf.getvalue())

        # 5. launch_frontend.py offline checks
        fe_env = fe.build_env(valid)
        check("node_cache_uses_single_canonical_directory", Path(fe_env["NODE_COMPILE_CACHE"]) == fe.CACHE)
        check("env_sets_mi2_api_target_8091", fe_env["MI2_API_TARGET"] == "http://127.0.0.1:8091")
        check("env_temp_in_startup_tree", Path(fe_env["TEMP"]).is_relative_to(fe.TMP), fe_env["TEMP"])
        check("env_cache_in_startup_tree",
              Path(fe_env["NODE_COMPILE_CACHE"]).is_relative_to(fe.CACHE),
              fe_env["NODE_COMPILE_CACHE"])
        check("env_removes_legacy_target",
              "VITE_API_TARGET" not in fe_env and "MI2_TUNNEL_PORT" not in fe_env)

        fe_cmd = fe.build_command()
        check("command_uses_existing_node", Path(fe_cmd[0]) == fe.NODE_EXE and fe.NODE_EXE.is_file())
        check("command_uses_existing_vite", Path(fe_cmd[1]) == fe.VITE_BIN and fe.VITE_BIN.is_file())
        check("command_binds_5174_loopback",
              fe_cmd[2:] == ["--host", "127.0.0.1", "--port", "5174", "--strictPort"])

        fe_plan = fe.describe(valid)
        fe_plan_text = json.dumps(fe_plan, ensure_ascii=False)
        check("no_c_drive_in_plan", all("C:" not in str(value) for value in fe_plan.values()))
        check("readonly_ports_declared_unused", "1461" in fe_plan["readonly_acceptance"])
        check("plan_has_no_stale_claims",
              "8091 不被使用" not in fe_plan_text and "http-tunnel.json" not in fe_plan_text)
        check("plan_logs_in_startup_tree", Path(fe_plan["logs"]).is_relative_to(fe.CACHE_ROOT))
        check("plan_temp_in_startup_tree", Path(fe_plan["temp"]).is_relative_to(fe.CACHE_ROOT))

        # 6. m6_serve.py bind validation
        check("m6_serve_default_bind_5000", m6_serve.resolve_bind({}) == ("127.0.0.1", 5000))
        check("m6_serve_custom_loopback_8091",
              m6_serve.resolve_bind({"MI2_HOST": "127.0.0.1", "MI2_PORT": "8091"})
              == ("127.0.0.1", 8091))

        def m6_rejects(env_map):
            try:
                m6_serve.resolve_bind(env_map)
            except RuntimeError:
                return True
            return False

        check("m6_serve_rejects_bad_binds",
              m6_rejects({"MI2_HOST": "0.0.0.0"})
              and m6_rejects({"MI2_HOST": "192.168.147.50"})
              and m6_rejects({"MI2_PORT": "0"})
              and m6_rejects({"MI2_PORT": "70000"})
              and m6_rejects({"MI2_PORT": "abc"}))

        # 7. launch_project.py offline orchestration check
        check("launch_project_uses_stop_db_tunnel", lp.STOP_MARKER_NAME == "stop-db-tunnel")
        calls = []
        orig_be_main = lp.launch_backend.main
        orig_fe_main = lp.launch_frontend.main
        try:
            lp.launch_backend.main = lambda a=None: calls.append(("backend", list(a or []))) or 0
            lp.launch_frontend.main = lambda a=None: calls.append(("frontend", list(a or []))) or 0
            lp_rc = lp.main(["--check", "--backend-port", "8091"])
        finally:
            lp.launch_backend.main = orig_be_main
            lp.launch_frontend.main = orig_fe_main
        check("launch_project_check_calls_both_launchers",
              lp_rc == 0
              and calls == [
                  ("backend", ["--check", "--port", "8091"]),
                  ("frontend", ["--check", "--backend-port", "8091"]),
              ],
              calls)

        # 8. stop_project.py & 停止项目.cmd offline checks
        stop_cmd_path = REPO_ROOT / "停止项目.cmd"
        stop_cmd_bytes = stop_cmd_path.read_bytes() if stop_cmd_path.is_file() else b""
        check("stop_cmd_exists_with_crlf",
              stop_cmd_path.is_file()
              and b"\r\n" in stop_cmd_bytes
              and b"\n" not in stop_cmd_bytes.replace(b"\r\n", b"")
              and b"stop_project.py" in stop_cmd_bytes)
        check("stop_project_stop_order",
              sp.STOP_ORDER == ("supervisor", "ssh", "backend", "frontend"))

        sb_root = tmp / "stop_sandbox" / "Flask-Vue-LSTM-scrapy"
        sb_repo = sb_root / "项目源码"
        sb_ubuntu = sb_root / "运行配置" / "Ubuntu"
        sb_state = tmp / "stop_sandbox_state"
        sb_ubuntu.mkdir(parents=True, exist_ok=True)
        sb_repo.mkdir(parents=True, exist_ok=True)
        sb_state.mkdir(parents=True, exist_ok=True)
        sb_tunnel_json = sb_ubuntu / "db-tunnel.json"
        sb_tunnel_json.write_text(json.dumps({
            "local_port": 3306,
            "remote_target": "127.0.0.1:3306",
        }), encoding="utf-8")

        sup_proc = {
            "ProcessId": 1001,
            "ParentProcessId": 500,
            "Name": "python.exe",
            "ExecutablePath": r"C:\Program Files\Python311\python.exe",
            "CommandLine": (
                f'"C:\\Program Files\\Python311\\python.exe" -B '
                f'{sb_repo}\\scripts\\tunnel_supervisor.py'
            ),
        }
        ssh_proc = {
            "ProcessId": 1002,
            "ParentProcessId": 1001,
            "Name": "ssh.exe",
            "ExecutablePath": r"C:\Windows\System32\OpenSSH\ssh.exe",
            "CommandLine": (
                f'C:\\Windows\\System32\\OpenSSH\\ssh.exe -F none '
                f'-i {sb_ubuntu}\\private\\m6_ed25519 '
                f'-o StrictHostKeyChecking=yes -N -L 127.0.0.1:3306:127.0.0.1:3306 user@host'
            ),
        }
        be_stub_proc = {
            "ProcessId": 1003,
            "ParentProcessId": 500,
            "Name": "python.exe",
            "ExecutablePath": f"{sb_repo}\\.local-cache\\auth-venv\\Scripts\\python.exe",
            "CommandLine": (
                f'{sb_repo}\\.local-cache\\auth-venv\\Scripts\\python.exe -B -m app.m6_serve'
            ),
        }
        be_child_proc = {
            "ProcessId": 1005,
            "ParentProcessId": 1003,
            "Name": "python.exe",
            "ExecutablePath": r"C:\Program Files\Python311\python.exe",
            "CommandLine": r'"C:\Program Files\Python311\python.exe" -B -m app.m6_serve',
        }
        fe_proc = {
            "ProcessId": 1004,
            "ParentProcessId": 500,
            "Name": "node.exe",
            "ExecutablePath": r"F:\Node\node.exe",
            "CommandLine": (
                f'F:\\Node\\node.exe {sb_repo}\\movie-web\\node_modules\\vite\\bin\\vite.js '
                f'--host 127.0.0.1 --port 5174 --strictPort'
            ),
        }

        unowned_python = {
            "ProcessId": 9001,
            "ParentProcessId": 500,
            "Name": "python.exe",
            "ExecutablePath": r"C:\Program Files\Python311\python.exe",
            "CommandLine": r'"C:\Program Files\Python311\python.exe" -m http.server 8091',
        }
        unowned_other_backend = {
            "ProcessId": 9004,
            "ParentProcessId": 501,
            "Name": "python.exe",
            "ExecutablePath": r"D:\other_proj\.venv\Scripts\python.exe",
            "CommandLine": r'D:\other_proj\.venv\Scripts\python.exe -B -m app.m6_serve',
        }
        unowned_node = {
            "ProcessId": 9002,
            "ParentProcessId": 500,
            "Name": "node.exe",
            "ExecutablePath": r"F:\Node\node.exe",
            "CommandLine": r'F:\Node\node.exe D:\other_app\server.js --port 5174',
        }
        unowned_other_vite = {
            "ProcessId": 9005,
            "ParentProcessId": 500,
            "Name": "node.exe",
            "ExecutablePath": r"F:\Node\node.exe",
            "CommandLine": (
                r'F:\Node\node.exe D:\other_app\node_modules\vite\bin\vite.js '
                r'--port 5174 --strictPort'
            ),
        }
        unowned_ssh = {
            "ProcessId": 9003,
            "ParentProcessId": 500,
            "Name": "ssh.exe",
            "ExecutablePath": r"C:\Windows\System32\OpenSSH\ssh.exe",
            "CommandLine": r'ssh.exe -N -L 127.0.0.1:3306:127.0.0.1:3306 ops@other-host',
        }
        unowned_wrong_port_ssh = {
            "ProcessId": 9006,
            "ParentProcessId": 500,
            "Name": "ssh.exe",
            "ExecutablePath": r"C:\Windows\System32\OpenSSH\ssh.exe",
            "CommandLine": (
                f'ssh.exe -i {sb_ubuntu}\\private\\m6_ed25519 '
                f'-N -L 127.0.0.1:33060:127.0.0.1:3306 user@host'
            ),
        }
        unowned_other_sup = {
            "ProcessId": 9007,
            "ParentProcessId": 500,
            "Name": "python.exe",
            "ExecutablePath": r"C:\Program Files\Python311\python.exe",
            "CommandLine": r'"C:\Program Files\Python311\python.exe" -B D:\other_proj\scripts\tunnel_supervisor.py',
        }
        stale_recycled_backend = {
            "ProcessId": 1003,
            "ParentProcessId": 500,
            "Name": "notepad.exe",
            "ExecutablePath": r"C:\Windows\System32\notepad.exe",
            "CommandLine": r'C:\Windows\System32\notepad.exe notes.txt',
        }
        be_proc_map = {1003: be_stub_proc, 1005: be_child_proc}

        check("stop_owned_positive_all_roles",
              sp.is_owned_process("supervisor", sup_proc, repo_root=sb_repo, state_dir=sb_ubuntu)
              and sp.is_owned_process("ssh", ssh_proc, repo_root=sb_repo, state_dir=sb_ubuntu, db_port=3306)
              and sp.is_owned_process("backend", be_stub_proc, repo_root=sb_repo, state_pids={1003})
              and sp.is_owned_process("backend", be_child_proc, repo_root=sb_repo, state_pids={1003}, proc_map=be_proc_map)
              and sp.is_owned_process("frontend", fe_proc, repo_root=sb_repo, frontend_port=5174))

        check("stop_owned_negative_rejects_unrelated_processes",
              not sp.is_owned_process("supervisor", unowned_python, repo_root=sb_repo, state_dir=sb_ubuntu)
              and not sp.is_owned_process("supervisor", unowned_other_sup, repo_root=sb_repo, state_dir=sb_ubuntu)
              and not sp.is_owned_process("ssh", unowned_ssh, repo_root=sb_repo, state_dir=sb_ubuntu, db_port=3306)
              and not sp.is_owned_process("ssh", unowned_wrong_port_ssh, repo_root=sb_repo, state_dir=sb_ubuntu, db_port=3306)
              and not sp.is_owned_process("backend", unowned_python, repo_root=sb_repo, state_pids={9001})
              and not sp.is_owned_process("backend", stale_recycled_backend, repo_root=sb_repo, state_pids={1003})
              and not sp.is_owned_process("backend", unowned_other_backend, repo_root=sb_repo, state_pids={1003})
              and not sp.is_owned_process("frontend", unowned_node, repo_root=sb_repo, frontend_port=5174)
              and not sp.is_owned_process("frontend", unowned_other_vite, repo_root=sb_repo, frontend_port=5174)
              and not sp.is_owned_process("frontend", fe_proc, repo_root=sb_repo, frontend_port=5175)
              and not sp.is_owned_process("frontend", {"ProcessId": 77, "CommandLine": ""}, repo_root=sb_repo))

        # Sandbox state files
        (sb_ubuntu / "tunnel-supervisor.json").write_text(
            json.dumps({"supervisor_pid": 1001, "ssh_pid": 1002, "attempt": 1, "local_port": 3306}),
            encoding="utf-8",
        )
        (sb_state / "backend-launch.json").write_text(json.dumps({"pid": 1003}), encoding="utf-8")
        (sb_state / "last-launch.json").write_text(json.dumps({"pid": 1004}), encoding="utf-8")

        sb_settings = fe.load_settings(sb_tunnel_json, backend_port=8091)
        check_report = sp.build_check_report(
            sb_settings, sb_tunnel_json, state_dir=sb_state, port_checker=lambda _p: False
        )
        check("stop_check_report_offline_ok",
              check_report["result"] == "CHECK_OK"
              and check_report["state_pids"]["supervisor"] == [1001]
              and check_report["state_pids"]["ssh"] == [1002]
              and check_report["state_pids"]["backend"] == [1003]
              and check_report["state_pids"]["frontend"] == [1004])

        # Reject forbidden ports in stop_project CLI
        bad_err = io.StringIO()
        with contextlib.redirect_stderr(bad_err):
            bad_stop_rc = sp.main(["--check", "--backend-port", "1461", "--tunnel-json", str(sb_tunnel_json)])
        check("stop_rejects_readonly_port_1461", bad_stop_rc == sp.EXIT_CONFIG)

        mock_runtime = {
            "connections": [
                {"LocalAddress": "127.0.0.1", "LocalPort": 3306, "OwningProcess": 1002},
                {"LocalAddress": "127.0.0.1", "LocalPort": 8091, "OwningProcess": 1005},
                {"LocalAddress": "127.0.0.1", "LocalPort": 8091, "OwningProcess": 9001},
                {"LocalAddress": "127.0.0.1", "LocalPort": 5174, "OwningProcess": 1004},
                {"LocalAddress": "127.0.0.1", "LocalPort": 5174, "OwningProcess": 9002},
            ],
            "processes": [
                sup_proc, ssh_proc, be_stub_proc, be_child_proc, fe_proc,
                unowned_python, unowned_node,
            ],
        }

        orig_state = sp.STATE
        orig_repo = sp.REPO_ROOT
        orig_inspector = sp.query_windows_runtime
        orig_terminator = sp.terminate_pid
        try:
            sp.STATE = sb_state
            sp.REPO_ROOT = sb_repo
            sp.query_windows_runtime = lambda ports, extra: mock_runtime
            dry_calls = []
            sp.terminate_pid = lambda pid, **kw: dry_calls.append(pid) or {"pid": pid, "status": "STOPPED_GRACEFUL"}

            dry_buf = io.StringIO()
            with contextlib.redirect_stdout(dry_buf):
                dry_rc = sp.main(["--dry-run", "--tunnel-json", str(sb_tunnel_json)])
            dry_doc = json.loads(dry_buf.getvalue())
            check("stop_dry_run_zero_kills_and_strict_order",
                  dry_rc == 0
                  and dry_doc["result"] == "DRY_RUN"
                  and dry_calls == []
                  and [(x["role"], x["pid"]) for x in dry_doc["would_stop"]] == [
                      ("supervisor", 1001),
                      ("ssh", 1002),
                      ("backend", 1003),
                      ("backend", 1005),
                      ("frontend", 1004),
                  ]
                  and {x["pid"] for x in dry_doc["skipped_unowned"]} == {9001, 9002}
                  and (sb_ubuntu / "tunnel-supervisor.json").is_file()
                  and not (sb_ubuntu / "stop-db-tunnel").exists())

            chk_buf = io.StringIO()
            with contextlib.redirect_stdout(chk_buf):
                chk_rc = sp.main(["--check", "--tunnel-json", str(sb_tunnel_json)])
            chk_doc = json.loads(chk_buf.getvalue())
            check("stop_cli_check_passes", chk_rc == 0 and chk_doc["result"] == "CHECK_OK")
        finally:
            sp.STATE = orig_state
            sp.REPO_ROOT = orig_repo
            sp.query_windows_runtime = orig_inspector
            sp.terminate_pid = orig_terminator

        # Mocked run_stop: strict kill order, unowned never killed, tunnel-supervisor.json cleaned, no stop marker
        clean_runtime = {
            "connections": [
                {"LocalAddress": "127.0.0.1", "LocalPort": 3306, "OwningProcess": 1002},
                {"LocalAddress": "127.0.0.1", "LocalPort": 8091, "OwningProcess": 1005},
                {"LocalAddress": "127.0.0.1", "LocalPort": 8091, "OwningProcess": 9001},
                {"LocalAddress": "127.0.0.1", "LocalPort": 5174, "OwningProcess": 1004},
                {"LocalAddress": "127.0.0.1", "LocalPort": 5174, "OwningProcess": 9002},
            ],
            "processes": [
                sup_proc, ssh_proc, be_stub_proc, be_child_proc, fe_proc,
                unowned_python, unowned_node,
            ],
        }
        kill_trace = []

        def fake_terminator(pid, role):
            kill_trace.append((role, pid))
            return {"pid": pid, "status": "STOPPED_GRACEFUL"}

        stop_rc, stop_doc = sp.run_stop(
            sb_settings,
            sb_tunnel_json,
            dry_run=False,
            state_dir=sb_state,
            repo_root=sb_repo,
            runtime_inspector=lambda ports, extra: clean_runtime,
            terminator=fake_terminator,
            port_checker=lambda port: port in (8091, 5174),
        )
        check("stop_run_strict_order_and_clean_tunnel",
              stop_rc == 0
              and stop_doc["result"] == "STOPPED_WITH_UNOWNED_SKIPPED"
              and kill_trace == [
                  ("supervisor", 1001),
                  ("ssh", 1002),
                  ("backend", 1003),
                  ("backend", 1005),
                  ("frontend", 1004),
              ]
              and all(pid not in {9001, 9002} for _, pid in kill_trace)
              and {x["pid"] for x in stop_doc["skipped_unowned"]} == {9001, 9002}
              and stop_doc["tunnel_supervisor_json_removed"] is True
              and not (sb_ubuntu / "tunnel-supervisor.json").exists()
              and stop_doc["stop_marker_created"] is False
              and not (sb_ubuntu / "stop-db-tunnel").exists())

        # Offline terminate_pid graceful + force fallback checks
        alive_state = {"alive": True}
        taskkill_cmds = []

        def fake_runner_force(cmd):
            taskkill_cmds.append(list(cmd))
            if "/F" in cmd:
                alive_state["alive"] = False
                return 0, "SUCCESS"
            return 128, "Only forceful termination supported"

        term_res = sp.terminate_pid(
            5555,
            is_alive_fn=lambda _p: alive_state["alive"],
            run_cmd_fn=fake_runner_force,
            grace_timeout=0.02,
            force_timeout=0.1,
            sleep_fn=lambda _s: None,
        )
        check("stop_terminate_pid_force_fallback",
              term_res["status"] == "STOPPED_FORCED"
              and taskkill_cmds == [
                  ["taskkill", "/PID", "5555", "/T"],
                  ["taskkill", "/PID", "5555", "/T", "/F"],
              ])

        grace_alive = {"alive": True}
        grace_cmds = []

        def fake_runner_grace(cmd):
            grace_cmds.append(list(cmd))
            grace_alive["alive"] = False
            return 0, "SUCCESS"

        grace_res = sp.terminate_pid(
            5556,
            is_alive_fn=lambda _p: grace_alive["alive"],
            run_cmd_fn=fake_runner_grace,
            grace_timeout=0.05,
            sleep_fn=lambda _s: None,
        )
        dead_res = sp.terminate_pid(
            5557,
            is_alive_fn=lambda _p: False,
            run_cmd_fn=lambda _c: (0, ""),
        )
        check("stop_terminate_pid_graceful_and_already_exited",
              grace_res["status"] == "STOPPED_GRACEFUL"
              and grace_cmds == [["taskkill", "/PID", "5556", "/T"]]
              and dead_res["status"] == "ALREADY_EXITED")

        # Unowned SSH + parent_of_ssh fallback + FAILED_TO_STOP handling
        (sb_ubuntu / "tunnel-supervisor.json").write_text(
            json.dumps({"ssh_pid": 1002}), encoding="utf-8"
        )
        unowned_ssh_runtime = {
            "connections": [
                {"LocalAddress": "127.0.0.1", "LocalPort": 3306, "OwningProcess": 9003},
            ],
            "processes": [sup_proc, ssh_proc, unowned_ssh],
        }
        ssh_fallback_trace = []
        unowned_ssh_rc, unowned_ssh_doc = sp.run_stop(
            sb_settings,
            sb_tunnel_json,
            dry_run=False,
            state_dir=sb_state,
            repo_root=sb_repo,
            runtime_inspector=lambda ports, extra: unowned_ssh_runtime,
            terminator=lambda pid, role: ssh_fallback_trace.append((role, pid)) or {"pid": pid, "status": "STOPPED_GRACEFUL"},
            port_checker=lambda port: port == 3306,
        )
        check("stop_unowned_ssh_skipped_and_parent_supervisor_found",
              unowned_ssh_rc == 0
              and ssh_fallback_trace == [("supervisor", 1001), ("ssh", 1002)]
              and [x["pid"] for x in unowned_ssh_doc["skipped_unowned"]] == [9003]
              and unowned_ssh_doc["tunnel_supervisor_json_removed"] is False
              and (sb_ubuntu / "tunnel-supervisor.json").is_file()
              and not (sb_ubuntu / "stop-db-tunnel").exists())

        (sb_ubuntu / "tunnel-supervisor.json").write_text(
            json.dumps({"supervisor_pid": 1001}), encoding="utf-8"
        )
        fail_rc, fail_doc = sp.run_stop(
            sb_settings,
            sb_tunnel_json,
            dry_run=False,
            state_dir=sb_state,
            repo_root=sb_repo,
            runtime_inspector=lambda ports, extra: {"connections": [], "processes": [sup_proc]},
            terminator=lambda pid, role: {"pid": pid, "status": "FAILED_TO_STOP"},
            port_checker=lambda _p: False,
        )
        check("stop_failed_to_stop_returns_nonzero",
              fail_rc == sp.EXIT_STOP_FAILED
              and fail_doc["result"] == "STOP_FAILED"
              and len(fail_doc["failed"]) == 1)

    result = {
        "selftest": "Windows local backend + DB tunnel launcher offline checks",
        "network_calls": "NONE",
        "checks": checks,
        "passed": sum(1 for item in checks if item["pass"]),
        "total": len(checks),
        "status": "PASS" if all(item["pass"] for item in checks) else "FAIL",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for item in checks:
        print(("PASS " if item["pass"] else "FAIL ") + item["check"])
    print("SELFTEST %s (%d/%d)" % (result["status"], result["passed"], result["total"]))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
