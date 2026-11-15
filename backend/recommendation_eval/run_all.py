"""One reproducible command: unit checks + synthetic demo + code hashes."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import platform
import sys

from . import EVAL_SCHEMA_VERSION, METRIC_VERSION
from . import baseline_v1, boards_reference
from .demo import run_demo
from .run_checks import run_checks

HERE = pathlib.Path(__file__).resolve().parent
BACKEND = HERE.parent
SOURCE_RECOMMENDATION = BACKEND / "catalog" / "recommendation.py"
SOURCE_BOARDS = BACKEND / "catalog" / "recommendation_boards.py"


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package_files():
    files = []
    for path in sorted(HERE.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
            continue
        if path.name.endswith(".tmp"):
            continue
        relative = path.relative_to(BACKEND).as_posix()
        files.append({
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        })
    return files


def _package_digest(files):
    material = "".join("%s\0%s\n" % (item["path"], item["sha256"])
                       for item in files).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _source_status():
    recommendation_sha = _sha256(SOURCE_RECOMMENDATION) if SOURCE_RECOMMENDATION.is_file() else None
    boards_sha = _sha256(SOURCE_BOARDS) if SOURCE_BOARDS.is_file() else None
    return {
        "recommendation_path": "backend/catalog/recommendation.py",
        "recommendation_sha256_actual": recommendation_sha,
        "recommendation_matches_frozen": recommendation_sha == baseline_v1.SOURCE_SHA256,
        "boards_path": "backend/catalog/recommendation_boards.py",
        "boards_sha256_actual": boards_sha,
        "boards_matches_frozen": boards_sha == boards_reference.SOURCE_SHA256,
        "note": "基线不会追随后续源码改动；此处只报告冻结时与当前字节是否一致。",
    }


def _demo_summary(demo):
    engine = demo["engine_check"]
    metrics = {}
    for name, value in engine["metrics"].items():
        metrics[name] = value.get("value") if isinstance(value, dict) else value
    return {
        "status": demo["status"],
        "synthetic": True,
        "checks": {"passed": sum(1 for item in demo["checks"] if item["pass"]),
                   "total": len(demo["checks"])},
        "scenarios": engine["scenario_summary"]["total"],
        "by_split": engine["scenario_summary"]["by_split"],
        "label_eligible": engine["scenario_summary"]["label_eligible"],
        "metrics": metrics,
        "effectiveness_probe": demo["effectiveness_probe"],
        "warning": demo["warning"],
    }


def build_result(output_dir: pathlib.Path):
    checks = run_checks()
    demo = run_demo(collect_resources=True)
    files = _package_files()
    demo_path = output_dir / "demo-result.json"
    unit_path = output_dir / "unit-results.json"
    result_path = output_dir / "result.json"
    status = "PASS" if checks["status"] == "PASS" and demo["status"] == "PASS" else "FAIL"
    exit_code = 0 if status == "PASS" else 1
    unit_doc = dict(checks)
    unit_doc["exit_code"] = exit_code if checks["status"] == "PASS" else 1
    result = {
        "task": "M10 员工1：Baseline V1 与离线评估工具",
        "status": status,
        "exit_code": exit_code,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "schema_version": EVAL_SCHEMA_VERSION,
        "metric_version": METRIC_VERSION,
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.system(),
            "network": False,
            "database": False,
            "model_calls": 0,
        },
        "baseline": {
            "baseline_id": baseline_v1.BASELINE_ID,
            "source_path": baseline_v1.SOURCE_PATH,
            "source_sha256": baseline_v1.SOURCE_SHA256,
            "function_name": baseline_v1.FUNCTION_NAME,
            "function_lines": list(baseline_v1.FUNCTION_LINES),
            "function_sha256": baseline_v1.FUNCTION_SHA256,
            "license_status": baseline_v1.LICENSE_STATUS,
            "license_note": baseline_v1.LICENSE_NOTE,
            "scope_note": baseline_v1.SCOPE_NOTE,
            "not_covered": list(baseline_v1.NOT_COVERED),
            "parameters": baseline_v1.PARAMETERS,
        },
        "boards_reference": {
            "version": boards_reference.BOARDS_VERSION,
            "source_sha256": boards_reference.SOURCE_SHA256,
            "channels": list(boards_reference.CHANNEL_NAMES),
            "scope_note": boards_reference.SCOPE_NOTE,
            "not_covered_by_baseline": list(boards_reference.NOT_COVERED_BY_BASELINE),
        },
        "checks": {
            "total": checks["total"],
            "passed": checks["passed"],
            "failed": checks["failed"],
            "status": checks["status"],
            "results_file": "unit-results.json",
        },
        "demo": _demo_summary(demo),
        "code": {
            "package_sha256": _package_digest(files),
            "files": files,
        },
        "source_status": _source_status(),
        "side_effects": {
            "browser": False,
            "real_http": False,
            "database": False,
            "model_calls": 0,
            "product_source_modified": False,
            "writes": [
                "unit-results.json",
                "demo-result.json",
                "result.json",
            ],
            "temp_cache_required": False,
        },
        "unverified": [
            "未使用真实冻结电影快照/真实内部 movie_id",
            "未使用人工或历史留出标签，不能报告真实用户效果",
            "未做统计显著性、置信区间或线上因果归因",
            "Baseline V1 只冻结纯 recommend()，未覆盖 boards 的全部频道重排",
            "未接入 Flask/MySQL/HTTP/前端或真实用户会话",
            "未调用真实模型/BYOK，未验证供应商计费与超时",
            "未做浏览器布局、截图或真实交互验证",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(json.dumps(unit_doc, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    demo_path.write_text(json.dumps(demo, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return result, checks, demo


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run all M10 offline checks, demo and machine result.")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    output_dir = pathlib.Path(args.output_dir).resolve()
    if output_dir.drive.upper() == "C:":
        raise SystemExit("refusing C: output")
    result, checks, demo = build_result(output_dir)
    for check in checks["checks"]:
        print("%s %s%s" % (
            "PASS" if check["pass"] else "FAIL",
            check["name"],
            "" if check["pass"] else " :: %s" % check.get("detail")))
    for check in demo["checks"]:
        print("%s demo:%s" % ("PASS" if check["pass"] else "FAIL", check["check"]))
    print("RESULT %s checks=%d/%d demo=%s exit=%d" % (
        result["status"], result["checks"]["passed"], result["checks"]["total"],
        result["demo"]["status"], result["exit_code"]))
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())