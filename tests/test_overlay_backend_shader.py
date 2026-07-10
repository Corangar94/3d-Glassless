from pathlib import Path


def test_overlay_shader_receives_display_backend_uniform():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "float displayBackend;" in source
    assert "g_displayBackend" in source


def test_overlay_shader_uses_runtime_ipd_for_stereo_eye_spread():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "float ipdCm;" in source
    assert "static_assert(sizeof(CBuf) % 16 == 0" in source
    assert "g_ipdCm" in source
    assert "s.ipdMm * 0.1f" in source
    assert "viewOffset * ipdCm" in source
    assert "viewOffset * 6.4" not in source


def test_overlay_settings_contract_includes_runtime_calibration_fields():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert 'STRUCT_FORMAT = "<fffffIfffffffIII" "IIIIfI"' in Path("tracker/shared_settings.py").read_text(encoding="utf-8")
    assert "uint32_t  stereoLayout;" in source
    assert "uint32_t  eyeOrder;" in source
    assert "uint32_t  panelWidthPx;" in source
    assert "uint32_t  panelHeightPx;" in source
    assert "float     focusPlaneCm;" in source
    assert "uint32_t  trackingMode;" in source
    assert "static_assert(sizeof(Settings) == 88" in source


def test_overlay_shader_uses_runtime_stereo_layout_and_eye_order():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "float stereoLayout;" in source
    assert "float eyeOrder;" in source
    assert "g_stereoLayout" in source
    assert "g_eyeOrder" in source
    assert "float rightEye = (eyeOrder < 0.5) ? rightSlot : (1.0 - rightSlot);" in source
    assert "if (stereoLayout < 0.5)" in source


def test_overlay_shader_uses_focus_plane_as_convergence_plane():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "float focusPlaneCm;" in source
    assert "g_focusPlaneCm" in source
    assert "float focus = max(focusPlaneCm, 0.0);" in source
    assert "float focusF = focus / (hz + focus);" in source
    assert "float f  = (oz / (hz + oz)) - focusF;" in source


def test_overlay_checks_constant_buffer_creation_and_map_failures():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert 'LogHR("CreateBuffer(CBuf)", hr)' in source
    assert 'FatalError(L"CreateBuffer(CBuf) failed", hr)' in source
    assert 'LogHR("Map(CBuf)", hr)' in source
    assert "if (FAILED(hr)) {\n        LogHR(\"Map(CBuf)\", hr);\n        return;\n    }" in source


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


def test_overlay_uses_crisp_scene_sampler_and_linear_depth_sampler():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "SamplerState SceneSmp : register(s0);" in source
    assert "SamplerState DepthSmp : register(s1);" in source
    assert "DepthTex.Sample(DepthSmp" in source
    assert "DepthPrevTex.Sample(DepthSmp" in source
    assert "SceneTex.SampleGrad(SceneSmp" in source
    assert "D3D11_FILTER_MIN_MAG_MIP_POINT" in source
    assert "D3D11_FILTER_MIN_MAG_MIP_LINEAR" in source


def test_overlay_caps_relative_head_motion_before_shader_parallax():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "kMaxParallaxRelCm" in source
    assert "ClampVectorLength(dx, dy, kMaxParallaxRelCm)" in source


def test_overlay_blends_depth_at_discontinuities_to_reduce_mount_tearing():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "SampleDepthCohesive" in source
    assert "kDepthCohesionBlend" in source
    assert "smoothstep(kDepthCohesionLow" in source
    assert "SampleDepthCohesive(localUV, dCropW, sceneDdx, sceneDdy)" in source


def test_overlay_shader_computes_depth_confidence_for_edge_protection():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "struct DepthSample" in source
    assert "confidence" in source
    assert "kDepthConfidenceLow" in source
    assert "kDepthConfidenceHigh" in source
    assert "ApplyConfidenceProtectedParallax" in source
    assert "ParallaxShift(d_final.depth" in source


def test_overlay_debug_depth_cycles_through_multiple_views():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "g_debugDepthMode" in source
    assert "kDebugDepthModeCount" in source
    assert "debugDepthMode" in source
    assert "debug confidence" in source
    assert "debug edge" in source


def test_overlay_targets_world_of_warcraft_window_instead_of_full_desktop():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "FindWindowW(nullptr, L\"World of Warcraft\")" in source
    assert "EnumWindows(FindWowWindowProc" in source
    assert "wcsstr(title, L\"World of Warcraft\")" in source
    assert "GetClientRect(target" in source
    assert "ClientToScreen(target" in source
    assert "g_useTargetWindow" in source
    assert "g_targetRect" in source
    assert "MoveWindow(g_hwnd" in source
    assert "CreateTexture2D(&td" in source


def test_overlay_normalizes_desktop_duplication_to_the_target_window_when_available():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "FindWindowW(nullptr, L\"World of Warcraft\")" in source
    assert "BuildUprightCaptureRegion" in source
    assert "NormalizeCapturedFrame" in source
    assert "CopyResource(g_rawCapTex, source)" in source
    assert "CopySubresourceRegion(g_capTex" not in source


def test_overlay_suppresses_parallax_when_tracker_pose_is_stale():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "kPoseStaleMs" in source
    assert "poseFresh" in source
    assert "lastPoseChangeMs" in source
    assert "if (!poseFresh) { dx = 0.0f; dy = 0.0f; }" in source


def test_depth_inferencer_masks_likely_hud_regions_before_model_input():
    source = Path("overlay/depth_infer.cpp").read_text(encoding="utf-8")

    assert "IsLikelyHudUv" in source
    assert "return;" in source
    assert "leave HUD-like pixels at ImageNet mean grey" in source


def test_overlay_shader_reduces_parallax_in_likely_hud_regions():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "HudParallaxScale" in source
    assert "kHudParallaxMinScale" in source
    assert "ApplyConfidenceProtectedParallax" in source
    assert "HudParallaxScale(localUV)" in source
