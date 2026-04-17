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
