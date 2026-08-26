#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>

namespace g3d::parallax {

struct HealthInputs {
    bool pose_fresh = false;
    bool depth_ready = false;
    bool pose_v2 = false;
    float pose_confidence = 1.0f;
    uint32_t pose_age_ms = 0;
    uint32_t depth_age_ms = 0;
};

inline float Saturate(float value) {
    return std::max(0.0f, std::min(1.0f, value));
}

inline float SmoothStep01(float value) {
    const float x = Saturate(value);
    return x * x * (3.0f - 2.0f * x);
}

inline float AgeScale(
    uint32_t age_ms,
    uint32_t full_strength_until_ms,
    uint32_t zero_strength_at_ms) {
    if (age_ms <= full_strength_until_ms) return 1.0f;
    if (age_ms >= zero_strength_at_ms) return 0.0f;
    const float span = static_cast<float>(
        zero_strength_at_ms - full_strength_until_ms);
    const float normalized = static_cast<float>(
        age_ms - full_strength_until_ms) / std::max(1.0f, span);
    return 1.0f - SmoothStep01(normalized);
}

inline float ConfidenceScale(float confidence) {
    // MediaPipe confidence is useful well before the old hard 0.05 cutoff, but
    // full-strength reprojection should require a genuinely stable face lock.
    constexpr float kZeroAt = 0.15f;
    constexpr float kFullAt = 0.75f;
    return SmoothStep01((confidence - kZeroAt) / (kFullAt - kZeroAt));
}

inline float TargetScale(const HealthInputs& inputs) {
    if (!inputs.pose_fresh || !inputs.depth_ready) return 0.0f;

    const float pose_age = AgeScale(inputs.pose_age_ms, 70, 350);
    const float depth_age = AgeScale(inputs.depth_age_ms, 140, 750);
    const float confidence = inputs.pose_v2
        ? ConfidenceScale(inputs.pose_confidence)
        : 1.0f;

    // The weakest upstream signal owns the comfort envelope. Multiplication
    // would punish several mildly imperfect signals too aggressively.
    return std::min(pose_age, std::min(depth_age, confidence));
}

inline float SlewScale(
    float current,
    float target,
    float dt_seconds,
    float degrade_half_life_s = 0.045f,
    float recover_half_life_s = 0.220f) {
    current = Saturate(current);
    target = Saturate(target);
    dt_seconds = std::max(0.0f, std::min(0.25f, dt_seconds));
    const float half_life = target < current
        ? std::max(0.001f, degrade_half_life_s)
        : std::max(0.001f, recover_half_life_s);
    const float alpha = 1.0f - std::exp(
        -0.69314718056f * dt_seconds / half_life);
    return current + (target - current) * alpha;
}

}  // namespace g3d::parallax
