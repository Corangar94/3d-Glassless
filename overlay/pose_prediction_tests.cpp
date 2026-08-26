#include "pose_prediction.h"

#include <cmath>
#include <iostream>

namespace {

bool Near(float a, float b, float tolerance = 1e-4f) {
    return std::fabs(a - b) <= tolerance;
}

bool Check(bool condition, const char* message) {
    if (condition) return true;
    std::cerr << "pose_prediction_tests: " << message << '\n';
    return false;
}

}  // namespace

int main() {
    using g3d::pose_prediction::Extrapolate;
    using g3d::pose_prediction::ResidualDelayMs;

    int failures = 0;
    auto require = [&failures](bool condition, const char* message) {
        if (!Check(condition, message)) ++failures;
    };

    require(ResidualDelayMs(8, 0, 20) == 8, "uncovered publish age should be predicted");
    require(ResidualDelayMs(8, 10, 20) == 0, "producer future lead should prevent double prediction");
    require(ResidualDelayMs(27, 5, 20) == 20, "residual delay should be bounded");

    auto result = Extrapolate(
        1.0f, 2.0f, 60.0f,
        100.0f, -50.0f, 25.0f,
        10, 0, 0.9f, true);
    require(result.applied, "valid confident pose should extrapolate");
    require(result.residual_ms == 10, "residual time mismatch");
    require(Near(result.x, 2.0f), "x extrapolation mismatch");
    require(Near(result.y, 1.5f), "y extrapolation mismatch");
    require(Near(result.z, 60.25f), "z extrapolation mismatch");

    auto capped = Extrapolate(
        0.0f, 0.0f, 60.0f,
        1000.0f, -1000.0f, 1000.0f,
        40, 0, 0.9f, true);
    require(capped.residual_ms == 20, "maximum residual time not enforced");
    require(Near(capped.delta_x_cm, 2.0f), "x delta cap not enforced");
    require(Near(capped.delta_y_cm, -2.0f), "y delta cap not enforced");
    require(Near(capped.delta_z_cm, 2.5f), "z delta cap not enforced");

    auto low_confidence = Extrapolate(
        1.0f, 2.0f, 60.0f,
        100.0f, 100.0f, 100.0f,
        12, 0, 0.14f, true);
    require(!low_confidence.applied, "low confidence must disable native prediction");
    require(Near(low_confidence.x, 1.0f), "disabled prediction changed pose");

    auto invalid = Extrapolate(
        1.0f, 2.0f, 60.0f,
        100.0f, 100.0f, 100.0f,
        12, 0, 0.9f, false);
    require(!invalid.applied, "invalid pose must disable native prediction");

    return failures == 0 ? 0 : 1;
}
