"""Supervise the actual frozen executable and reject stale/incomplete evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid

from launcher.self_test import REQUIRED_CHECKS, SCHEMA_VERSION, SCOPE, write_report


MAX_REPORT_BYTES = 1024 * 1024
MAX_LOG_BYTES = 64 * 1024


def validate_report(report: object, *, request_id: str, expected_commit: str) -> dict:
    if not isinstance(report, dict):
        raise ValueError("self-test report must be an object")
    if type(report.get("schema_version")) is not int or report["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unknown self-test report schema")
    if report.get("scope") != SCOPE or report.get("request_id") != request_id:
        raise ValueError("self-test report is from another scope or invocation")
    if report.get("frozen") is not True:
        raise ValueError("self-test did not run inside a frozen executable")
    if report.get("passed") is not True or report.get("status") != "passed":
        raise ValueError("self-test did not pass")
    checks = report.get("checks")
    if not isinstance(checks, list) or not all(isinstance(item, dict) for item in checks):
        raise ValueError("self-test checks are missing or malformed")
    if [item.get("name") for item in checks] != list(REQUIRED_CHECKS):
        raise ValueError("required self-test check coverage is incomplete or duplicated")
    if any(item.get("status") != "passed" for item in checks):
        raise ValueError("one or more required self-test checks did not pass")
    assets = checks[0].get("details")
    if not isinstance(assets, dict) or assets.get("source_commit") != expected_commit:
        raise ValueError("self-test native source differs from the checked-out source")
    return report


def _tail(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - MAX_LOG_BYTES))
            return stream.read(MAX_LOG_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _load_report(path: Path) -> object:
    with path.open("rb") as stream:
        data = stream.read(MAX_REPORT_BYTES + 1)
    if len(data) > MAX_REPORT_BYTES:
        raise ValueError("self-test report exceeds size limit")
    return json.loads(data.decode("utf-8"))


def run_smoke(executable: Path, output: Path, expected_commit: str,
              timeout_s: float = 120.0) -> dict:
    if not math.isfinite(timeout_s) or not 0 < timeout_s <= 600:
        raise ValueError("timeout must be finite and between 0 and 600 seconds")
    if re.fullmatch(r"[a-f0-9]{40}", expected_commit) is None:
        raise ValueError("expected commit must be a full lowercase Git SHA")
    executable = executable.resolve()
    nonce = uuid.uuid4().hex
    summary = {"schema_version": 1, "scope": SCOPE, "request_id": nonce,
               "expected_commit": expected_commit, "status": "running", "passed": False,
               "timeout_s": timeout_s}
    write_report(output, summary)  # invalidate any previous successful evidence
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="glassless-frozen-smoke-") as directory:
        workspace = Path(directory)
        report_path = workspace / "self-test.json"
        stdout_path, stderr_path = workspace / "stdout.log", workspace / "stderr.log"
        env = os.environ.copy()
        env.update(QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1",
                   APPDATA=str(workspace), LOCALAPPDATA=str(workspace),
                   MPLCONFIGDIR=str(workspace / "matplotlib"))
        # Exercise bundled discovery from an unrelated CWD without source-tree
        # PYTHONPATH or Python-home assistance from the developer workstation.
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        try:
            digest = hashlib.sha256()
            with executable.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            summary["executable_sha256"] = digest.hexdigest()
            args = [str(executable), "--self-test", "--output", str(report_path),
                    "--request-id", nonce, "--expected-commit", expected_commit]
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                result = subprocess.run(args, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                                        stdout=stdout, stderr=stderr, timeout=timeout_s, check=False)
            summary["exit_code"] = result.returncode
            if report_path.is_file():
                summary["self_test"] = _load_report(report_path)
            if result.returncode != 0:
                raise RuntimeError("frozen self-test exited with code " + str(result.returncode))
            validate_report(summary.get("self_test"), request_id=nonce, expected_commit=expected_commit)
            summary["passed"] = True
            summary["status"] = "passed"
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            summary["status"] = "failed"
            summary["error"] = type(error).__name__ + ": " + str(error)[:2000]
            summary["stdout_tail"] = _tail(stdout_path)
            summary["stderr_tail"] = _tail(stderr_path)
        summary["duration_ms"] = round((time.monotonic() - started) * 1000.0, 3)
        write_report(output, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded offline frozen-runtime integration")
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)
    report = run_smoke(args.executable, args.output, args.expected_commit, args.timeout)
    print("Frozen offline runtime:", "PASS" if report["passed"] else "FAIL")
    if not report["passed"]:
        print(report.get("error", "failed self-test"))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
