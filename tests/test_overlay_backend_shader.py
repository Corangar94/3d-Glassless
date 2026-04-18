from pathlib import Path


def test_overlay_shader_receives_display_backend_uniform():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "float displayBackend;" in source
    assert "g_displayBackend" in source


def test_overlay_shader_contains_stereo_and_quilt_layout_paths():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "stereo_autostereo" in source
    assert "lightfield_quilt" in source
    assert "viewOffset" in source
    assert "localUV" in source


def test_overlay_settings_include_depth_performance_mode():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "depthMode" in source
    assert "mode=%s" in source
    assert "DepthModeName" in source


def test_depth_inferencer_exposes_runtime_performance_mode():
    header = Path("overlay/depth_infer.h").read_text(encoding="utf-8")
    source = Path("overlay/depth_infer.cpp").read_text(encoding="utf-8")

    assert "set_performance_mode" in header
    assert "performance_mode" in source
