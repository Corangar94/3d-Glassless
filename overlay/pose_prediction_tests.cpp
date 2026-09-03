#include "pose_prediction.h"

#include <cmath>
#include <iostream>
#include <limits>

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
    using g3d::pose_prediction::ClampMagnitude;
    using g3d::pose_prediction::Extrapolate;
    using g3d::pose_prediction::PredictionConfidenceScale;
    using g3d::pose_prediction::ResidualCorrectionMs;
    using g3d::pose_prediction::ResidualDelayMs;

    int failures = 0;
    auto require = [&failures](bool condition, const char* message) {
        if (!Check(condition, message)) ++failures;
    };

    // Keep the historical direct helper unchanged for downstream callers.
    require(ResidualDelayMs(8, 0, 20) == 8, "uncovered publish age should be predicted");
    require(ResidualDelayMs(8, 10, 20) == 0, "forward-only helper should not rewind");
    require(ResidualDelayMs(27, 5, 20) == 20, "forward residual delay should be bounded");

    require(ResidualCorrectionMs(8, 0) == 8, "positive correction mismatch");
    require(ResidualCorrectionMs(8, 10) == -2, "producer over-lead should rewind");
    require(ResidualCorrectionMs(27, 5) == 20, "forward correction cap mismatch");
    require(ResidualCorrectionMs(5, 100) == -80, "rewind correction cap mismatch");
    require(ResidualCorrectionMs(12, 12) == 0, "matched target needs no correction");

    require(Near(PredictionConfidenceScale(0.15f), 0.0f), "confidence floor mismatch");
    require(Near(PredictionConfidenceScale(0.45f), 0.5f), "mid confidence smoothstep mismatch");
    require(Near(PredictionConfidenceScale(0.75f), 1.0f), "full confidence mismatch");
    require(Near(PredictionConfidenceScale(2.0f), 1.0f), "confidence upper clamp mismatch");
    require(
        Near(PredictionConfidenceScale(std::numeric_limits<float>::quiet_NaN()), 0.0f),
        "NaN confidence must fail closed");

    auto result = Extrapolate(
        1.0f, 2.0f, 60.0f,
        100.0f, -50.0f, 25.0f,
        10, 0, 0.9f, true);
    require(result.applied, "valid confident pose should extrapolate");
    require(!result.rewound, "forward prediction marked as rewind");
    require(result.signed_residual_ms == 10, "signed forward residual mismatch");
    require(result.residual_ms == 10, "residual magnitude mismatch");
    require(Near(result.confidence_scale, 1.0f), "full-confidence gain mismatch");
    require(Near(result.x, 2.0f), "x extrapolation mismatch");
    require(Near(result.y, 1.5f), "y extrapolation mismatch");
    require(Near(result.z, 60.25f), "z extrapolation mismatch");

    auto rewind = Extrapolate(
        1.0f, 2.0f, 60.0f,
        100.0f, -50.0f, 25.0f,
        5, 15, 0.9f, true);
    require(rewind.applied, "valid producer over-lead should be corrected");
    require(rewind.rewound, "negative temporal correction must be marked as rewind");
    require(rewind.signed_residual_ms == -10, "signed rewind residual mismatch");
    require(rewind.residual_ms == 10, "rewind magnitude mismatch");
    require(Near(rewind.x, 0.0f), "x rewind mismatch");
    require(Near(rewind.y, 2.5f), "y rewind mismatch");
    require(Near(rewind.z, 59.75f), "z rewind mismatch");

    auto confidence_scaled = Extrapolate(
        1.0f, 2.0f, 60.0f,
        100.0f, 0.0f, 0.0f,
        10, 0, 0.45f, true);
    require(confidence_scaled.applied, "mid-confidence correction should remain active");
    require(Near(confidence_scaled.confidence_scale, 0.5f), "mid-confidence gain mismatch");
    require(Near(confidence_scaled.delta_x_cm, 0.5f), "confidence did not attenuate delta");

    auto capped = Extrapolate(
        0.0f, 0.0f, 60.0f,
        1000.0f, -1000.0f, 1000.0f,
        40, 0, 0.9f, true);
    require(capped.signed_residual_ms == 20, "maximum forward time not enforced");
    require(capped.residual_ms == 20, "maximum residual magnitude not enforced");
    require(
        Near(std::hypot(capped.delta_x_cm, capped.delta_y_cm), 2.0f),
        "combined XY delta cap not enforced");
    require(
        Near(capped.delta_x_cm, std::sqrt(2.0f)),
        "diagonal x component should preserve direction");
    require(
        Near(capped.delta_y_cm, -std::sqrt(2.0f)),
        "diagonal y component should preserve direction");
    require(Near(capped.delta_z_cm, 2.5f), "z delta cap not enforced");

    auto rewind_capped = Extrapolate(
        0.0f, 0.0f, 60.0f,
        1000.0f, -1000.0f, 1000.0f,
        0, 80, 0.9f, true);
    require(rewind_capped.signed_residual_ms == -80, "maximum rewind time not enforced");
    require(
        Near(std::hypot(rewind_capped.delta_x_cm, rewind_capped.delta_y_cm), 2.0f),
        "rewind XY cap not enforced");
    require(rewind_capped.delta_x_cm < 0.0f, "rewind x direction changed");
    require(rewind_capped.delta_y_cm > 0.0f, "rewind y direction changed");
    require(Near(rewind_capped.delta_z_cm, -2.5f), "rewind z cap not enforced");

    auto axis_capped = Extrapolate(
        0.0f, 0.0f, 60.0f,
        1000.0f, 0.0f, 0.0f,
        20, 0, 0.9f, true);
    require(Near(axis_capped.delta_x_cm, 2.0f), "axis x cap changed");
    require(Near(axis_capped.delta_y_cm, 0.0f), "axis y cap changed");

    auto directional = ClampMagnitude(6.0f, 8.0f, 5.0f);
    require(Near(directional.x, 3.0f), "vector cap changed x direction");
    require(Near(directional.y, 4.0f), "vector cap changed y direction");

    auto exact_target = Extrapolate(
        1.0f, 2.0f, 60.0f,
        100.0f, 100.0f, 100.0f,
        12, 12, 0.9f, true);
    require(!exact_target.applied, "matched producer/render target should not move");
    require(exact_target.signed_residual_ms == 0, "matched target residual mismatch");

    auto low_confidence = Extrapolate(
        1.0f, 2.0f, 60.0f,
        100.0f, 100.0f, 100.0f,
        12, 0, 0.14f, true);
    require(!low_confidence.applied, "low confidence must disable native prediction");
    require(Near(low_confidence.x, 1.0f), "disabled prediction changed pose");

    auto nan_confidence = Extrapolate(
        1.0f, 2.0f, 60.0f,
        100.0f, 100.0f, 100.0f,
        12, 0, std::numeric_limits<float>::quiet_NaN(), true);
    require(!nan_confidence.applied, "NaN confidence must disable prediction");

    auto infinite_confidence = Extrapolate(
        1.0f, 2.0f, 60.0f,
        100.0f, 100.0f, 100.0f,
        12, 0, std::numeric_limits<float>::infinity(), true);
    require(!infinite_confidence.applied, "infinite confidence must disable prediction");

    auto invalid_limit = Extrapolate(
        1.0f, 2.0f, 60.0f,
        100.0f, 100.0f, 100.0f,
        12, 0, 0.9f, true, 20,
        std::numeric_limits<float>::quiet_NaN(),
        std::numeric_limits<float>::quiet_NaN());
    require(invalid_limit.applied, "valid pose should still process invalid caps safely");
    require(Near(invalid_limit.delta_x_cm, 0.0f), "NaN XY cap must fail closed");
    require(Near(invalid_limit.delta_y_cm, 0.0f), "NaN XY cap must fail closed");
    require(Near(invalid_limit.delta_z_cm, 0.0f), "NaN Z cap must fail closed");

    auto invalid = Extrapolate(
        1.0f, 2.0f, 60.0f,
        100.0f, 100.0f, 100.0f,
        12, 0, 0.9f, false);
    require(!invalid.applied, "invalid pose must disable native prediction");

    return failures == 0 ? 0 : 1;
}
