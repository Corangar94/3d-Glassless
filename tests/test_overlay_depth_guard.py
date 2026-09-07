from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_overlay_visibility_requires_publication_without_mutating_capture():
    source = _source("overlay/overlay.cpp")
    visibility = source.split("static void UpdateOverlayVisibility() {", 1)[1].split(
        "static void SetCaptureState(", 1
    )[0]
    assert "g_depth->depth_updates_published() > 0" in visibility
    assert "OverlayVisible(" in visibility
    assert "g_hasFrame =" not in visibility
    assert "g_depth->run(" not in visibility


def test_visibility_is_not_a_hidden_showwindow_macro():
    assert "G3D_OVERLAY_SHOWWINDOW_GUARD" not in _source("overlay/CMakeLists.txt")
    assert "#define ShowWindow" not in _source("overlay/depth_infer.h")
    assert "test_first_frame_visibility" in _source("overlay/runtime_integration_tests.cpp")
