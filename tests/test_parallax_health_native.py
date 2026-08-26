from pathlib import Path


def _source(relative: str) -> str:
    return Path(relative).read_text(encoding="utf-8")


def test_native_health_envelope_uses_pose_confidence_and_age():
    header = _source("overlay/parallax_health.h")

    assert "struct HealthInputs" in header
    assert "pose_confidence" in header
    assert "pose_age_ms" in header
    assert "depth_age_ms" in header
    assert "ConfidenceScale" in header
    assert "AgeScale(inputs.pose_age_ms, 70, 350)" in header
    assert "AgeScale(inputs.depth_age_ms, 140, 750)" in header


def test_health_envelope_degrades_faster_than_it_recovers():
    header = _source("overlay/parallax_health.h")

    assert "degrade_half_life_s = 0.045f" in header
    assert "recover_half_life_s = 0.220f" in header
    assert "target < current" in header
    assert "-0.69314718056f * dt_seconds / half_life" in header


def test_overlay_scales_both_parallax_axes_with_health_envelope():
    source = _source("overlay/overlay.cpp")

    assert '#include "parallax_health.h"' in source
    assert "g_parallaxHealthScale" in source
    assert "g3d::parallax::TargetScale(healthInputs)" in source
    assert "g3d::parallax::SlewScale(" in source
    assert "g_strengthX * g_parallaxHealthScale" in source
    assert "g_strengthY * g_parallaxHealthScale" in source


def test_legacy_pose_gets_age_fade_without_confidence_penalty():
    source = _source("overlay/overlay.cpp")

    assert "poseAgeMs = seenPose ? (nowMs - lastPoseChangeMs)" in source
    assert "usingPoseV2 ? poseConfidence : 1.0f" in source
    assert 'usingPoseV2 ? "v2" : "legacy"' in source


def test_health_ramp_keeps_event_driven_renderer_animating():
    source = _source("overlay/overlay.cpp")

    assert "const bool healthAnimating" in source
    assert "motionActive || blendActive || healthAnimating" in source


def test_health_telemetry_is_logged_for_support_bundles():
    source = _source("overlay/overlay.cpp")

    assert "ParallaxHealth scale=%.3f target=%.3f" in source
    assert "pose_age_ms=%u" in source
    assert "depth_age_ms=%u" in source
