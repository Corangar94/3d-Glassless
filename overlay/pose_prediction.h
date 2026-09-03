#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>

namespace g3d::pose_prediction {

struct Result {
    float x = 0.0f;
    float y = 0.0f;
    float z = 60.0f;
    // Magnitude retained for the existing overlay diagnostic field.
    uint32_t residual_ms = 0;
    // Positive predicts forward from the producer target; negative rewinds an
    // intentionally over-leading producer pose back toward render time.
    int32_t signed_residual_ms = 0;
    float confidence_scale = 0.0f;
    float delta_x_cm = 0.0f;
    float delta_y_cm = 0.0f;
    float delta_z_cm = 0.0f;
    float input_xy_speed_cm_s = 0.0f;
    float input_z_speed_cm_s = 0.0f;
    float bounded_xy_speed_cm_s = 0.0f;
    float bounded_z_speed_cm_s = 0.0f;
    bool velocity_limited = false;
    bool rewound = false;
    bool applied = false;
};

struct Vector2 {
    float x = 0.0f;
    float y = 0.0f;
};

inline float FiniteNonnegativeLimit(float value) {
    return std::isfinite(value) ? std::max(0.0f, value) : 0.0f;
}

inline float ClampAbs(float value, float maximum_absolute) {
    if (!std::isfinite(value)) return 0.0f;
    const float limit = FiniteNonnegativeLimit(maximum_absolute);
    return std::max(-limit, std::min(limit, value));
}

inline Vector2 ClampMagnitude(
    float x,
    float y,
    float maximum_magnitude) {
    if (!std::isfinite(x) || !std::isfinite(y)) return {};
    const double limit = static_cast<double>(
        FiniteNonnegativeLimit(maximum_magnitude));
    const double dx = static_cast<double>(x);
    const double dy = static_cast<double>(y);
    const double magnitude = std::hypot(dx, dy);
    if (magnitude <= limit || magnitude <= 0.0) return {x, y};
    const double scale = limit / magnitude;
    return {
        static_cast<float>(dx * scale),
        static_cast<float>(dy * scale),
    };
}

inline float SaturatingMagnitude(float x, float y) {
    if (!std::isfinite(x) || !std::isfinite(y)) return 0.0f;
    const double magnitude = std::hypot(
        static_cast<double>(x),
        static_cast<double>(y));
    return static_cast<float>(std::min(
        magnitude,
        static_cast<double>(std::numeric_limits<float>::max())));
}

inline uint32_t ResidualDelayMs(
    uint32_t publish_age_ms,
    uint32_t producer_prediction_lead_ms,
    uint32_t maximum_residual_ms = 20) {
    // Historical forward-only helper retained for direct callers.
    if (publish_age_ms <= producer_prediction_lead_ms) return 0;
    return std::min(
        maximum_residual_ms,
        publish_age_ms - producer_prediction_lead_ms);
}

inline int32_t ResidualCorrectionMs(
    uint32_t publish_age_ms,
    uint32_t producer_prediction_lead_ms,
    uint32_t maximum_forward_ms = 20,
    uint32_t maximum_rewind_ms = 80) {
    const int64_t difference = static_cast<int64_t>(publish_age_ms)
        - static_cast<int64_t>(producer_prediction_lead_ms);
    const uint32_t signed_limit = static_cast<uint32_t>(
        std::numeric_limits<int32_t>::max());
    if (difference > 0) {
        const uint32_t limit = std::min(maximum_forward_ms, signed_limit);
        return static_cast<int32_t>(std::min<int64_t>(difference, limit));
    }
    if (difference < 0) {
        const uint32_t limit = std::min(maximum_rewind_ms, signed_limit);
        const int64_t magnitude = std::min<int64_t>(-difference, limit);
        return -static_cast<int32_t>(magnitude);
    }
    return 0;
}

inline float PredictionConfidenceScale(
    float confidence,
    float minimum_confidence = 0.15f,
    float full_confidence = 0.75f) {
    if (!std::isfinite(confidence)
        || !std::isfinite(minimum_confidence)
        || !std::isfinite(full_confidence)
        || minimum_confidence < 0.0f
        || full_confidence <= minimum_confidence) {
        return 0.0f;
    }
    if (confidence <= minimum_confidence) return 0.0f;
    if (confidence >= full_confidence) return 1.0f;
    const float t = (confidence - minimum_confidence)
        / (full_confidence - minimum_confidence);
    // Smoothstep avoids a visible velocity step around the confidence floor.
    return t * t * (3.0f - 2.0f * t);
}

inline Result Extrapolate(
    float x,
    float y,
    float z,
    float vx_cm_s,
    float vy_cm_s,
    float vz_cm_s,
    uint32_t publish_age_ms,
    uint32_t producer_prediction_lead_ms,
    float confidence,
    bool valid,
    uint32_t maximum_residual_ms = 20,
    float maximum_xy_delta_cm = 2.0f,
    float maximum_z_delta_cm = 2.5f,
    uint32_t maximum_rewind_ms = 80,
    float maximum_xy_speed_cm_s = 300.0f,
    float maximum_z_speed_cm_s = 360.0f) {
    Result result;
    result.x = x;
    result.y = y;
    result.z = z;

    if (!valid || !std::isfinite(confidence)) return result;
    if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z)
        || !std::isfinite(vx_cm_s) || !std::isfinite(vy_cm_s)
        || !std::isfinite(vz_cm_s)) {
        return result;
    }

    result.input_xy_speed_cm_s = SaturatingMagnitude(vx_cm_s, vy_cm_s);
    result.input_z_speed_cm_s = std::fabs(vz_cm_s);
    const Vector2 bounded_xy_velocity = ClampMagnitude(
        vx_cm_s,
        vy_cm_s,
        maximum_xy_speed_cm_s);
    const float bounded_z_velocity = ClampAbs(
        vz_cm_s,
        maximum_z_speed_cm_s);
    result.bounded_xy_speed_cm_s = SaturatingMagnitude(
        bounded_xy_velocity.x,
        bounded_xy_velocity.y);
    result.bounded_z_speed_cm_s = std::fabs(bounded_z_velocity);
    result.velocity_limited = (
        result.bounded_xy_speed_cm_s < result.input_xy_speed_cm_s
        || result.bounded_z_speed_cm_s < result.input_z_speed_cm_s
    );

    result.confidence_scale = PredictionConfidenceScale(confidence);
    if (result.confidence_scale <= 0.0f) return result;

    result.signed_residual_ms = ResidualCorrectionMs(
        publish_age_ms,
        producer_prediction_lead_ms,
        maximum_residual_ms,
        maximum_rewind_ms);
    if (result.signed_residual_ms == 0) return result;
    result.rewound = result.signed_residual_ms < 0;
    result.residual_ms = static_cast<uint32_t>(
        result.rewound
            ? -static_cast<int64_t>(result.signed_residual_ms)
            : result.signed_residual_ms);

    const float dt = static_cast<float>(result.signed_residual_ms) / 1000.0f;
    const float confidence_dt = dt * result.confidence_scale;
    const Vector2 xy_delta = ClampMagnitude(
        bounded_xy_velocity.x * confidence_dt,
        bounded_xy_velocity.y * confidence_dt,
        maximum_xy_delta_cm);
    result.delta_x_cm = xy_delta.x;
    result.delta_y_cm = xy_delta.y;
    result.delta_z_cm = ClampAbs(
        bounded_z_velocity * confidence_dt,
        maximum_z_delta_cm);
    result.x += result.delta_x_cm;
    result.y += result.delta_y_cm;
    result.z = std::max(1.0f, result.z + result.delta_z_cm);
    result.applied = true;
    return result;
}

}  // namespace g3d::pose_prediction
