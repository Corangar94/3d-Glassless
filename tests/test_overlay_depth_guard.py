from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_overlay_show_is_gated_until_first_depth_result_is_uploaded():
    source = _source("overlay/overlay.cpp")
    visibility = source.split("static void UpdateOverlayVisibility() {", 1)[1].split("\nstatic ", 1)[0]
    assert "g_depth->depth_updates_published() > 0" in visibility
    assert "g_depth->depth_age_ms() <= 750" in visibility
    frame = source.split("static void Frame() {", 1)[1]
    assert frame.index("g_depth->run(g_capTex, g_lastCaptureFrameMs)") < frame.index("UpdateOverlayVisibility();")


def test_show_window_guard_is_scoped_to_overlay_translation_unit():
    header = _source("overlay/depth_infer.h")
    cmake = _source("overlay/CMakeLists.txt")
    assert "#define ShowWindow" not in header
    assert "G3D_OVERLAY_SHOWWINDOW_GUARD" not in cmake
    assert "G3DShowWindowAfterDepthUpload" not in header
    assert "overlay.cpp" in cmake
