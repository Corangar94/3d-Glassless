from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_overlay_show_is_gated_until_first_depth_result_is_uploaded():
    header = _source("overlay/depth_infer.h")

    assert "G3DShowWindowAfterDepthUpload" in header
    assert "depth->inferences_completed() > 0" in header
    assert "depth->run(captured_frame)" in header
    assert "has_frame = false;" in header
    assert "return ::ShowWindow(window, SW_HIDE);" in header


def test_show_window_guard_is_scoped_to_overlay_translation_unit():
    cmake = _source("overlay/CMakeLists.txt")

    assert "set_source_files_properties(overlay.cpp PROPERTIES" in cmake
    assert "COMPILE_DEFINITIONS G3D_OVERLAY_SHOWWINDOW_GUARD" in cmake
    assert "target_compile_definitions(Glassless3DOverlay PRIVATE" in cmake
    assert "G3D_OVERLAY_SHOWWINDOW_GUARD" not in cmake.split(
        "target_compile_definitions(Glassless3DOverlay PRIVATE", 1
    )[1].split(")", 1)[0]
