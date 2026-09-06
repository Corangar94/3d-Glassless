#pragma once
// The same function literal is compiled into production and WARP regressions.
#define G3D_DEPTH_COHESION_HLSL \
"float G3DTrimmedMean5(float a, float b, float c, float d, float e) {\n" \
"    float lo = min(a, min(min(b, c), min(d, e)));\n" \
"    float hi = max(a, max(max(b, c), max(d, e)));\n" \
"    return max(0.0, (a + b + c + d + e - lo - hi) / 3.0);\n" \
"}\n"
