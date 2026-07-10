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


def test_overlay_uses_a_scoped_lease_for_every_acquired_desktop_frame():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "class DesktopFrameLease" in source
    assert "~DesktopFrameLease" in source
    assert "duplication_->ReleaseFrame()" in source
    assert "g_dup->ReleaseFrame();" not in source


def test_overlay_has_explicit_rebind_and_unavailable_paths():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "CaptureState::Rebinding" in source
    assert "CaptureState::Unavailable" in source
    assert "DXGI_ERROR_ACCESS_LOST" in source
    assert "DXGI_ERROR_INVALID_CALL" in source
    assert "DXGI_ERROR_NOT_CURRENTLY_AVAILABLE" in source
    assert "DXGI_ERROR_SESSION_DISCONNECTED" in source
    assert "target_spans_output" in source
    assert "CaptureStatus: state=%s reason=%s" in source


def test_overlay_does_not_sleep_inside_duplication_reset():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "static void ResetDuplication()" not in source
    assert "Sleep(300)" not in source
    assert "RetrySchedule g_rebindRetry" in source
