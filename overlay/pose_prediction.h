#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>

namespace g3d::pose_prediction {

struct Result {
    float x = 0.0f;
    float y = 0.0f;
    float z = 60.0f;
    uint32_t residual_ms = 0;
    float delta_x_cm = 0.0f;
    float delta_y_cm = 0.0f;
    float delta_z_cm = 0.0f;
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

inline uint32_t ResidualDelayMs(
    uint32_t publish_age_ms,
    uint32_t producer_prediction_lead_ms,
    uint32_t maximum_residual_ms = 20) {
    if (publish_age_ms <= producer_prediction_lead_ms) return 0;
    return std::min(
        maximum_residual_ms,
        publish_age_ms - producer_prediction_lead_ms);
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
    float maximum_z_delta_cm = 2.5f) {
    Result result;
    result.x = x;
    result.y = y;
    result.z = z;

    if (!valid || !std::isfinite(confidence) || confidence < 0.15f) {
        return result;
    }
    if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z)
        || !std::isfinite(vx_cm_s) || !std::isfinite(vy_cm_s)
        || !std::isfinite(vz_cm_s)) {
        return result;
    }

    result.residual_ms = ResidualDelayMs(
        publish_age_ms,
        producer_prediction_lead_ms,
        maximum_residual_ms);
    if (result.residual_ms == 0) return result;

    const float dt = static_cast<float>(result.residual_ms) / 1000.0f;
    const Vector2 xy_delta = ClampMagnitude(
        vx_cm_s * dt,
        vy_cm_s * dt,
        maximum_xy_delta_cm);
    result.delta_x_cm = xy_delta.x;
    result.delta_y_cm = xy_delta.y;
    result.delta_z_cm = ClampAbs(vz_cm_s * dt, maximum_z_delta_cm);
    result.x += result.delta_x_cm;
    result.y += result.delta_y_cm;
    result.z = std::max(1.0f, result.z + result.delta_z_cm);
    result.applied = true;
    return result;
}

}  // namespace g3d::pose_prediction