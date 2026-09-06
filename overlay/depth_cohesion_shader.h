#pragma once

#include <algorithm>
#include <array>

namespace g3d::depth_cohesion {

// Five saturated depth taps with the single lowest and highest samples removed.
// Exactly three values remain, so the normalized trimmed mean divides by three.
inline float TrimmedMean5(float a, float b, float c, float d, float e) {
    const std::array<float, 5> samples = {a, b, c, d, e};
    const auto bounds = std::minmax_element(samples.begin(), samples.end());
    const float sum = a + b + c + d + e;
    return std::max(
        0.0f,
        (sum - *bounds.first - *bounds.second) / 3.0f);
}

}  // namespace g3d::depth_cohesion

// The same HLSL literal is compiled into production and the WARP regression.
#define G3D_DEPTH_COHESION_HLSL \
"float G3DTrimmedMean5(float a, float b, float c, float d, float e) {\n" \
"    float lo = min(a, min(min(b, c), min(d, e)));\n" \
"    float hi = max(a, max(max(b, c), max(d, e)));\n" \
"    return max(0.0, (a + b + c + d + e - lo - hi) / 3.0);\n" \
"}\n"
