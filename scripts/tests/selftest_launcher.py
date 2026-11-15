#!/usr/bin/env python
"""Offline self-test for scripts/launch_frontend.py; zero network calls."""
from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAUNCHER = HERE.parent / "launch_frontend.py"


def load_module():
    spec = importlib.util.spec_from_file_location("canonical_launch_frontend", LAUNCHER)
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

    m = load_module()
    checks = []

    def check(name, condition, detail=None):
        checks.append({"check": name, "pass": bool(condition), "detail": detail})
        if not condition:
            raise AssertionError(name)

    # Standalone path derivation: never import the frozen M10 evidence package.
    check("repo_root_derived_from_script", m.REPO_ROOT.name == "项目源码", str(m.REPO_ROOT))
    check("movie_web_is_repo_child", m.MOVIE_WEB == m.REPO_ROOT / "movie-web", str(m.MOVIE_WEB))
    check("cache_root_is_repo_local", m.CACHE_ROOT == m.REPO_ROOT / ".local-cache" / "startup",
          str(m.CACHE_ROOT))
    check("startup_paths_are_distinct",
          len({str(m.TMP), str(m.LOGS), str(m.STATE), str(m.CACHE)}) == 4)
    check("startup_paths_avoid_vite_cache",
          all("node_modules" not in str(p) for p in (m.TMP, m.LOGS, m.STATE, m.CACHE)))
    check("startup_paths_on_f_drive", all(Path(p).drive.upper() == "F:" for p in
                                           (m.TMP, m.LOGS, m.STATE, m.CACHE)))

    m.TMP.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(m.TMP)) as tmp:
        tmp = Path(tmp)

        def settings_doc(doc):
            path = tmp / "tunnel.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            return m.load_settings(path)

        def rejects(doc):
            try:
                settings_doc(doc)
            except m.LaunchError:
                return True
            return False

        valid = m.load_settings()
        check("real_tunnel_settings_load", valid["local_port"] == 3629,
              {"local_port": valid["local_port"]})
        check("api_target_is_loopback_tunnel_port",
              valid["api_target"] == "http://127.0.0.1:3629")
        check("only_two_settings_keys_used",
              valid["ignored_keys"] == ["health", "pid"], valid["ignored_keys"])
        check("remote_target_is_loopback", valid["remote_host"] == "127.0.0.1")
        check("remote_port_is_real", valid["remote_port"] == 5000)

        check("reject_readonly_1461", rejects({"local_port": 1461,
                                               "remote_target": "127.0.0.1:5000"}))
        check("reject_readonly_3575", rejects({"local_port": 3575,
                                               "remote_target": "127.0.0.1:5000"}))
        check("reject_old_fallback_8091", rejects({"local_port": 8091,
                                                   "remote_target": "127.0.0.1:5000"}))
        check("reject_frontend_port_as_backend", rejects({"local_port": 5174,
                                                          "remote_target": "127.0.0.1:5000"}))
        check("reject_non_int_port", rejects({"local_port": "3629",
                                              "remote_target": "127.0.0.1:5000"}))
        check("reject_port_out_of_range", rejects({"local_port": 70000,
                                                   "remote_target": "127.0.0.1:5000"}))
        check("reject_non_loopback_remote", rejects({"local_port": 3629,
                                                     "remote_target": "192.168.147.50:5000"}))
        check("reject_bad_remote_format", rejects({"local_port": 3629,
                                                   "remote_target": "127.0.0.1"}))
        check("reject_readonly_remote_port", rejects({"local_port": 3629,
                                                      "remote_target": "127.0.0.1:1461"}))

        env = m.build_env(valid)
        check("node_cache_uses_single_canonical_directory", Path(env["NODE_COMPILE_CACHE"]) == m.CACHE)
        check("env_sets_mi2_api_target", env["MI2_API_TARGET"] == "http://127.0.0.1:3629")
        check("env_temp_in_startup_tree", Path(env["TEMP"]).is_relative_to(m.TMP),
              env["TEMP"])
        check("env_cache_in_startup_tree",
              Path(env["NODE_COMPILE_CACHE"]).is_relative_to(m.CACHE),
              env["NODE_COMPILE_CACHE"])
        check("env_removes_legacy_target", "VITE_API_TARGET" not in env)

        command = m.build_command()
        check("command_uses_existing_node", Path(command[0]) == m.NODE_EXE
              and m.NODE_EXE.is_file())
        check("command_uses_existing_vite", Path(command[1]) == m.VITE_BIN
              and m.VITE_BIN.is_file())
        check("command_binds_5174_loopback",
              command[2:] == ["--host", "127.0.0.1", "--port", "5174", "--strictPort"])

        plan = m.describe(valid)
        check("no_c_drive_in_plan", all("C:" not in str(value) for value in plan.values()))
        check("readonly_ports_declared_unused", "1461" in plan["readonly_acceptance"])
        check("old_fallback_declared_unused", "8091" in plan["old_fallback"])
        check("plan_logs_in_startup_tree", Path(plan["logs"]).is_relative_to(m.CACHE_ROOT))
        check("plan_temp_in_startup_tree", Path(plan["temp"]).is_relative_to(m.CACHE_ROOT))

    result = {
        "selftest": "scripts/launch_frontend.py offline checks",
        "network_calls": "NONE",
        "checks": checks,
        "passed": sum(1 for item in checks if item["pass"]),
        "total": len(checks),
        "status": "PASS" if all(item["pass"] for item in checks) else "FAIL",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    for item in checks:
        print(("PASS " if item["pass"] else "FAIL ") + item["check"])
    print("SELFTEST %s (%d/%d)" % (result["status"], result["passed"], result["total"]))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
