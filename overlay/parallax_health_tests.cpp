#include "parallax_health.h"

#include <cmath>
#include <iostream>
#include <limits>

namespace {

bool Near(float a, float b, float tolerance = 1e-4f) {
    return std::fabs(a - b) <= tolerance;
}

bool Require(bool condition, const char* message) {
    if (condition) return true;
    std::cerr << "parallax_health_tests: " << message << '\n';
    return false;
}

}  // namespace

int main() {
    using g3d::parallax::AgeScale;
    using g3d::parallax::ConfidenceScale;
    using g3d::parallax::HealthInputs;
    using g3d::parallax::Saturate;
    using g3d::parallax::SlewScale;
    using g3d::parallax::SmoothStep01;
    using g3d::parallax::TargetScale;

    int failures = 0;
    auto check = [&failures](bool condition, const char* message) {
        if (!Require(condition, message)) ++failures;
    };

    check(Near(AgeScale(0, 70, 350), 1.0f), "fresh age should be full strength");
    check(Near(AgeScale(70, 70, 350), 1.0f), "full-strength age boundary changed");
    check(Near(AgeScale(350, 70, 350), 0.0f), "stale age should be zero strength");
    const float mid_pose_age = AgeScale(210, 70, 350);
    check(
        mid_pose_age > 0.45f && mid_pose_age < 0.55f,
        "pose-age midpoint should be near half strength");
    check(Near(AgeScale(10, 350, 70), 0.0f), "reversed age bounds must fail closed");
    check(Near(AgeScale(10, 70, 70), 0.0f), "zero-width age bounds must fail closed");

    check(Near(ConfidenceScale(0.15f), 0.0f), "low confidence should zero strength");
    check(Near(ConfidenceScale(0.75f), 1.0f), "high confidence should reach full strength");
    const float mid_confidence = ConfidenceScale(0.45f);
    check(
        mid_confidence > 0.45f && mid_confidence < 0.55f,
        "confidence midpoint should be near half strength");
    check(
        Near(ConfidenceScale(std::numeric_limits<float>::quiet_NaN()), 0.0f),
        "NaN confidence must fail closed");
    check(
        Near(ConfidenceScale(std::numeric_limits<float>::infinity()), 0.0f),
        "infinite confidence must fail closed");
    check(
        Near(Saturate(std::numeric_limits<float>::quiet_NaN()), 0.0f),
        "NaN saturation must not become full strength");
    check(
        Near(SmoothStep01(std::numeric_limits<float>::quiet_NaN()), 0.0f),
        "NaN smoothstep input must fail closed");

    HealthInputs healthy = {};
    healthy.pose_fresh = true;
    healthy.depth_ready = true;
    healthy.pose_v2 = true;
    healthy.pose_confidence = 0.95f;
    healthy.pose_age_ms = 20;
    healthy.depth_age_ms = 40;
    check(Near(TargetScale(healthy), 1.0f), "healthy inputs should permit full parallax");

    HealthInputs stale_pose = healthy;
    stale_pose.pose_age_ms = 350;
    check(Near(TargetScale(stale_pose), 0.0f), "stale pose should fade parallax to zero");

    HealthInputs stale_depth = healthy;
    stale_depth.depth_age_ms = 750;
    check(Near(TargetScale(stale_depth), 0.0f), "stale depth should fade parallax to zero");

    HealthInputs invalid_confidence = healthy;
    invalid_confidence.pose_confidence = std::numeric_limits<float>::quiet_NaN();
    check(
        Near(TargetScale(invalid_confidence), 0.0f),
        "non-finite PoseV2 confidence must zero target strength");

    HealthInputs legacy = healthy;
    legacy.pose_v2 = false;
    legacy.pose_confidence = std::numeric_limits<float>::quiet_NaN();
    check(
        Near(TargetScale(legacy), 1.0f),
        "legacy producers should not be penalized for unavailable confidence");

    HealthInputs invalid = healthy;
    invalid.pose_fresh = false;
    check(Near(TargetScale(invalid), 0.0f), "invalid pose should zero target strength");

    // One degradation half-life moves 1 -> 0 halfway toward zero.
    const float degraded = SlewScale(1.0f, 0.0f, 0.045f);
    check(
        degraded > 0.49f && degraded < 0.51f,
        "degradation half-life no longer halves the remaining strength");

    // The same wall time recovers much less because recovery intentionally has
    // a longer half-life, preventing a visible snap back to full parallax.
    const float recovered = SlewScale(0.0f, 1.0f, 0.045f);
    check(
        recovered > 0.12f && recovered < 0.14f,
        "recovery half-life no longer produces the expected slow ramp");
    check(
        (1.0f - degraded) > recovered,
        "health should degrade faster than it recovers");

    const float nan = std::numeric_limits<float>::quiet_NaN();
    const float infinity = std::numeric_limits<float>::infinity();
    check(Near(SlewScale(nan, 1.0f, 0.016f), 0.0492f, 0.002f),
          "non-finite current strength should recover from zero normally");
    check(Near(SlewScale(1.0f, nan, 0.016f), 0.7818f, 0.003f),
          "non-finite target strength should degrade toward zero");
    check(Near(SlewScale(1.0f, 0.0f, nan), 0.0f),
          "invalid elapsed time must immediately apply degradation");
    check(Near(SlewScale(0.0f, 1.0f, infinity), 0.0f),
          "invalid elapsed time must not increase parallax");
    check(Near(SlewScale(1.0f, 0.0f, 0.016f, nan, 0.220f), 0.0f),
          "invalid degradation half-life must fail toward target");
    check(Near(SlewScale(0.0f, 1.0f, 0.016f, 0.045f, nan), 0.0f),
          "invalid recovery half-life must not increase parallax");

    return failures == 0 ? 0 : 1;
}
