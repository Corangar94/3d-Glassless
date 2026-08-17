# Depth Confidence Debug Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve perceived 3D stability by damping parallax at unreliable depth edges and adding debug views that show depth/confidence behavior.

**Architecture:** Keep this shader-side first so it can be shipped without changing the shared settings ABI. The overlay pixel shader will produce a small depth sample struct containing cohesive depth, raw depth range, and confidence; parallax uses confidence to reduce edge tearing, and Ctrl+D cycles through off/depth/confidence/edge debug views.

**Tech Stack:** C++17, Direct3D 11/HLSL embedded in `overlay/overlay.cpp`, pytest text-structure tests.

---

### Task 1: Add Shader Text Regressions

**Files:**
- Modify: `tests/test_overlay_backend_shader.py`
- Test: `tests/test_overlay_backend_shader.py`

- [ ] **Step 1: Add failing tests**

```python
def test_overlay_shader_computes_depth_confidence_for_edge_protection():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "struct DepthSample" in source
    assert "confidence" in source
    assert "kDepthConfidenceLow" in source
    assert "kDepthConfidenceHigh" in source
    assert "ApplyConfidenceProtectedParallax" in source
    assert "ParallaxShift(d_final.depth" in source


def test_overlay_debug_depth_cycles_through_multiple_views():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "g_debugDepthMode" in source
    assert "kDebugDepthModeCount" in source
    assert "debugDepthMode" in source
    assert "debug confidence" in source
    assert "debug edge" in source
```

- [ ] **Step 2: Verify tests fail**

Run: `python -m pytest tests/test_overlay_backend_shader.py::test_overlay_shader_computes_depth_confidence_for_edge_protection tests/test_overlay_backend_shader.py::test_overlay_debug_depth_cycles_through_multiple_views -q`

Expected: both tests fail because the shader still exposes only `SampleDepthCohesive` and a boolean-style `g_debugDepth`.

### Task 2: Implement Confidence-Protected Parallax and Debug Views

**Files:**
- Modify: `overlay/overlay.cpp`
- Test: `tests/test_overlay_backend_shader.py`

- [ ] **Step 1: Replace scalar cohesive depth with structured depth sample**

Add `DepthSample` to the HLSL shader string:

```hlsl
struct DepthSample {
    float depth;
    float rawDepth;
    float range;
    float confidence;
};
```

Change `SampleDepthCohesive` so it returns `DepthSample`, computes range from the five depth samples, computes a trimmed mean for depth cohesion, and computes confidence using:

```hlsl
static const float kDepthConfidenceLow  = 0.10;
static const float kDepthConfidenceHigh = 0.30;
float confidence = 1.0 - smoothstep(kDepthConfidenceLow, kDepthConfidenceHigh, dMax - dMin);
```

- [ ] **Step 2: Dampen parallax by confidence**

Add:

```hlsl
float2 ApplyConfidenceProtectedParallax(DepthSample sample, float hz, float sw, float sh, float vd, float eyeX, float eyeY) {
    float confidenceScale = lerp(0.35, 1.0, sample.confidence);
    return ParallaxShift(sample.depth, hz, sw, sh, vd, eyeX, eyeY) * confidenceScale;
}
```

Use this in `main()`:

```hlsl
DepthSample d_final = SampleDepthCohesive(localUV, dCropW, sceneDdx, sceneDdy);
float2 uv_final = localUV + ApplyConfidenceProtectedParallax(d_final, hz, sw, sh, vd, eyeX, eyeY) * fade;
```

- [ ] **Step 3: Convert Ctrl+D debug from boolean to mode cycle**

In C++ state, replace `g_debugDepth` with:

```cpp
static int g_debugDepthMode = 0;
static constexpr int kDebugDepthModeCount = 4;
```

In the hotkey handler, cycle:

```cpp
g_debugDepthMode = (g_debugDepthMode + 1) % kDebugDepthModeCount;
Log("WndProc: debug depth mode %d", g_debugDepthMode);
```

In the constant buffer, pass `(float)g_debugDepthMode` instead of boolean.

- [ ] **Step 4: Add shader debug modes**

In HLSL `main()`, before final scene sampling:

```hlsl
if (debugDepthMode > 0.5 && debugDepthMode < 1.5) {
    return float4(d_final.depth, d_final.depth, d_final.depth, 1);
}
if (debugDepthMode >= 1.5 && debugDepthMode < 2.5) {
    return float4(1.0 - d_final.confidence, d_final.confidence, 0.0, 1);
}
if (debugDepthMode >= 2.5) {
    float edge = saturate((d_final.range - kDepthConfidenceLow) / max(0.001, kDepthConfidenceHigh - kDepthConfidenceLow));
    return float4(edge, edge, edge, 1);
}
```

- [ ] **Step 5: Verify focused tests pass**

Run: `python -m pytest tests/test_overlay_backend_shader.py -q`

Expected: all shader text tests pass.

### Task 3: Build and Full Verification

**Files:**
- Modify: `Glassless3DOverlay.exe` via native build output copy

- [ ] **Step 1: Rebuild overlay**

Run: `& 'C:\Users\coran\AppData\Local\Programs\Python\Python314\Lib\site-packages\cmake\data\bin\cmake.exe' --build 'E:\Glassless 3d\overlay\build_mingw' --target Glassless3DOverlay`

Expected: build exits 0 and prints `Built target Glassless3DOverlay`.

- [ ] **Step 2: Deploy root executable**

Run: `Copy-Item -LiteralPath 'E:\Glassless 3d\overlay\build_mingw\Glassless3DOverlay.exe' -Destination 'E:\Glassless 3d\Glassless3DOverlay.exe' -Force`

Expected: command exits 0.

- [ ] **Step 3: Run full tests**

Run: `python -m pytest tests/ -q`

Expected: all tests pass.

- [ ] **Step 4: Run diagnostics**

Run: `python -m launcher.diagnostics --config "$env:APPDATA\Glassless3D\config.yaml"`

Expected: diagnostics exits 0 and reports no problems.
