from pathlib import Path
from unittest.mock import MagicMock

from scripts import run_live_runtime_check
from launcher import diagnostics


def _report(tmp_path: Path, ready: bool) -> diagnostics.DiagnosticsReport:
    return diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "Glassless3DOverlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=tmp_path / "config.yaml",
        config_loaded=True,
        ready=ready,
        problems=[] if ready else ["fresh overlay runtime summary missing"],
    )


def test_run_live_runtime_check_starts_fake_tracker_settings_and_overlay(tmp_path, monkeypatch):
    calls = []
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    overlay = MagicMock()
    overlay.start.return_value = tmp_path / "Glassless3DOverlay.exe"
    overlay.poll_exit_code.return_value = None
    reports = [_report(tmp_path, False), _report(tmp_path, True)]

    monkeypatch.setattr(run_live_runtime_check, "OverlayProcess", lambda: overlay)
    monkeypatch.setattr(run_live_runtime_check.subprocess, "Popen", lambda args, **kwargs: calls.append(args) or fake_proc)
    monkeypatch.setattr(run_live_runtime_check, "_clear_overlay_log", lambda: None)
    monkeypatch.setattr(run_live_runtime_check.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(run_live_runtime_check.time, "monotonic", iter([0.0, 0.1, 0.2, 0.3]).__next__)
    monkeypatch.setattr(run_live_runtime_check.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: reports.pop(0))

    code = run_live_runtime_check.main(["--config", str(tmp_path / "config.yaml"), "--timeout", "5"])

    assert code == 0
    assert overlay.start.called
    assert any(args[1:] == ["scripts/run_settings_writer.py", "--config", str(tmp_path / "config.yaml")] for args in calls)
    assert any(args[1:3] == ["tests/fake_tracker.py", "--static"] for args in calls)
    overlay.stop.assert_called_once()
    assert fake_proc.terminate.call_count == 2


def test_run_live_runtime_check_returns_failure_when_runtime_never_ready(tmp_path, monkeypatch):
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    overlay = MagicMock()
    overlay.start.return_value = tmp_path / "Glassless3DOverlay.exe"
    overlay.poll_exit_code.return_value = None

    monkeypatch.setattr(run_live_runtime_check, "OverlayProcess", lambda: overlay)
    monkeypatch.setattr(run_live_runtime_check.subprocess, "Popen", lambda *args, **kwargs: fake_proc)
    monkeypatch.setattr(run_live_runtime_check, "_clear_overlay_log", lambda: None)
    monkeypatch.setattr(run_live_runtime_check.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(run_live_runtime_check.time, "monotonic", iter([0.0, 0.1, 2.0]).__next__)
    monkeypatch.setattr(
        run_live_runtime_check.diagnostics,
        "collect_diagnostics",
        lambda config, require_live_runtime=False: _report(tmp_path, False),
    )

    code = run_live_runtime_check.main(["--config", str(tmp_path / "config.yaml"), "--timeout", "1"])

    assert code == 1
    overlay.stop.assert_called_once()


def test_run_live_runtime_check_reports_overlay_exit_during_poll(tmp_path, monkeypatch, capsys):
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    overlay = MagicMock()
    overlay.start.return_value = tmp_path / "Glassless3DOverlay.exe"
    overlay.poll_exit_code.side_effect = [None, 7]

    monkeypatch.setattr(run_live_runtime_check, "OverlayProcess", lambda: overlay)
    monkeypatch.setattr(run_live_runtime_check.subprocess, "Popen", lambda *args, **kwargs: fake_proc)
    monkeypatch.setattr(run_live_runtime_check, "_clear_overlay_log", lambda: None)
    monkeypatch.setattr(run_live_runtime_check.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(run_live_runtime_check.time, "monotonic", iter([0.0, 0.1, 0.2, 0.3]).__next__)
    monkeypatch.setattr(
        run_live_runtime_check.diagnostics,
        "collect_diagnostics",
        lambda config, require_live_runtime=False: _report(tmp_path, False),
    )

    code = run_live_runtime_check.main(["--config", str(tmp_path / "config.yaml"), "--timeout", "5"])

    assert code == 1
    assert "overlay exited before runtime became ready (exit code 7)" in capsys.readouterr().err
    overlay.stop.assert_called_once()


def test_run_live_runtime_check_clears_previous_overlay_log(tmp_path, monkeypatch):
    log = tmp_path / "overlay.log"
    log.write_text("old Frame#60 backend=0", encoding="utf-8")
    monkeypatch.setattr(run_live_runtime_check, "_overlay_log_path", lambda: log)

    run_live_runtime_check._clear_overlay_log()

    assert not log.exists()
