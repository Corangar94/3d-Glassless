from pathlib import Path

from launcher.diagnostics import parse_overlay_summary_line


def test_native_depth_api_exposes_auto_controller_and_telemetry():
    header = Path("overlay/depth_infer.h").read_text(encoding="utf-8")
    source = Path("overlay/depth_infer.cpp").read_text(encoding="utf-8")

    assert "3=auto" in header
    assert "active_performance_mode()" in header
    assert "set_runtime_load" in header
    assert "active_model_width()" in header
    assert "last_inference_ms()" in header
    assert "depth_age_ms()" in header
    assert "resolve_performance_mode" in source
    assert "auto_candidate_streak" in source
    assert "render_cost > 13.0f" in source


def test_overlay_logs_requested_and_active_depth_profile():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert 'case 3: return "auto"' in source
    assert "active=%s profile=%dx%d tiles=%d" in source
    assert "inference_ms=%.2f blend_ms=%.1f age_ms=%u" in source
    assert "g_depth->set_runtime_load" in source


def test_diagnostics_parses_extended_depth_telemetry():
    summary = parse_overlay_summary_line(
        "[12:00:00.000] Frame#120 acq[ok=120 timeout=0 lost=0 other=0] "
        "shm[LIVE reads=120 changes=60 (30/s) ts=1234] "
        "depth[total=12 10Hz mode=auto active=fast profile=392x224 tiles=1 "
        "inference_ms=92.50 blend_ms=88.0 age_ms=54] "
        "timing[capture_cpu=0.200 draw_gpu=1.300 present_cpu=0.100 frame_cpu=2.100] "
        "backend=0 layout=0 eye_order=0 ipd=6.40 focus=0.00 panel=0x0 tracking=0 "
        "head=(0.00,0.00,60.00) rest=(0.00,0.00) rel=(0.00,0.00) wobble=0.00 "
        "strength=1.00 depth=30.00 hasFrame=1 capture=running capture_reason=bound_desktop"
    )

    assert summary is not None
    assert summary.depth_mode == "auto"
    assert summary.active_depth_mode == "fast"
    assert summary.depth_model_width == 392
    assert summary.depth_model_height == 224
    assert summary.scheduled_tiles == 1
    assert summary.inference_ms == 92.5
    assert summary.blend_ms == 88.0
    assert summary.depth_age_ms == 54


def test_diagnostics_remains_backward_compatible_with_old_summary():
    summary = parse_overlay_summary_line(
        "Frame#10 acq[ok=10 timeout=0 lost=0 other=0] "
        "shm[LIVE reads=10 changes=10 (10/s) ts=12] "
        "depth[total=2 2Hz mode=balanced] "
        "timing[capture_cpu=0.2 draw_gpu=1.0 present_cpu=0.1 frame_cpu=2.0] "
        "backend=0 head=(0.0,0.0,60.0) hasFrame=1 capture=running capture_reason=bound_desktop"
    )

    assert summary is not None
    assert summary.depth_mode == "balanced"
    assert summary.active_depth_mode is None
    assert summary.depth_model_width is None


def test_launcher_and_wizard_offer_auto_depth_mode():
    mainwindow = Path("launcher/mainwindow.py").read_text(encoding="utf-8")
    wizard = Path("launcher/wizard.py").read_text(encoding="utf-8")
    settings = Path("tracker/shared_settings.py").read_text(encoding="utf-8")

    assert '"auto": 3' in mainwindow
    assert '("Auto depth", 3)' in mainwindow
    assert '"depth_performance_mode": "auto"' in wizard
    assert "3=auto" in settings
