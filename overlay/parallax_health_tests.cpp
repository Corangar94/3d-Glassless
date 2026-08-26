#include "parallax_health.h"

#include <cmath>
#include <iostream>

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
    using g3d::parallax::SlewScale;
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

    check(Near(ConfidenceScale(0.15f), 0.0f), "low confidence should zero strength");
    check(Near(ConfidenceScale(0.75f), 1.0f), "high confidence should reach full strength");
    const float mid_confidence = ConfidenceScale(0.45f);
    check(
        mid_confidence > 0.45f && mid_confidence < 0.55f,
        "confidence midpoint should be near half strength");

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

    HealthInputs legacy = healthy;
    legacy.pose_v2 = false;
    legacy.pose_confidence = 0.01f;
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

    return failures == 0 ? 0 : 1;
}
