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


def test_overlay_marks_bindings_dirty_for_dpi_and_display_changes():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "SetProcessDpiAwarenessContext" in source
    assert "WM_DPICHANGED" in source
    assert "WM_DISPLAYCHANGE" in source
    assert "g_bindingDirty = true" in source


def test_overlay_checks_present_and_enters_device_recovery():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "const HRESULT present_hr = g_swap->Present(1, 0);" in source
    assert "SetMaximumFrameLatency(1)" in source
    assert "GetDeviceRemovedReason" in source
    assert "DXGI_ERROR_DEVICE_REMOVED" in source
    assert "DXGI_ERROR_DEVICE_RESET" in source


def test_overlay_targets_configured_game_without_taking_focus_or_flashing_black():
    source = OVERLAY.read_text(encoding="utf-8")

    assert 'L"--target-exe"' in source
    assert "QueryFullProcessImageNameW" in source
    assert "WM_MOUSEACTIVATE" in source
    assert "MA_NOACTIVATE" in source
    assert "SetLayeredWindowAttributes(g_hwnd, 0, 0, LWA_ALPHA)" in source
    assert "First frame presented; reveal overlay" in source
    assert "DXGI_ERROR_DEVICE_HUNG" in source
    assert "CaptureState::DeviceRecovery" in source


def test_depth_cleanup_releases_both_current_and_previous_depth_resources():
    source = Path("overlay/depth_infer.cpp").read_text(encoding="utf-8")

    assert "if (depth_prev_srv) { depth_prev_srv->Release(); depth_prev_srv = nullptr; }" in source
    assert "if (depth_prev_tex) { depth_prev_tex->Release(); depth_prev_tex = nullptr; }" in source
    assert "if (depth_srv) { depth_srv->Release(); depth_srv = nullptr; }" in source
    assert "if (depth_tex) { depth_tex->Release(); depth_tex = nullptr; }" in source


def test_depth_worker_run_can_be_terminated_before_join():
    source = Path("overlay/depth_infer.cpp").read_text(encoding="utf-8")

    assert "std::unique_ptr<Ort::RunOptions>" in source
    assert "session->Run(*run_options" in source
    assert "run_options->SetTerminate()" in source
    assert source.index("run_options->SetTerminate()") < source.index("worker.join()")


def test_acquire_device_loss_enters_device_recovery_before_generic_rebind():
    source = OVERLAY.read_text(encoding="utf-8")
    update_capture = source[source.index("static void UpdateCapture()") :]

    assert 'EnterDeviceRecovery("AcquireNextFrame"' in update_capture
    assert update_capture.index('EnterDeviceRecovery("AcquireNextFrame"') < update_capture.index(
        '"acquire_failed"'
    )


def test_hidden_capture_states_use_bounded_message_wait_and_wall_clock_summary():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "CaptureIdleWaitMs" in source
    assert "MsgWaitForMultipleObjectsEx" in source
    assert "lastSummaryMs" in source
    assert "frameCount % 60" not in source


def test_depth_rate_handles_inference_counter_reset_after_recovery():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "infNow >= lastInferences" in source
