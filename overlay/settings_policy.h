#pragma once

#include <cmath>
#include <cstdint>

namespace g3d::settings {

inline constexpr float kMaxStrength = 16.0f;
inline constexpr float kMaxVirtualDepthCm = 1000.0f;
inline constexpr float kMaxScreenDimensionCm = 1000.0f;
inline constexpr float kMaxDepthGamma = 16.0f;
inline constexpr float kMaxFocusRadius = 1.0f;
inline constexpr float kMaxIpdMm = 200.0f;
inline constexpr float kMaxDeadzoneMm = 1000.0f;
inline constexpr float kMaxFocusPlaneCm = 1000.0f;
inline constexpr uint32_t kMaxPanelDimensionPx = 65535u;

enum Field : uint32_t {
    kStrengthX = 1u << 0,
    kStrengthY = 1u << 1,
    kVirtualDepth = 1u << 2,
    kScreenWidth = 1u << 3,
    kScreenHeight = 1u << 4,
    kDepthCurve = 1u << 5,
    kDepthGamma = 1u << 6,
    kFocusRadius = 1u << 7,
    kIpd = 1u << 8,
    kDeadzone = 1u << 9,
    kDisplayBackend = 1u << 10,
    kDepthMode = 1u << 11,
    kStereoLayout = 1u << 12,
    kEyeOrder = 1u << 13,
    kPanelWidth = 1u << 14,
    kPanelHeight = 1u << 15,
    kFocusPlane = 1u << 16,
    kTrackingMode = 1u << 17,
};

struct SharedSettingsInput {
    float strength_x = 1.0f;
    float strength_y = 1.0f;
    float virtual_depth_cm = 30.0f;
    float screen_width_cm = 0.0f;
    float screen_height_cm = 0.0f;
    uint32_t depth_curve = 1;
    float depth_gamma = 1.0f;
    float focus_radius = 0.1f;
    float ipd_mm = 64.0f;
    float deadzone_mm = 5.0f;
    uint32_t display_backend = 0;
    uint32_t depth_mode = 3;
    uint32_t stereo_layout = 0;
    uint32_t eye_order = 0;
    uint32_t panel_width_px = 0;
    uint32_t panel_height_px = 0;
    float focus_plane_cm = 0.0f;
    uint32_t tracking_mode = 0;
};

struct ValidationResult {
    uint32_t rejected_fields = 0;
    uint32_t normalized_fields = 0;

    constexpr bool accepted() const {
        return rejected_fields == 0;
    }
};

inline bool FiniteClosedRange(float value, float minimum, float maximum) {
    return std::isfinite(value) && value >= minimum && value <= maximum;
}

inline bool FiniteOpenClosedRange(float value, float minimum, float maximum) {
    return std::isfinite(value) && value > minimum && value <= maximum;
}

inline ValidationResult ValidateSharedSettings(
    const SharedSettingsInput& input) {
    ValidationResult result;
    if (!FiniteClosedRange(input.strength_x, 0.0f, kMaxStrength))
        result.rejected_fields |= kStrengthX;
    if (!FiniteClosedRange(input.strength_y, 0.0f, kMaxStrength))
        result.rejected_fields |= kStrengthY;
    if (!FiniteClosedRange(
            input.virtual_depth_cm, 0.0f, kMaxVirtualDepthCm))
        result.rejected_fields |= kVirtualDepth;
    if (!FiniteClosedRange(
            input.screen_width_cm, 0.0f, kMaxScreenDimensionCm))
        result.rejected_fields |= kScreenWidth;
    if (!FiniteClosedRange(
            input.screen_height_cm, 0.0f, kMaxScreenDimensionCm))
        result.rejected_fields |= kScreenHeight;
    if (input.depth_curve > 2u)
        result.normalized_fields |= kDepthCurve;
    if (!FiniteOpenClosedRange(input.depth_gamma, 0.0f, kMaxDepthGamma))
        result.rejected_fields |= kDepthGamma;
    if (!FiniteClosedRange(input.focus_radius, 0.0f, kMaxFocusRadius))
        result.rejected_fields |= kFocusRadius;
    if (!FiniteOpenClosedRange(input.ipd_mm, 0.0f, kMaxIpdMm))
        result.rejected_fields |= kIpd;
    if (!FiniteClosedRange(input.deadzone_mm, 0.0f, kMaxDeadzoneMm))
        result.rejected_fields |= kDeadzone;
    if (input.display_backend > 2u)
        result.normalized_fields |= kDisplayBackend;
    if (input.depth_mode > 3u)
        result.normalized_fields |= kDepthMode;
    if (input.stereo_layout > 1u)
        result.normalized_fields |= kStereoLayout;
    if (input.eye_order > 1u)
        result.normalized_fields |= kEyeOrder;
    if (input.panel_width_px > kMaxPanelDimensionPx)
        result.rejected_fields |= kPanelWidth;
    if (input.panel_height_px > kMaxPanelDimensionPx)
        result.rejected_fields |= kPanelHeight;
    if (!FiniteClosedRange(
            input.focus_plane_cm, 0.0f, kMaxFocusPlaneCm))
        result.rejected_fields |= kFocusPlane;
    if (input.tracking_mode > 1u)
        result.normalized_fields |= kTrackingMode;
    return result;
}

inline constexpr uint32_t NormalizeDepthCurve(uint32_t value) {
    return value <= 2u ? value : 1u;
}

inline constexpr uint32_t NormalizeDisplayBackend(uint32_t value) {
    return value <= 2u ? value : 0u;
}

}  // namespace g3d::settings
