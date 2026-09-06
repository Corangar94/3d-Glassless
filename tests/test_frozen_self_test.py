"""Admission, failure and isolation contracts for real packaged startup probes."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

from launcher import self_test
from scripts import run_frozen_smoke as smoke

SHA = "a" * 40
NONCE = "b" * 32


def _valid_report(nonce=NONCE):
    return {"schema_version": 1, "scope": self_test.SCOPE, "request_id": nonce,
            "frozen": True, "passed": True, "status": "passed",
            "checks": [{"name": name, "status": "passed", "details":
                        {"source_commit": SHA} if index == 0 else {}}
                       for index, name in enumerate(self_test.REQUIRED_CHECKS)]}


def test_complete_matching_frozen_report_is_accepted():
    report = _valid_report()
    assert smoke.validate_report(report, request_id=NONCE, expected_commit=SHA) is report


@pytest.mark.parametrize("change", [
    {"schema_version": True}, {"schema_version": 2}, {"scope": "hardware"},
    {"request_id": "c" * 32}, {"frozen": False}, {"frozen": "true"},
    {"passed": 1}, {"status": "running"}, {"checks": []}, {"checks": [None]},
])
def test_report_rejects_wrong_invocation_schema_scope_or_status(change):
    report = _valid_report()
    report.update(change)
    with pytest.raises(ValueError):
        smoke.validate_report(report, request_id=NONCE, expected_commit=SHA)


@pytest.mark.parametrize("kind", ["omitted", "duplicate", "reordered", "failed", "source"])
def test_report_requires_all_check_names_once_and_the_expected_source(kind):
    report = deepcopy(_valid_report())
    if kind == "omitted": report["checks"].pop()
    if kind == "duplicate": report["checks"].append(report["checks"][-1])
    if kind == "reordered": report["checks"].reverse()
    if kind == "failed": report["checks"][-1]["status"] = "failed"
    if kind == "source": report["checks"][0]["details"]["source_commit"] = "c" * 40
    with pytest.raises(ValueError):
        smoke.validate_report(report, request_id=NONCE, expected_commit=SHA)


def test_failed_check_is_preserved_and_later_checks_do_not_run(tmp_path, monkeypatch):
    observed = []
    monkeypatch.setattr(self_test, "_runtime_assets", lambda *_: {"source_commit": SHA})

    def fail():
        observed.append("imports")
        raise RuntimeError("missing frozen dependency")

    monkeypatch.setattr(self_test, "_production_imports", fail)
    monkeypatch.setattr(self_test, "_shared_memory", lambda *_: observed.append("unsafe continuation"))
    output = tmp_path / "report.json"
    output.write_text(json.dumps(_valid_report()), encoding="utf-8")
    report = self_test.run_checks(tmp_path, tmp_path, output, NONCE, SHA)
    assert not report["passed"] and report["status"] == "failed"
    assert report["checks"][1]["error"] == "RuntimeError: missing frozen dependency"
    assert all(item["status"] == "not_run" for item in report["checks"][2:])
    assert observed == ["imports"]
    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_assets_are_not_loaded(tmp_path, monkeypatch):
    from launcher import native_provenance
    monkeypatch.setattr(native_provenance, "verify_native_build", lambda *_a, **_kw: {"source_commit": SHA})
    (tmp_path / "models").mkdir()
    (tmp_path / "models/face_landmarker.task").write_bytes(b"invalid model")
    with pytest.raises(ValueError, match="model integrity"):
        self_test._runtime_assets(tmp_path, SHA)


@pytest.mark.parametrize("failure", [None, "exit", "timeout", "missing", "stale", "partial", "oversized"])
def test_supervisor_bounds_child_and_fails_closed(tmp_path, monkeypatch, failure):
    exe = tmp_path / "a bundle with spaces" / "Glassless3D.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"test executable")
    output = tmp_path / "smoke.json"
    output.write_text('{"passed": true}', encoding="utf-8")
    observed = []

    def fake_run(args, **kwargs):
        observed.append((args, kwargs))
        assert kwargs["timeout"] == 7.0
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["cwd"] != exe.parent
        assert "PYTHONPATH" not in kwargs["env"]
        assert "PYTHONHOME" not in kwargs["env"]
        assert kwargs["env"]["QT_QPA_PLATFORM"] == "offscreen"
        assert json.loads(output.read_text(encoding="utf-8"))["passed"] is False
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        report = Path(args[args.index("--output") + 1])
        nonce = args[args.index("--request-id") + 1]
        if failure != "missing":
            payload = _valid_report(NONCE if failure == "stale" else nonce)
            if failure == "partial": payload["checks"].pop()
            if failure == "oversized": payload["padding"] = "x" * smoke.MAX_REPORT_BYTES
            report.write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(returncode=9 if failure == "exit" else 0)

    monkeypatch.setattr(smoke.subprocess, "run", fake_run)
    report = smoke.run_smoke(exe, output, SHA, timeout_s=7.0)
    assert report["passed"] is (failure is None)
    assert report["status"] == ("passed" if failure is None else "failed")
    assert len(observed) == 1
    assert not observed[0][1]["cwd"].exists()
    assert json.loads(output.read_text(encoding="utf-8")) == report


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 0, 601])
def test_invalid_deadlines_do_not_start_process(tmp_path, value):
    with pytest.raises(ValueError, match="timeout"):
        smoke.run_smoke(tmp_path / "missing.exe", tmp_path / "report.json", SHA, value)


def test_diagnostic_log_read_is_bounded(tmp_path):
    path = tmp_path / "log.txt"
    path.write_bytes(b"x" * (smoke.MAX_LOG_BYTES * 3))
    assert len(smoke._tail(path)) == smoke.MAX_LOG_BYTES


def test_dispatch_selects_self_test_without_starting_normal_launcher(monkeypatch):
    from launcher.__main__ import _select_main
    calls = []
    monkeypatch.setattr(self_test, "main", lambda argv: calls.append(argv) or 0)
    argv = ["Glassless3D.exe", "--self-test", "--output", "evidence.json"]
    with pytest.raises(SystemExit) as result:
        _select_main(argv)()
    assert result.value.code == 0
    assert calls == [["--output", "evidence.json"]]


def test_conflicting_mode_is_rejected_before_camera_dispatch():
    from launcher.__main__ import _select_main
    argv = ["Glassless3D.exe", "--self-test", "--tracker-child", "--output", "unused.json"]
    with pytest.raises(SystemExit) as result:
        _select_main(argv)()
    assert result.value.code == 2


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mappings")
def test_real_transport_round_trips_are_isolated():
    report = self_test._shared_memory(uuid.uuid4().hex)
    assert report["isolated"] is True
    assert len(report["channels"]) == 4


def test_real_opencv_fallback_assets_and_generated_input():
    report = self_test._opencv_inference()
    assert report["cascades_loaded"] is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher settings transport")
def test_actual_main_window_starts_renders_and_closes_offline(tmp_path):
    env = os.environ.copy()
    env.update(QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1", APPDATA=str(tmp_path), LOCALAPPDATA=str(tmp_path))
    script = (
        "import json,sys; from pathlib import Path; "
        "from launcher.self_test import _qt_main_window; "
        "print(json.dumps(_qt_main_window(Path(sys.argv[1]),sys.argv[2])))"
    )
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path), uuid.uuid4().hex],
                            env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["platform"] == "offscreen" and report["rendered"] is True
    assert report["runtime_children_started"] is False
