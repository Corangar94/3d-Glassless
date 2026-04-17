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


def test_parse_overlay_summary_line_extracts_runtime_health():
    line = (
        "[15:26:00.371] Frame#3249300 acq[ok=3247868 timeout=1432 lost=0 other=0] "
        "shm[LIVE reads=3249300 changes=369 (2/s) ts=80350123] "
        "depth[total=116440 8Hz] head=(-1.09,0.97,58.62) rest=(0.63,-0.38) "
        "rel=(-1.72,1.35) wobble=0.00 strength=1.00 depth=30.00 hasFrame=1"
    )

    summary = diagnostics.parse_overlay_summary_line(line)

    assert summary is not None
    assert summary.frame_count == 3_249_300
    assert summary.shm_status == "LIVE"
    assert summary.shm_changes_per_sec == 2
    assert summary.depth_hz == 8
    assert summary.head_z_cm == pytest.approx(58.62)
    assert summary.has_frame is True


def test_collect_diagnostics_includes_overlay_log_warnings(tmp_path, monkeypatch):
    log_path = tmp_path / "overlay.log"
    log_path.write_text(
        "[15:25:55.013] Frame#120 acq[ok=118 timeout=2 lost=0 other=0] "
        "shm[STALE (tracker running but not writing?) reads=120 changes=3 (0/s) ts=10] "
        "depth[total=0 0Hz] head=(0.00,0.00,60.00) rest=(0.00,0.00) "
        "rel=(0.00,0.00) wobble=0.00 strength=1.00 depth=30.00 hasFrame=0\n"
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text("{}")
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: log_path)

    report = diagnostics.collect_diagnostics(config_path=cfg)

    assert report.overlay_summary is not None
    assert report.overlay_summary.shm_status.startswith("STALE")
    assert "overlay log reports stale tracker shared memory" in report.warnings
    assert "overlay log reports no captured frame" in report.warnings
