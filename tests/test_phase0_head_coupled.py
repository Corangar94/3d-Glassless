from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_inverse_warp_moves_virtual_content_with_the_viewer():
    overlay = source("overlay/overlay.cpp")
    assert "float2 requestedUV = localUV" in overlay
    assert "- ApplyConfidenceProtectedParallax" in overlay
    assert "+ ApplyConfidenceProtectedParallax" not in overlay
    assert "ResolveDepthDisocclusion" in overlay


def test_overlay_uses_explicit_recenter_instead_of_moving_rest_ema():
    overlay = source("overlay/overlay.cpp")
    assert "HOTKEY_RECENTER" in overlay
    assert "Ctrl+R recenter" in overlay
    assert "g_recenterRequested" in overlay
    assert "Recentered: restX" in overlay
    assert "DriftEMA calibrated" not in overlay


def test_depth_is_required_and_fallback_matches_depth_convention():
    overlay = source("overlay/overlay.cpp")
    launcher = source("launcher/overlay_process.py")
    assert "static bool InitDepth()" in overlay
    assert 'SetCaptureState(CaptureState::Unavailable, "depth_unavailable")' in overlay
    assert "uint16_t farDepth = 0x3C00u" in overlay
    assert "g_depth != nullptr" in overlay
    assert "missing_overlay_runtime_assets" in launcher
    assert "runtime is incomplete" in launcher


def test_deadzone_is_applied_only_after_recentering():
    tracker = source("tracker/main.py")
    overlay = source("overlay/overlay.cpp")
    run_body = tracker[tracker.index("class TrackingLoop"):tracker.index("def _load_config")]
    assert "_apply_deadzone(" not in run_body
    assert "SoftDeadzone(dx, g_deadzoneCm)" in overlay


def test_camera_properties_and_live_calibration_are_wired():
    tracker = source("tracker/main.py")
    mediapipe_tracker = source("tracker/face_tracker.py")
    cv_tracker = source("tracker/face_tracker_cv2.py")
    assert "CAP_PROP_FRAME_WIDTH" in tracker
    assert "CAP_PROP_FRAME_HEIGHT" in tracker
    assert "CAP_PROP_FPS" in tracker
    assert "camera_width=int(cam.get" in tracker
    assert "set_calibration(" in tracker
    assert "def set_calibration(" in mediapipe_tracker
    assert "def set_calibration(" in cv_tracker


def test_bootstrap_rebuilds_and_verifies_runtime_layout():
    bootstrap = source("scripts/_bootstrap_core.py")
    function = bootstrap[bootstrap.index("def step_build_overlay"):bootstrap.index("# -- Main")]
    assert "already present: Glassless3DOverlay.exe" not in function
    assert "_sync_overlay_runtime_files" in function
    assert "shutil.copy2(built, OVERLAY_OUT)" in function
