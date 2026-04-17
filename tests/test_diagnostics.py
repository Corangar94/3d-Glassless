from pathlib import Path

import pytest

from launcher import diagnostics


def test_collect_diagnostics_reports_overlay_assets(tmp_path, monkeypatch):
    exe = tmp_path / "Glassless3DOverlay.exe"
    model = tmp_path / "models" / "depth_anything_v2_small_fp16.onnx"
    model.parent.mkdir()
    exe.write_bytes(b"exe")
    model.write_bytes(b"model")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("camera:\n  index: 1\noverlay:\n  virtual_depth_cm: 30\n")

    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: exe)
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: model)

    report = diagnostics.collect_diagnostics(config_path=cfg)

    assert report.overlay_exe == exe
    assert report.depth_model == model
    assert report.config_path == cfg
    assert report.config_loaded is True
    assert report.ready is True
    assert report.problems == []


def test_collect_diagnostics_reports_missing_overlay_requirements(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: None)
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: None)

    report = diagnostics.collect_diagnostics(config_path=tmp_path / "missing.yaml")

    assert report.ready is False
    assert "overlay executable missing" in report.problems
    assert "depth model missing" in report.problems
    assert "config file missing" in report.problems


def test_format_diagnostics_report_includes_actionable_commands(tmp_path):
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=None,
        depth_model=None,
        config_path=tmp_path / "missing.yaml",
        config_loaded=False,
        ready=False,
        problems=["overlay executable missing"],
    )

    text = diagnostics.format_diagnostics_report(report)

    assert "Glassless3D Diagnostics" in text
    assert "overlay executable missing" in text
    assert "python scripts/bootstrap.py" in text
    assert "python -m tracker.debug_monitor" in text


def test_collect_diagnostics_marks_invalid_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("overlay: [")
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")

    report = diagnostics.collect_diagnostics(config_path=cfg)

    assert report.ready is False
    assert report.config_loaded is False
    assert any(p.startswith("config unreadable:") for p in report.problems)


def test_main_returns_nonzero_when_report_not_ready(tmp_path, monkeypatch, capsys):
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=None,
        depth_model=None,
        config_path=tmp_path / "missing.yaml",
        config_loaded=False,
        ready=False,
        problems=["overlay executable missing"],
    )
    monkeypatch.setattr(diagnostics, "collect_diagnostics", lambda _config: report)

    code = diagnostics.main(["--config", str(tmp_path / "missing.yaml")])

    assert code == 1
    assert "NOT READY" in capsys.readouterr().out
