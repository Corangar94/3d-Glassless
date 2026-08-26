#include "parallax_health.h"

#include <cassert>
#include <cmath>

namespace {

bool Near(float a, float b, float tolerance = 1e-4f) {
    return std::fabs(a - b) <= tolerance;
}

}  // namespace

int main() {
    using g3d::parallax::AgeScale;
    using g3d::parallax::ConfidenceScale;
    using g3d::parallax::HealthInputs;
    using g3d::parallax::SlewScale;
    using g3d::parallax::TargetScale;

    assert(Near(AgeScale(0, 70, 350), 1.0f));
    assert(Near(AgeScale(70, 70, 350), 1.0f));
    assert(Near(AgeScale(350, 70, 350), 0.0f));
    const float mid_pose_age = AgeScale(210, 70, 350);
    assert(mid_pose_age > 0.45f && mid_pose_age < 0.55f);

    assert(Near(ConfidenceScale(0.15f), 0.0f));
    assert(Near(ConfidenceScale(0.75f), 1.0f));
    const float mid_confidence = ConfidenceScale(0.45f);
    assert(mid_confidence > 0.45f && mid_confidence < 0.55f);

    HealthInputs healthy = {};
    healthy.pose_fresh = true;
    healthy.depth_ready = true;
    healthy.pose_v2 = true;
    healthy.pose_confidence = 0.95f;
    healthy.pose_age_ms = 20;
    healthy.depth_age_ms = 40;
    assert(Near(TargetScale(healthy), 1.0f));

    HealthInputs stale_pose = healthy;
    stale_pose.pose_age_ms = 350;
    assert(Near(TargetScale(stale_pose), 0.0f));

    HealthInputs stale_depth = healthy;
    stale_depth.depth_age_ms = 750;
    assert(Near(TargetScale(stale_depth), 0.0f));

    HealthInputs legacy = healthy;
    legacy.pose_v2 = false;
    legacy.pose_confidence = 0.01f;
    assert(Near(TargetScale(legacy), 1.0f));

    HealthInputs invalid = healthy;
    invalid.pose_fresh = false;
    assert(Near(TargetScale(invalid), 0.0f));

    // One degradation half-life moves 1 -> 0 halfway toward zero.
    const float degraded = SlewScale(1.0f, 0.0f, 0.045f);
    assert(degraded > 0.49f && degraded < 0.51f);

    // The same wall time recovers much less because recovery intentionally has
    // a longer half-life, preventing a visible snap back to full parallax.
    const float recovered = SlewScale(0.0f, 1.0f, 0.045f);
    assert(recovered > 0.12f && recovered < 0.14f);
    assert((1.0f - degraded) > recovered);

    return 0;
}
