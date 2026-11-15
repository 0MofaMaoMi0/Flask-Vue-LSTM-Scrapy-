"""Standard-library unittest runner with machine-readable per-check results."""
from __future__ import annotations

import argparse
import io
import json
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent


class JsonResult(unittest.TextTestResult):
    def __init__(self, stream, descriptions, verbosity):
        super().__init__(stream, descriptions, verbosity)
        self.checks = []

    def _record(self, test, passed, detail=None):
        self.checks.append({
            "name": test.id(),
            "pass": bool(passed),
            "detail": detail,
        })

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, True)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, False, self._exc_info_to_string(err, test).splitlines()[-1])

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, False, self._exc_info_to_string(err, test).splitlines()[-1])

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._record(test, False, "SKIPPED: %s" % reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._record(test, False, "UNEXPECTED_EXPECTED_FAILURE")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._record(test, False, "UNEXPECTED_SUCCESS")


def run_checks() -> dict:
    loader = unittest.TestLoader()
    suite = loader.discover(str(HERE / "tests"), top_level_dir=str(HERE.parent))
    stream = io.StringIO()
    runner = unittest.TextTestRunner(
        stream=stream, verbosity=0, resultclass=JsonResult)
    result = runner.run(suite)
    checks = result.checks
    passed = sum(1 for check in checks if check["pass"])
    failed = len(checks) - passed
    payload = {
        "status": "PASS" if failed == 0 and result.testsRun > 0 else "FAIL",
        "total": len(checks),
        "passed": passed,
        "failed": failed,
        "exit_code": 0 if failed == 0 and result.testsRun > 0 else 1,
        "checks": checks,
    }
    if failed:
        payload["raw_tail"] = stream.getvalue()[-4000:]
    return payload


def print_checks(payload):
    for check in payload["checks"]:
        print("%s %s%s" % (
            "PASS" if check["pass"] else "FAIL",
            check["name"],
            "" if check["pass"] else " :: %s" % check.get("detail")))
    print("CHECKS %d/%d PASS exit=%d" % (
        payload["passed"], payload["total"], payload["exit_code"]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run M10 evaluation unit checks.")
    parser.add_argument("--output", help="Optional JSON result path (non-C:).")
    args = parser.parse_args(argv)
    payload = run_checks()
    if args.output:
        output = pathlib.Path(args.output).resolve()
        if output.drive.upper() == "C:":
            raise SystemExit("refusing C: output")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    print_checks(payload)
    return payload["exit_code"]


if __name__ == "__main__":
    sys.exit(main())