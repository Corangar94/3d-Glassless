#include "settings_policy.h"

#include <cmath>
#include <iostream>
#include <limits>

namespace {

bool Check(bool condition, const char* message) {
    if (condition) return true;
    std::cerr << "settings_policy_tests: " << message << '\n';
    return false;
}

}  // namespace

int main() {
    using namespace g3d::settings;

    int failures = 0;
    const auto require = [&failures](bool condition, const char* message) {
        if (!Check(condition, message)) ++failures;
    };

    SharedSettingsInput defaults;
    auto result = ValidateSharedSettings(defaults);
    require(result.accepted(), "defaults must be accepted");
    require(result.rejected_fields == 0, "defaults rejected fields");
    require(result.normalized_fields == 0, "defaults normalized fields");

    SharedSettingsInput zero_strength = defaults;
    zero_strength.strength_x = 0.0f;
    zero_strength.strength_y = 0.0f;
    result = ValidateSharedSettings(zero_strength);
    require(result.accepted(), "zero strengths must disable parallax axes");

    SharedSettingsInput exact_bounds = defaults;
    exact_bounds.strength_x = kMaxStrength;
    exact_bounds.strength_y = kMaxStrength;
    exact_bounds.virtual_depth_cm = kMaxVirtualDepthCm;
    exact_bounds.screen_width_cm = kMaxScreenDimensionCm;
    exact_bounds.screen_height_cm = kMaxScreenDimensionCm;
    exact_bounds.depth_gamma = kMaxDepthGamma;
    exact_bounds.focus_radius = kMaxFocusRadius;
    exact_bounds.ipd_mm = kMaxIpdMm;
    exact_bounds.deadzone_mm = kMaxDeadzoneMm;
    exact_bounds.panel_width_px = kMaxPanelDimensionPx;
    exact_bounds.panel_height_px = kMaxPanelDimensionPx;
    exact_bounds.focus_plane_cm = kMaxFocusPlaneCm;
    result = ValidateSharedSettings(exact_bounds);
    require(result.accepted(), "exact safety bounds must be accepted");

    SharedSettingsInput invalid = defaults;
    invalid.strength_x = -0.01f;
    invalid.virtual_depth_cm = std::numeric_limits<float>::infinity();
    invalid.screen_width_cm = std::numeric_limits<float>::quiet_NaN();
    invalid.depth_gamma = 0.0f;
    invalid.focus_radius = kMaxFocusRadius + 0.01f;
    invalid.ipd_mm = 0.0f;
    invalid.deadzone_mm = kMaxDeadzoneMm + 1.0f;
    invalid.panel_width_px = kMaxPanelDimensionPx + 1u;
    invalid.focus_plane_cm = -1.0f;
    result = ValidateSharedSettings(invalid);
    require(!result.accepted(), "invalid numeric settings must be rejected");
    for (uint32_t field : {
             kStrengthX,
             kVirtualDepth,
             kScreenWidth,
             kDepthGamma,
             kFocusRadius,
             kIpd,
             kDeadzone,
             kPanelWidth,
             kFocusPlane,
         }) {
        require((result.rejected_fields & field) != 0, "missing rejected field bit");
    }

    SharedSettingsInput enums = defaults;
    enums.depth_curve = 99;
    enums.display_backend = 99;
    enums.depth_mode = 99;
    enums.stereo_layout = 99;
    enums.eye_order = 99;
    enums.tracking_mode = 99;
    result = ValidateSharedSettings(enums);
    require(result.accepted(), "unknown enums should normalize, not stall settings");
    for (uint32_t field : {
             kDepthCurve,
             kDisplayBackend,
             kDepthMode,
             kStereoLayout,
             kEyeOrder,
             kTrackingMode,
         }) {
        require((result.normalized_fields & field) != 0, "missing normalized field bit");
    }

    require(NormalizeDepthCurve(0) == 0, "linear curve changed");
    require(NormalizeDepthCurve(1) == 1, "sqrt curve changed");
    require(NormalizeDepthCurve(2) == 2, "gamma curve changed");
    require(NormalizeDepthCurve(3) == 1, "unknown curve must use sqrt");
    require(NormalizeDisplayBackend(0) == 0, "desktop backend changed");
    require(NormalizeDisplayBackend(1) == 1, "stereo backend changed");
    require(NormalizeDisplayBackend(2) == 2, "quilt backend changed");
    require(NormalizeDisplayBackend(3) == 0, "unknown backend must use desktop");

    return failures == 0 ? 0 : 1;
}
