from pathlib import Path
import json
import time

import pytest

from launcher import diagnostics


def _stub_profile_diagnostics_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: None)
    monkeypatch.setattr(
        diagnostics,
        "_probe_camera",
        lambda index: diagnostics.CameraProbe(index=index, opened=True, frame_ok=True),
    )
    monkeypatch.setattr(diagnostics, "_collect_display_inventory", lambda: [])


def test_diagnostics_reports_profile_downgrade(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text(
        "active_game_profile: arena\ngame_profiles:\n  arena:\n"
        "    display_name: Arena\n    executable_path: C:/Arena.exe\n"
        "    play_context: online_multiplayer\n    requested_mode: offline_advanced\n"
        "    advanced_acknowledged: true\n",
        encoding="utf-8",
    )
    _stub_profile_diagnostics_environment(monkeypatch, tmp_path)

    report = diagnostics.collect_diagnostics(config)

    assert report.active_profile_id == "arena"
    assert report.requested_profile_mode == "offline_advanced"
    assert report.active_profile_mode == "non_injecting_desktop"
    assert report.profile_reason == "online profiles permit non-injecting desktop only"
    assert '"active_profile_id": "arena"' in diagnostics.format_diagnostics_json(report)
    assert "Active game profile: arena" in diagnostics.format_diagnostics_report(report)


def test_diagnostics_reports_malformed_profile_config_without_crashing(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("game_profiles: [unterminated\n", encoding="utf-8")
    _stub_profile_diagnostics_environment(monkeypatch, tmp_path)

    report = diagnostics.collect_diagnostics(config)

    assert report.active_profile_id is None
    assert any("profile configuration" in problem for problem in report.problems)


def test_diagnostics_explains_unavailable_capture_from_overlay_summary(tmp_path, monkeypatch):
    log_path = tmp_path / "overlay.log"
    log_path.write_text(
        "[15:26:00.371] Frame#300 acq[ok=300 timeout=0 lost=0 other=0] "
        "shm[LIVE reads=300 changes=200 (34/s) ts=123] "
        "depth[total=17 5Hz mode=balanced] head=(0.00,0.00,60.00) "
        "rest=(0.00,0.00) rel=(0.00,0.00) wobble=0.00 strength=1.00 "
        "depth=30.00 hasFrame=0 capture=unavailable capture_reason=target_spans_output\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text("camera:\n  index: 0\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: log_path)
    monkeypatch.setattr(
        diagnostics,
        "_probe_camera",
        lambda index: diagnostics.CameraProbe(index=index, opened=True, frame_ok=True),
    )
    monkeypatch.setattr(diagnostics, "_collect_display_inventory", lambda: [])

    report = diagnostics.collect_diagnostics(config_path=cfg)

    assert report.ready is False
    assert report.overlay_summary is not None
    assert report.overlay_summary.capture_state == "unavailable"
    assert report.overlay_summary.capture_reason == "target_spans_output"
    assert "move it fully onto one display" in diagnostics.format_diagnostics_report(report)
    assert '"capture_reason": "target_spans_output"' in diagnostics.format_diagnostics_json(report)


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
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: None)
    monkeypatch.setattr(
        diagnostics,
        "_probe_camera",
        lambda index: diagnostics.CameraProbe(index=index, opened=True, frame_ok=True, width=640, height=480),
    )
    monkeypatch.setattr(
        diagnostics,
        "_collect_display_inventory",
        lambda: [
            diagnostics.DisplayInventoryItem(
                name="Generic PnP Monitor",
                device_id="DISPLAY\\SAM71AC",
                manufacturer="SAM",
                product_code="71AC",
                width_px=5120,
                height_px=1440,
                primary=True,
            )
        ],
    )

    report = diagnostics.collect_diagnostics(config_path=cfg)

    assert report.overlay_exe == exe
    assert report.depth_model == model
    assert report.config_path == cfg
    assert report.config_loaded is True
    assert report.ready is True
    assert report.problems == []
    assert report.default_backend_id == "desktop_overlay"
    assert report.runtime_backend_id is None
    assert "stereo_autostereo" in report.experimental_backend_ids
    assert report.display_inventory[0].manufacturer == "SAM"


def test_collect_diagnostics_reports_camera_stream_status(tmp_path, monkeypatch):
    exe = tmp_path / "Glassless3DOverlay.exe"
    model = tmp_path / "models" / "depth_anything_v2_small_fp16.onnx"
    model.parent.mkdir()
    exe.write_bytes(b"exe")
    model.write_bytes(b"model")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("camera:\n  index: 2\n", encoding="utf-8")

    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: exe)
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: model)
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: None)
    monkeypatch.setattr(
        diagnostics,
        "_probe_camera",
        lambda index: diagnostics.CameraProbe(index=index, opened=True, frame_ok=True, width=640, height=480),
    )

    report = diagnostics.collect_diagnostics(config_path=cfg)

    assert report.camera == diagnostics.CameraProbe(
        index=2,
        opened=True,
        frame_ok=True,
        width=640,
        height=480,
    )
    assert report.ready is True


def test_probe_camera_falls_back_when_directshow_cannot_open(monkeypatch):
    calls = []

    class FakeCapture:
        def __init__(self, index, backend):
            self.index = index
            self.backend = backend
            calls.append((index, backend))

        def isOpened(self):
            return self.backend == diagnostics.cv2.CAP_MSMF

        def read(self):
            class Frame:
                shape = (480, 640, 3)

            return True, Frame()

        def release(self):
            pass

    monkeypatch.setattr(diagnostics.cv2, "VideoCapture", FakeCapture)

    probe = diagnostics._probe_camera(3)

    assert calls == [
        (3, diagnostics.cv2.CAP_DSHOW),
        (3, diagnostics.cv2.CAP_MSMF),
    ]
    assert probe == diagnostics.CameraProbe(
        index=3,
        opened=True,
        frame_ok=True,
        width=640,
        height=480,
    )


def test_collect_diagnostics_marks_unstreamable_camera_not_ready(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("camera:\n  index: 4\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: None)
    monkeypatch.setattr(
        diagnostics,
        "_probe_camera",
        lambda index: diagnostics.CameraProbe(index=index, opened=True, frame_ok=False),
    )

    report = diagnostics.collect_diagnostics(config_path=cfg)

    assert report.ready is False
    assert "camera 4 opened but returned no frames" in report.problems


def test_collect_diagnostics_uses_live_tracker_instead_of_camera_probe(tmp_path, monkeypatch):
    log_path = tmp_path / "overlay.log"
    log_path.write_text(
        "[15:25:55.013] Frame#120 acq[ok=118 timeout=2 lost=0 other=0] "
        "shm[LIVE reads=120 changes=9 (3/s) ts=10] "
        "depth[total=18 6Hz] head=(0.00,0.00,54.28) rest=(0.00,0.00) "
        "rel=(0.00,0.00) wobble=0.00 strength=1.00 depth=30.00 hasFrame=1\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text("camera:\n  index: 0\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: log_path)
    monkeypatch.setattr(
        diagnostics,
        "_probe_camera",
        lambda index: diagnostics.CameraProbe(index=index, opened=True, frame_ok=False),
    )

    report = diagnostics.collect_diagnostics(config_path=cfg)

    assert report.ready is True
    assert "camera 0 opened but returned no frames" not in report.problems
    assert "camera probe returned no frames while tracker shared memory is live" not in report.warnings
    assert report.camera is not None
    assert report.camera.inferred_from_tracker is True


def test_collect_diagnostics_marks_stale_tracker_not_ready(tmp_path, monkeypatch):
    log_path = tmp_path / "overlay.log"
    log_path.write_text(
        "[15:25:55.013] Frame#120 acq[ok=118 timeout=2 lost=0 other=0] "
        "shm[STALE (tracker running but not writing?) reads=120 changes=9 (0/s) ts=10] "
        "depth[total=18 6Hz] head=(0.00,0.00,54.28) rest=(0.00,0.00) "
        "rel=(0.00,0.00) wobble=0.00 strength=1.00 depth=30.00 hasFrame=1\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text("camera:\n  index: 0\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: log_path)
    monkeypatch.setattr(
        diagnostics,
        "_probe_camera",
        lambda index: diagnostics.CameraProbe(index=index, opened=True, frame_ok=True),
    )

    report = diagnostics.collect_diagnostics(config_path=cfg)

    assert report.ready is False
    assert "tracker shared memory stale" in report.problems


def test_collect_diagnostics_ignores_stale_overlay_log_for_readiness(tmp_path, monkeypatch):
    log_path = tmp_path / "overlay.log"
    log_path.write_text(
        "[15:25:55.013] Frame#120 acq[ok=118 timeout=2 lost=0 other=0] "
        "shm[STALE (tracker running but not writing?) reads=120 changes=9 (0/s) ts=10] "
        "depth[total=18 1Hz mode=fast] head=(0.00,0.00,54.28) rest=(0.00,0.00) "
        "rel=(0.00,0.00) wobble=0.00 strength=1.00 depth=30.00 hasFrame=1\n",
        encoding="utf-8",
    )
    old = time.time() - diagnostics._OVERLAY_LOG_FRESH_SECONDS - 5
    import os

    os.utime(log_path, (old, old))
    cfg = tmp_path / "config.yaml"
    cfg.write_text("camera:\n  index: 0\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: log_path)
    monkeypatch.setattr(
        diagnostics,
        "_probe_camera",
        lambda index: diagnostics.CameraProbe(index=index, opened=True, frame_ok=True),
    )

    report = diagnostics.collect_diagnostics(config_path=cfg)

    assert report.ready is True
    assert "tracker shared memory stale" not in report.problems
    assert "depth inference too slow: 1Hz" not in report.problems
    assert report.overlay_summary is None
    assert "overlay log is not fresh; skipping runtime health checks" in report.warnings


def test_collect_diagnostics_can_require_fresh_runtime_summary(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("camera:\n  index: 0\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: None)
    monkeypatch.setattr(
        diagnostics,
        "_probe_camera",
        lambda index: diagnostics.CameraProbe(index=index, opened=True, frame_ok=True),
    )

    report = diagnostics.collect_diagnostics(config_path=cfg, require_live_runtime=True)

    assert report.ready is False
    assert "fresh overlay runtime summary missing" in report.problems


def test_main_exposes_require_live_runtime_gate(tmp_path, monkeypatch):
    calls = []

    def fake_collect(config, require_live_runtime=False):
        calls.append((config, require_live_runtime))
        return diagnostics.DiagnosticsReport(
            project_root=tmp_path,
            python_executable=Path("python"),
            overlay_exe=tmp_path / "overlay.exe",
            depth_model=tmp_path / "model.onnx",
            config_path=tmp_path / "config.yaml",
            config_loaded=True,
            ready=True,
            problems=[],
        )

    monkeypatch.setattr(diagnostics, "collect_diagnostics", fake_collect)

    code = diagnostics.main(["--config", str(tmp_path / "config.yaml"), "--require-live-runtime"])

    assert code == 0
    assert calls == [(str(tmp_path / "config.yaml"), True)]


def test_collect_diagnostics_marks_very_low_depth_rate_not_ready(tmp_path, monkeypatch):
    log_path = tmp_path / "overlay.log"
    log_path.write_text(
        "[15:25:55.013] Frame#120 acq[ok=118 timeout=2 lost=0 other=0] "
        "shm[LIVE reads=120 changes=9 (4/s) ts=10] "
        "depth[total=18 2Hz mode=fast] head=(0.00,0.00,54.28) rest=(0.00,0.00) "
        "rel=(0.00,0.00) wobble=0.00 strength=1.00 depth=30.00 hasFrame=1\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text("camera:\n  index: 0\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: log_path)

    report = diagnostics.collect_diagnostics(config_path=cfg)

    assert report.ready is False
    assert "depth inference too slow: 2Hz" in report.problems


def test_collect_diagnostics_skips_camera_probe_when_tracker_shm_is_live(tmp_path, monkeypatch):
    log_path = tmp_path / "overlay.log"
    log_path.write_text(
        "[15:25:55.013] Frame#120 acq[ok=118 timeout=2 lost=0 other=0] "
        "shm[LIVE reads=120 changes=9 (3/s) ts=10] "
        "depth[total=18 6Hz] head=(0.00,0.00,54.28) rest=(0.00,0.00) "
        "rel=(0.00,0.00) wobble=0.00 strength=1.00 depth=30.00 hasFrame=1\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text("camera:\n  index: 0\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: log_path)

    def fail_probe(index):
        raise AssertionError(f"camera probe should be skipped while tracker is live: {index}")

    monkeypatch.setattr(diagnostics, "_probe_camera", fail_probe)

    report = diagnostics.collect_diagnostics(config_path=cfg)
    text = diagnostics.format_diagnostics_report(report)

    assert report.ready is True
    assert report.camera == diagnostics.CameraProbe(
        index=0,
        opened=True,
        frame_ok=True,
        inferred_from_tracker=True,
    )
    assert "Camera: 0 (live tracker)" in text


def test_collect_diagnostics_reports_configured_display_backend_layout(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "overlay:\n"
        "  display_backend: stereo_autostereo\n"
        "  display_calibration:\n"
        "    viewer_distance_cm: 65.0\n"
        "    view_cone_deg: 35.0\n"
        "    stereo_layout: half_sbs\n"
        "    eye_order: right_left\n"
        "    ipd_mm: 63.5\n"
        "    panel_width_px: 3840\n"
        "    panel_height_px: 1080\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")

    report = diagnostics.collect_diagnostics(config_path=cfg)

    assert report.configured_backend_id == "stereo_autostereo"
    assert report.configured_backend_layout == {
        "columns": 2,
        "rows": 1,
        "view_count": 2,
    }
    assert report.display_calibration == {
        "viewer_distance_cm": 65.0,
        "view_cone_deg": 35.0,
        "stereo_layout": "half_sbs",
        "eye_order": "right_left",
        "ipd_mm": 63.5,
        "panel_width_px": 3840,
        "panel_height_px": 1080,
    }


def test_collect_diagnostics_reports_runtime_backend_from_overlay_log(tmp_path, monkeypatch):
    log_path = tmp_path / "overlay.log"
    log_path.write_text(
        "[15:26:00.371] Frame#300 acq[ok=300 timeout=0 lost=0 other=0] "
        "shm[LIVE reads=300 changes=200 (34/s) ts=123] "
        "depth[total=17 5Hz mode=balanced] gpu_ms=0.98 backend=1 "
        "head=(0.00,0.00,60.00) rest=(0.00,0.00) rel=(0.00,0.00) "
        "wobble=0.00 strength=1.00 depth=30.00 hasFrame=1\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: log_path)
    monkeypatch.setattr(diagnostics, "_probe_camera", lambda index: diagnostics.CameraProbe(index, True, True))

    report = diagnostics.collect_diagnostics(config_path=cfg, require_live_runtime=True)

    assert report.ready is True
    assert report.runtime_backend_id == "stereo_autostereo"


def test_collect_diagnostics_rejects_runtime_backend_mismatch(tmp_path, monkeypatch):
    log_path = tmp_path / "overlay.log"
    log_path.write_text(
        "[15:26:00.371] Frame#300 acq[ok=300 timeout=0 lost=0 other=0] "
        "shm[LIVE reads=300 changes=200 (34/s) ts=123] "
        "depth[total=17 5Hz mode=balanced] gpu_ms=0.98 backend=0 "
        "head=(0.00,0.00,60.00) rest=(0.00,0.00) rel=(0.00,0.00) "
        "wobble=0.00 strength=1.00 depth=30.00 hasFrame=1\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: log_path)
    monkeypatch.setattr(diagnostics, "_probe_camera", lambda index: diagnostics.CameraProbe(index, True, True))

    report = diagnostics.collect_diagnostics(config_path=cfg, require_live_runtime=True)

    assert report.ready is False
    assert report.runtime_backend_id == "desktop_overlay"
    assert "runtime backend desktop_overlay does not match configured backend stereo_autostereo" in report.problems


def test_collect_diagnostics_rejects_unknown_display_backend(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("overlay:\n  display_backend: mystery_backend\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")

    report = diagnostics.collect_diagnostics(config_path=cfg)

    assert report.ready is False
    assert "unknown display backend: mystery_backend" in report.problems


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
    assert "Default backend: desktop_overlay" in text
    assert "Runtime backend: unavailable" in text
    assert "Camera: not checked" in text


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
    monkeypatch.setattr(
        diagnostics,
        "collect_diagnostics",
        lambda _config, require_live_runtime=False: report,
    )

    code = diagnostics.main(["--config", str(tmp_path / "missing.yaml")])

    assert code == 1
    assert "NOT READY" in capsys.readouterr().out


def test_main_writes_report_to_output_file(tmp_path, monkeypatch, capsys):
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "overlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=tmp_path / "config.yaml",
        config_loaded=True,
        ready=True,
        problems=[],
    )
    output = tmp_path / "diagnostics.txt"
    monkeypatch.setattr(
        diagnostics,
        "collect_diagnostics",
        lambda _config, require_live_runtime=False: report,
    )

    code = diagnostics.main(["--config", str(tmp_path / "config.yaml"), "--output", str(output)])

    assert code == 0
    assert "READY" in output.read_text(encoding="utf-8")
    assert "wrote diagnostics report" in capsys.readouterr().out


def test_format_diagnostics_json_is_machine_readable(tmp_path):
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "overlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=tmp_path / "config.yaml",
        config_loaded=True,
        ready=True,
        problems=[],
        experimental_backend_ids=["stereo_autostereo"],
    )

    data = json.loads(diagnostics.format_diagnostics_json(report))

    assert data["ready"] is True
    assert data["overlay_exe"].endswith("overlay.exe")
    assert data["default_backend_id"] == "desktop_overlay"
    assert data["experimental_backend_ids"] == ["stereo_autostereo"]
    assert data["configured_backend_id"] == "desktop_overlay"
    assert data["runtime_backend_id"] is None
    assert data["configured_backend_layout"] == {
        "columns": 1,
        "rows": 1,
        "view_count": 1,
    }
    assert data["camera"] is None


def test_format_diagnostics_json_includes_camera_probe(tmp_path):
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "overlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=tmp_path / "config.yaml",
        config_loaded=True,
        ready=True,
        problems=[],
        camera=diagnostics.CameraProbe(index=0, opened=True, frame_ok=True, width=640, height=480),
    )

    data = json.loads(diagnostics.format_diagnostics_json(report))

    assert data["camera"] == {
        "index": 0,
        "opened": True,
        "frame_ok": True,
        "width": 640,
        "height": 480,
        "inferred_from_tracker": False,
    }


def test_format_diagnostics_json_includes_display_inventory(tmp_path):
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "overlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=tmp_path / "config.yaml",
        config_loaded=True,
        ready=True,
        problems=[],
        display_inventory=[
            diagnostics.DisplayInventoryItem(
                name="Generic PnP Monitor",
                device_id="DISPLAY\\SAM71AC",
                manufacturer="SAM",
                product_code="71AC",
                width_px=5120,
                height_px=1440,
                primary=True,
            )
        ],
    )

    data = json.loads(diagnostics.format_diagnostics_json(report))

    assert data["display_inventory"] == [
        {
            "name": "Generic PnP Monitor",
            "device_id": "DISPLAY\\SAM71AC",
            "manufacturer": "SAM",
            "product_code": "71AC",
            "width_px": 5120,
            "height_px": 1440,
            "primary": True,
        }
    ]


def test_format_diagnostics_report_includes_display_inventory(tmp_path):
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "overlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=tmp_path / "config.yaml",
        config_loaded=True,
        ready=True,
        problems=[],
        display_inventory=[
            diagnostics.DisplayInventoryItem(
                name="Generic PnP Monitor",
                device_id="DISPLAY\\SAM71AC",
                manufacturer="SAM",
                product_code="71AC",
                width_px=5120,
                height_px=1440,
                primary=True,
            )
        ],
    )

    text = diagnostics.format_diagnostics_report(report)

    assert "Connected displays:" in text
    assert "- Generic PnP Monitor 5120x1440 primary manufacturer=SAM product=71AC" in text


def test_main_writes_json_report_when_requested(tmp_path, monkeypatch, capsys):
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "overlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=tmp_path / "config.yaml",
        config_loaded=True,
        ready=True,
        problems=[],
    )
    output = tmp_path / "diagnostics.json"
    monkeypatch.setattr(
        diagnostics,
        "collect_diagnostics",
        lambda _config, require_live_runtime=False: report,
    )

    code = diagnostics.main([
        "--config",
        str(tmp_path / "config.yaml"),
        "--format",
        "json",
        "--output",
        str(output),
    ])

    assert code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["ready"] is True
    assert "wrote diagnostics report" in capsys.readouterr().out


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


def test_parse_overlay_summary_line_extracts_gpu_timing_when_present():
    line = (
        "[15:26:00.371] Frame#3249300 acq[ok=3247868 timeout=1432 lost=0 other=0] "
        "shm[LIVE reads=3249300 changes=369 (2/s) ts=80350123] "
        "depth[total=116440 8Hz] gpu_ms=0.42 head=(-1.09,0.97,58.62) rest=(0.63,-0.38) "
        "rel=(-1.72,1.35) wobble=0.00 strength=1.00 depth=30.00 hasFrame=1"
    )

    summary = diagnostics.parse_overlay_summary_line(line)

    assert summary is not None
    assert summary.gpu_ms == pytest.approx(0.42)


def test_parse_overlay_summary_line_extracts_expanded_runtime_timing():
    summary = diagnostics.parse_overlay_summary_line(
        "Frame#131 acq[ok=131 timeout=0 lost=0 other=0] "
        "shm[LIVE reads=131 changes=118 (57/s) ts=433652984] "
        "depth[total=12 8Hz mode=fast] "
        "timing[capture_cpu=0.192 draw_gpu=0.315 present_cpu=0.039 frame_cpu=0.271] "
        "backend=0 layout=0 eye_order=0 ipd=6.20 focus=0.00 panel=0x0 tracking=0 "
        "head=(0.00,0.00,60.00) hasFrame=1 capture=running capture_reason=bound"
    )

    assert summary is not None
    assert summary.gpu_ms == pytest.approx(0.315)
    assert summary.capture_cpu_ms == pytest.approx(0.192)
    assert summary.present_cpu_ms == pytest.approx(0.039)
    assert summary.frame_cpu_ms == pytest.approx(0.271)


def test_parse_overlay_summary_line_extracts_backend_when_present():
    line = (
        "[15:26:00.371] Frame#3249300 acq[ok=3247868 timeout=1432 lost=0 other=0] "
        "shm[LIVE reads=3249300 changes=369 (2/s) ts=80350123] "
        "depth[total=116440 8Hz] gpu_ms=0.42 backend=1 head=(-1.09,0.97,58.62) rest=(0.63,-0.38) "
        "rel=(-1.72,1.35) wobble=0.00 strength=1.00 depth=30.00 hasFrame=1"
    )

    summary = diagnostics.parse_overlay_summary_line(line)

    assert summary is not None
    assert summary.backend == 1


def test_parse_overlay_summary_line_extracts_runtime_calibration_when_present():
    line = (
        "[15:26:00.371] Frame#3249300 acq[ok=3247868 timeout=1432 lost=0 other=0] "
        "shm[LIVE reads=3249300 changes=369 (2/s) ts=80350123] "
        "depth[total=116440 8Hz mode=fast] gpu_ms=0.42 backend=1 "
        "layout=1 eye_order=1 ipd=6.35 focus=12.00 panel=3840x1080 tracking=1 "
        "head=(-1.09,0.97,58.62) rest=(0.63,-0.38) "
        "rel=(-1.72,1.35) wobble=0.00 strength=1.00 depth=30.00 hasFrame=1"
    )

    summary = diagnostics.parse_overlay_summary_line(line)

    assert summary is not None
    assert summary.stereo_layout == 1
    assert summary.eye_order == 1
    assert summary.ipd_cm == pytest.approx(6.35)
    assert summary.focus_plane_cm == pytest.approx(12.0)
    assert summary.panel_width_px == 3840
    assert summary.panel_height_px == 1080
    assert summary.tracking_mode == 1


def test_collect_diagnostics_rejects_runtime_calibration_mismatch(tmp_path, monkeypatch):
    log_path = tmp_path / "overlay.log"
    log_path.write_text(
        "[15:26:00.371] Frame#300 acq[ok=300 timeout=0 lost=0 other=0] "
        "shm[LIVE reads=300 changes=200 (34/s) ts=123] "
        "depth[total=17 5Hz mode=balanced] gpu_ms=0.98 backend=1 "
        "layout=0 eye_order=0 ipd=6.40 focus=0.00 panel=0x0 tracking=0 "
        "head=(0.00,0.00,60.00) rest=(0.00,0.00) rel=(0.00,0.00) "
        "wobble=0.00 strength=1.00 depth=30.00 hasFrame=1\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "overlay:\n"
        "  display_backend: stereo_autostereo\n"
        "  display_calibration:\n"
        "    stereo_layout: half_sbs\n"
        "    eye_order: right_left\n"
        "    ipd_mm: 63.5\n"
        "    focus_plane_cm: 12.0\n"
        "    panel_width_px: 3840\n"
        "    panel_height_px: 1080\n"
        "    tracking_mode: vendor_managed\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: log_path)
    monkeypatch.setattr(diagnostics, "_probe_camera", lambda index: diagnostics.CameraProbe(index, True, True))

    report = diagnostics.collect_diagnostics(config_path=cfg, require_live_runtime=True)

    assert report.ready is False
    assert "runtime stereo_layout 0 does not match configured half_sbs" in report.problems
    assert "runtime eye_order 0 does not match configured right_left" in report.problems
    assert "runtime panel_width_px 0 does not match configured 3840" in report.problems
    assert "runtime tracking_mode 0 does not match configured vendor_managed" in report.problems


def test_collect_diagnostics_allows_stale_tracker_for_vendor_managed_tracking(tmp_path, monkeypatch):
    log_path = tmp_path / "overlay.log"
    log_path.write_text(
        "[15:26:00.371] Frame#300 acq[ok=300 timeout=0 lost=0 other=0] "
        "shm[STALE (tracker running but not writing?) reads=300 changes=0 (0/s) ts=123] "
        "depth[total=17 5Hz mode=balanced] gpu_ms=0.98 backend=1 "
        "layout=1 eye_order=1 ipd=6.35 focus=12.00 panel=3840x1080 tracking=1 "
        "head=(0.00,0.00,60.00) rest=(0.00,0.00) rel=(0.00,0.00) "
        "wobble=0.00 strength=1.00 depth=30.00 hasFrame=1\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "overlay:\n"
        "  display_backend: stereo_autostereo\n"
        "  display_calibration:\n"
        "    stereo_layout: half_sbs\n"
        "    eye_order: right_left\n"
        "    ipd_mm: 63.5\n"
        "    focus_plane_cm: 12.0\n"
        "    panel_width_px: 3840\n"
        "    panel_height_px: 1080\n"
        "    tracking_mode: vendor_managed\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: log_path)
    monkeypatch.setattr(diagnostics, "_probe_camera", lambda index: diagnostics.CameraProbe(index, False, False))

    report = diagnostics.collect_diagnostics(config_path=cfg, require_live_runtime=True)

    assert report.ready is True
    assert "tracker shared memory stale" not in report.problems
    assert "vendor-managed tracking configured; Glassless3D tracker SHM is not required" in report.warnings


def test_collect_diagnostics_requires_captured_frame_for_live_runtime(tmp_path, monkeypatch):
    log_path = tmp_path / "overlay.log"
    log_path.write_text(
        "[15:26:00.371] Frame#300 acq[ok=300 timeout=0 lost=0 other=0] "
        "shm[LIVE reads=300 changes=200 (34/s) ts=123] "
        "depth[total=17 5Hz mode=balanced] gpu_ms=0.98 backend=0 "
        "head=(0.00,0.00,60.00) rest=(0.00,0.00) rel=(0.00,0.00) "
        "wobble=0.00 strength=1.00 depth=30.00 hasFrame=0\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: log_path)
    monkeypatch.setattr(diagnostics, "_probe_camera", lambda index: diagnostics.CameraProbe(index, True, True))

    report = diagnostics.collect_diagnostics(config_path=cfg, require_live_runtime=True)

    assert report.ready is False
    assert "overlay runtime has no captured frame" in report.problems


def test_parse_overlay_summary_line_extracts_depth_mode_when_present():
    line = (
        "[15:26:00.371] Frame#3249300 acq[ok=3247868 timeout=1432 lost=0 other=0] "
        "shm[LIVE reads=3249300 changes=369 (2/s) ts=80350123] "
        "depth[total=116440 8Hz mode=fast] gpu_ms=0.42 backend=1 head=(-1.09,0.97,58.62) rest=(0.63,-0.38) "
        "rel=(-1.72,1.35) wobble=0.00 strength=1.00 depth=30.00 hasFrame=1"
    )

    summary = diagnostics.parse_overlay_summary_line(line)

    assert summary is not None
    assert summary.depth_mode == "fast"


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
    assert report.ready is False
    assert "tracker shared memory stale" in report.problems
    assert "overlay log reports no captured frame" in report.warnings
