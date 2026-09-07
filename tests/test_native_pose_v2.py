from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_overlay_prefers_versioned_predicted_pose_without_double_filtering():
    source = _source("overlay/overlay.cpp")

    assert 'L"G3D_PoseV2"' in source
    assert "struct PoseV2" in source
    assert 'static_assert(sizeof(PoseV2) == 64' in source
    assert "ReadStablePoseV2" in source
    assert "if (!usingPoseV2)" in source
    assert "poseVelocityX = poseV2.vx" in source
    assert "poseV2.flags & POSE_V2_VALID" in source


def test_overlay_keeps_legacy_one_euro_fallback():
    source = _source("overlay/overlay.cpp")

    assert "OneEuroFilter(g_oeX" in source
    assert "OneEuroPredictedVelocity(g_oeX)" in source
    assert "else if (g_shmView)" in source


def test_scene_sampling_uses_linear_filter_and_depth_consistency_guard():
    source = _source("overlay/overlay.cpp")

    assert "D3D11_FILTER_MIN_MAG_MIP_LINEAR" in source
    assert "SampleSceneQuality" in source
    assert "ResolveDepthDisocclusion" in source
    assert "sourceConsistency" in source
    assert "laplacian * amount" in source


def test_pose_v2_mappings_are_released_at_shutdown():
    source = _source("overlay/overlay.cpp")

    assert "UnmapViewOfFile((void*)g_poseV2View)" in source
    assert "CloseHandle(g_poseV2H)" in source
    assert "UnmapViewOfFile((void*)g_poseV2SeqView)" in source
    assert "CloseHandle(g_poseV2SeqH)" in source


def test_pose_v2_attachment_retries_when_tracker_or_sequence_starts_late():
    source = _source("overlay/overlay.cpp")
    attach = source.split("static void TryAttachPoseV2()", 1)[1].split(
        "static bool ReadStablePoseV2", 1
    )[0]
    frame_attach = source.split("static void Frame()", 1)[1].split(
        "PollTargetWindow();", 1
    )[0]

    assert "if (g_poseV2View) return;" not in attach
    assert "if (!g_poseV2H)" in attach
    assert "if (!g_poseV2SeqH)" in attach
    assert "TryAttachPoseV2();" in frame_attach
