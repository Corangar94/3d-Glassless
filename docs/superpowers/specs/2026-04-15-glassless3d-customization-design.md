# Glassless3D Customization System — Design Spec
**Date:** 2026-04-15  
**Approach:** Option A — Big Settings Panel (single Advanced tab, all controls in one place)

---

## Overview

Expand the launcher with an "Advanced" tab that exposes every tunable parameter across four groups: shader tuning, auto-calibration, presets, and tracker calibration. Auto-detect runs once at launch to fill in screen dimensions and optionally measure head distance from the webcam. Named presets are saved to `config.yaml`.

---

## 1. UI Layout

The existing launcher main window gains a new **"Advanced"** tab. A preset dropdown sits at the top. Below it, four collapsible groups:

### 1.1 Presets (top bar)
- Dropdown listing saved preset names
- **Save**, **Load**, **Delete** buttons
- Default preset: `"default"`

### 1.2 Shader Tuning
| Control | Type | Range | Default |
|---|---|---|---|
| Depth curve | Dropdown | Linear / √ (sqrt) / Gamma | sqrt |
| Gamma γ | Slider (enabled when Gamma selected) | 0.3 – 3.0 | 1.0 |
| Strength X | Slider | 0.0 – 5.0 | 1.0 |
| Strength Y | Slider | 0.0 – 5.0 | 1.0 |
| Focus zone radius | Slider | 0.0 – 0.5 (UV) | 0.1 |

### 1.3 Auto-Calibration
| Control | Type | Default |
|---|---|---|
| Screen width cm | Spinbox (float) | 0 = auto |
| Screen height cm | Spinbox (float) | 0 = auto |
| **[Auto-detect screen]** | Button | Runs `detect_screen_cm()` once |
| Head distance cm | Spinbox (float) | 60.0 |
| **[Measure from camera]** | Button | Runs `measure_head_distance()` once |
| Virtual depth cm | Slider | 0 – 200, default 30 |

### 1.4 Tracker Calibration
| Control | Type | Range | Default |
|---|---|---|---|
| Camera FOV° | Dropdown + manual | 50 – 120° | 90° |
| IPD mm | Spinbox | 50 – 80 | 64 |
| Smoothing α | Slider | 0.01 – 1.0 | 0.2 |
| Deadzone mm | Slider | 0 – 30 | 5 |

---

## 2. Data Flow

### 2.1 Shared Memory Layout (v2)

`OverlaySettings` struct expands from 20 bytes to 56 bytes. Format string: `<fffffffffIffI` (Python `struct`).

| Field | Type | Description |
|---|---|---|
| strength_x | float32 | Horizontal parallax multiplier |
| strength_y | float32 | Vertical parallax multiplier |
| virtual_depth_cm | float32 | Virtual screen depth (existing) |
| screen_w_cm | float32 | Physical screen width cm (existing) |
| screen_h_cm | float32 | Physical screen height cm (existing) |
| depth_curve | uint32 | 0=linear, 1=sqrt, 2=gamma |
| depth_gamma | float32 | γ exponent when curve=gamma |
| focus_radius | float32 | UV-space radius of focus tap ring |
| head_dist_cm | float32 | Calibrated head distance |
| camera_fov_deg | float32 | Camera horizontal FOV |
| ipd_mm | float32 | Inter-pupillary distance |
| smoothing_alpha | float32 | EMA smoothing factor for tracker |
| deadzone_mm | float32 | Minimum head movement threshold |
| version | uint32 | Bumped to 2 |

**Total:** 56 bytes.

### 2.2 CBuf (HLSL, overlay.cpp)

Grows from 8 floats to 11:

```hlsl
cbuffer CB : register(b0) {
    float headX, headY, headZ, strengthX;
    float strengthY, screenW, screenH, virtualDepth;
    float debugDepth, depthGamma, focusRadius;
    float depthCurve;  // 0=linear,1=sqrt,2=gamma (packed as float)
};
```

Shader applies:
```hlsl
float rawD = DepthTex.Sample(SceneSmp, i.uv).r;
float depth;
if (depthCurve < 0.5)      depth = rawD;                        // linear
else if (depthCurve < 1.5) depth = sqrt(rawD);                  // sqrt
else                        depth = pow(rawD, depthGamma);       // gamma

// focus: weighted ring using focusRadius
// parallax: headX/hz * depthDelta * vd / sw * strengthX  (X axis)
//           headY/hz * depthDelta * vd / sh * strengthY  (Y axis)
```

### 2.3 Tracker side

`face_tracker.py` reads from `OverlaySettings`:
- `smoothing_alpha` → replaces hardcoded `α=0.2` in EMA
- `deadzone_mm` → if head displacement < deadzone, clamp delta to 0
- `camera_fov_deg` + `ipd_mm` → improve head-position-to-cm conversion
- `head_dist_cm` → used as fallback if camera measurement unavailable

---

## 3. New Files

### 3.1 `launcher/calibration.py`

```python
def detect_screen_cm() -> tuple[float, float]:
    """
    Uses ctypes GetMonitorInfo + GetDpiForMonitor to return
    physical (width_cm, height_cm) of the primary monitor.
    Falls back to (0.0, 0.0) if DPI API unavailable.
    """

def measure_head_distance(ipd_mm: float = 64.0) -> float:
    """
    Grabs one frame from the default webcam, runs MediaPipe
    face mesh, measures inter-eye pixel distance, converts to
    head distance in cm using known IPD.
    Returns measured distance or 60.0 as fallback.
    """
```

No new dependencies — uses `ctypes` (stdlib) and `mediapipe` (already required by tracker).

### 3.2 `launcher/presets.py`

```python
def list_presets(config_path: str) -> list[str]: ...
def save_preset(config_path: str, name: str, settings: dict) -> None: ...
def load_preset(config_path: str, name: str) -> dict: ...
def delete_preset(config_path: str, name: str) -> None: ...
```

Presets stored under a `presets:` top-level key in `config.yaml`:
```yaml
presets:
  default:
    strength_x: 1.0
    strength_y: 1.0
    depth_curve: sqrt
    ...
  wow_raiding:
    strength_x: 1.5
    ...
```

---

## 4. Modified Files

| File | Change summary |
|---|---|
| `tracker/shared_settings.py` | Expand dataclass + struct to v2 (56 bytes) |
| `tracker/face_tracker.py` | Apply smoothing_alpha, deadzone_mm, fov, ipd from settings |
| `launcher/mainwindow.py` | Add Advanced tab with 4 groups; wire buttons to calibration.py |
| `overlay/overlay.cpp` | Expand CBuf to 12 floats (headX/Y/Z, strengthX/Y, screenW/H, virtualDepth, debugDepth, depthGamma, focusRadius, depthCurve); update shader depth curve + per-axis strength + focus_radius; read new SHM fields |

---

## 5. Error Handling

- `detect_screen_cm()` failure → fields stay at 0 (existing auto-detect path already handles this)
- `measure_head_distance()` failure → field stays at current value, show status label "Measurement failed — using manual value"
- SHM version mismatch (overlay sees v1, launcher writes v2) → overlay reads with v1 offsets (graceful degradation) until rebuilt

---

## 6. Out of Scope

- Per-game auto-switching presets (foreground window detection) — deferred
- Continuous adaptive depth (always-on head distance estimation) — deferred
- Floating in-game HUD — deferred
