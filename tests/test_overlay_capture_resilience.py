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

    assert "DXGI_PRESENT_DO_NOT_WAIT" in source
    assert "DXGI_ERROR_WAS_STILL_DRAWING" in source
    assert "g_presentRetryPending = true" in source
    assert "g_swap->Present(syncInterval, presentFlags)" in source
    assert "SetMaximumFrameLatency(1)" in source
    assert "GetDeviceRemovedReason" in source
    assert "DXGI_ERROR_DEVICE_REMOVED" in source
    assert "DXGI_ERROR_DEVICE_RESET" in source


def test_overlay_targets_configured_game_without_taking_focus_or_flashing_black():
    source = OVERLAY.read_text(encoding="utf-8")

    assert 'L"--target-exe"' in source
    assert 'L"--target-pid"' in source
    assert "pid != search->targetPid" in source
    assert "QueryFullProcessImageNameW" in source
    assert "WM_MOUSEACTIVATE" in source
    assert "MA_NOACTIVATE" in source
    assert "SetLayeredWindowAttributes(g_hwnd, 0, 0, LWA_ALPHA)" in source
    assert "First frame presented; reveal overlay" in source
    assert "WS_EX_TOOLWINDOW" in source


def test_overlay_hides_target_frames_when_the_game_is_not_foreground_or_capture_is_stale():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "GetForegroundWindow()" in source
    assert "foregroundPid == selectedPid" in source
    assert "kCaptureFrameStaleMs" in source
    assert "g_lastCaptureFrameMs" in source
    assert "Overlay visibility:" in source


def test_overlay_accepts_partially_masked_frames_but_recovers_uniform_black_capture():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "ProtectedContentMaskedOut" in source
    assert "CaptureIsUniformBlack" in source
    assert "accepting usable frame" in source
    assert 'SetCaptureState(CaptureState::Unavailable, "protected_content")' not in source
    assert 'SetCaptureState(CaptureState::Unavailable, "black_capture")' in source


def test_target_wgc_applies_uniform_black_capture_guard():
    source = OVERLAY.read_text(encoding="utf-8")
    wgc = source.split("static void UpdateWgcCapture()", 1)[1].split(
        "static void UpdateCapture()", 1
    )[0]

    assert "RejectUniformBlackTargetFrame()" in wgc
    assert wgc.index("CopyResource(g_capTex, frameTex)") < wgc.index(
        "RejectUniformBlackTargetFrame()"
    )
    assert wgc.index("RejectUniformBlackTargetFrame()") < wgc.index(
        "g_hasFrame = true"
    )
    assert "g_rebindRetry.RecordFailure(GetTickCount64())" in source
    assert "DXGI_ERROR_DEVICE_HUNG" in source
    assert "CaptureState::DeviceRecovery" in source


def test_tracker_filter_and_rest_calibration_only_advance_on_new_samples():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "bool newPoseSample = false" in source
    assert "if (poseFresh && newPoseSample)" in source
    assert "OneEuroFilter(g_oeX" in source
    assert "g_emaFrames++" in source
    assert "G3D_State" in source
    assert "trackerState == 1" in source


def test_pose_reader_uses_optional_companion_seqlock_with_legacy_fallback():
    source = OVERLAY.read_text(encoding="utf-8")

    assert 'L"G3D_Seq"' in source
    assert "ReadStablePose" in source
    assert "if (!g_seqView) return ReadStableSnapshot" in source
    assert "before & 1u" in source
    assert "before == after && (after & 1u) == 0" in source


def test_depth_crossfade_is_wall_clock_based_and_not_render_frame_based():
    depth = Path("overlay/depth_infer.cpp").read_text(encoding="utf-8")
    header = Path("overlay/depth_infer.h").read_text(encoding="utf-8")

    assert "std::chrono::steady_clock" in depth
    assert "kBlendDurationSec" in depth
    assert "kBlendFrames" not in depth
    assert "advance_blend" not in header


def test_ultrawide_depth_is_tiled_across_full_width_without_static_hud_masks():
    overlay = OVERLAY.read_text(encoding="utf-8")
    depth = Path("overlay/depth_infer.cpp").read_text(encoding="utf-8")

    assert "tile_count" in depth
    assert "DepthInferencer::kModelSize * tile_count" in depth
    assert "IsLikelyHudUv" not in depth
    assert "HudParallaxScale" not in overlay


def test_depth_readback_is_gpu_reduced_and_nonblocking():
    depth = Path("overlay/depth_infer.cpp").read_text(encoding="utf-8")

    assert "compact_bgra" in depth
    assert "kReadbackRingSize = 3" in depth
    assert "D3D11_MAP_FLAG_DO_NOT_WAIT" in depth
    assert "DXGI_ERROR_WAS_STILL_DRAWING" in depth
    assert "preprocess_compact" in depth
    assert "sd.Width = cap_w" not in depth


def test_overlay_uses_flip_model_waitable_pacing_with_legacy_fallback():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "DXGI_SWAP_EFFECT_FLIP_DISCARD" in source
    assert "DXGI_SWAP_CHAIN_FLAG_FRAME_LATENCY_WAITABLE_OBJECT" in source
    assert "GetFrameLatencyWaitableObject" in source
    assert "D3D11CreateDeviceAndSwapChain(legacy fallback)" in source
    assert "g_frameLatencyWaitable" in source


def test_overlay_reports_capture_draw_present_and_full_frame_timing():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "g_lastCaptureCpuMs" in source
    assert "g_lastPresentCpuMs" in source
    assert "g_lastFrameCpuMs" in source
    assert "timing[capture_cpu=" in source


def test_hdr_capture_uses_output_metadata_and_explicit_fallback_tone_mapping():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "IDXGIOutput6" in source
    assert "DXGI_OUTPUT_DESC1" in source
    assert "DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020" in source
    assert "packedHdr ? 2.0f" in source
    assert "color.rgb=color.rgb/(1.0+color.rgb)" in source


def test_tiled_depth_uses_global_percentiles_and_contrast():
    depth = Path("overlay/depth_infer.cpp").read_text(encoding="utf-8")

    assert "global_samples" in depth
    assert "global_lo" in depth
    assert "global_hi" in depth
    assert "tile_overlap" in depth
    assert "tile_crop_x" in depth
    assert "Apply one contrast transform over the stitched frame" in depth


def test_overlay_prefers_wgc_target_window_capture_for_games():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "Windows.Graphics.Capture.Direct3D11CaptureFramePool" in source
    assert "IGraphicsCaptureItemInterop" in source
    assert "CreateForWindow" in source
    assert "CreateFreeThreaded" in source
    assert "TryGetNextFrame" in source
    assert "IDirect3DDxgiInterfaceAccess" in source
    assert '"bound_target_wgc"' in source
    assert '"bound_desktop"' in source
    assert '"target_not_running"' in source
    assert '"target_capture_unavailable"' in source
    assert '"bound_target_duplication"' not in source
    assert '"desktop_fallback"' not in source
    assert "falling back to desktop duplication" not in source


def test_singleton_rejection_is_silent_and_cannot_truncate_the_active_log():
    source = OVERLAY.read_text(encoding="utf-8")

    mutex_index = source.index("CreateMutexW")
    log_index = source.index("LogInit();", source.index("int WINAPI WinMain"))
    assert mutex_index < log_index
    assert 'L"Already Running"' not in source


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
