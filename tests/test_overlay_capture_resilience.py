from pathlib import Path


OVERLAY = Path("overlay/overlay.cpp")


def test_overlay_selects_the_output_matching_the_target_monitor():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "MonitorFromWindow" in source
    assert "desc.Monitor == monitor" in source
    assert "D3D_DRIVER_TYPE_UNKNOWN" in source
    assert "adapter->EnumOutputs(0, &output)" not in source


def test_overlay_normalizes_full_output_before_depth_and_parallax():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "g_rawCapTex" in source
    assert "g_capRtv" in source
    assert "NormalizeCapturedFrame" in source
    assert "CopyResource(g_rawCapTex, source)" in source
    assert "BuildUprightCaptureRegion" in source
    assert "TargetWindowToDuplicationBox" not in source
    assert "CopySubresourceRegion(g_capTex" not in source


def test_overlay_has_explicit_rotation_mapping_for_all_dxgi_rotations():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "DXGI_MODE_ROTATION_ROTATE90" in source
    assert "DXGI_MODE_ROTATION_ROTATE180" in source
    assert "DXGI_MODE_ROTATION_ROTATE270" in source
    assert "NormalizeCB" in source
