# Glassless3D — Architecture Reference

> Last updated: 2026-04-17. Authoritative source is the source code;
> update this document whenever a subsystem changes.

---

## 1. System Overview

Glassless3D's primary supported runtime is the standalone desktop overlay. The
launcher starts and supervises the tracker and overlay, while the overlay lazily
reads head pose and tuning data from shared memory each frame:

`tracker -> G3D`, `launcher -> G3D_Settings`, `Glassless3DOverlay.exe -> reads both`

The ReShade addon and `FT_SharedMem` path remain in the repository as
experimental integrations. They are not the default onboarding or support path.

Display backend status is represented in code by `tracker.display_backends`.
The current primary backend is `desktop_overlay`. `stereo_autostereo` and
`lightfield_quilt` are registered as experimental capability targets so future
work has stable IDs. The same module defines tested output layout contracts:
`desktop_overlay` is a 1x1 single-view output, `stereo_autostereo` is a 2x1
two-view output, and `lightfield_quilt` is a 9x5 45-view quilt with normalized
view offsets. Renderer implementations still land behind those contracts.
`overlay.display_backend` in `config.yaml` selects the intended backend for
diagnostics/support artifacts; unknown IDs make diagnostics `NOT READY`.

Three processes collaborate via Windows Named Shared Memory in the primary flow:

```
┌─────────────────────────────────────────────────────────┐
│                    LAUNCHER PROCESS                      │
│  PySide6 GUI  (launcher/app.py → mainwindow.py)         │
│                                                          │
│  ┌──────────────────────┐  ┌───────────────────────┐    │
│  │  TrackerThread        │  │  OverlayProcess        │   │
│  │  (QThread, in-proc)   │  │  (subprocess wrapper)  │   │
│  └──────────┬───────────┘  └───────────┬────────────┘   │
│             │                          │                  │
│      writes │ G3D (16 B)        reads  │ G3D_Settings    │
│      writes │ FT_SharedMem (compat)    │ written by GUI   │
│             │                          │                  │
└─────────────┼──────────────────────────┼─────────────────┘
              │                          │
   ┌──────────▼──────────┐   ┌───────────▼─────────────────┐
   │   TRACKER PROCESS   │   │      OVERLAY PROCESS         │
   │ optional standalone │   │  Glassless3DOverlay.exe      │
   │  tracker/main.py    │   │  D3D11 + DXGI + ONNX Runtime │
   └─────────────────────┘   └──────────────────────────────┘
```

### Named Shared Memory Channels

| Name | Size | Direction | Contents |
|------|------|-----------|---------|
| `G3D` | 16 bytes | Tracker → Overlay | `float x, y, z` (cm) + `uint32 timestamp_ms` |
| `G3D_Settings` | 56 bytes | Launcher GUI → Overlay | All tuning parameters (see Section 5) |
| `FT_SharedMem` | 92 bytes | Tracker → ReShade addon / FreeTrack readers | Compatibility channel for experimental integrations |

The overlay opens both `G3D` and `G3D_Settings` lazily on every frame — neither the tracker nor the GUI need to be running first. The overlay falls back gracefully to default values when either segment is absent.

---

## 2. Tracker Subsystem

**Source files:** `tracker/main.py`, `tracker/face_tracker.py`, `tracker/smoother.py`, `tracker/shared_memory.py`, `tracker/freetrack.py`

### Entry Points

There are two ways the tracker runs:

1. **Embedded in the launcher** — `launcher/tracker_thread.py` (`TrackerThread`) runs the tracking loop inside a `QThread` within the launcher process. This is the normal path when using the GUI.
2. **Standalone** — `tracker/main.py` `main()` runs the same loop as a standalone Python process. Used for headless / tray-only operation.

### Face Detection and Head Pose Estimation

`FaceTracker` (in `face_tracker.py`) wraps the MediaPipe Tasks `FaceLandmarker` API:

- Runs in `RunningMode.IMAGE` (synchronous, one frame at a time)
- Detects 478 facial landmarks using the `face_landmarker.task` model
- GPU delegate tried first, falls back to CPU on failure
- Uses only three landmark indices:
  - `468` (left iris center) and `473` (right iris center) for IPD measurement
  - `1` (nose tip) for XY position

**Z estimation (depth from IPD):**
```
focal_px = image_width / (2 * tan(fov_deg / 2))
z_cm     = focal_px * real_ipd_cm / ipd_px
```
Where `ipd_px` is the pixel distance between iris centers.

**XY estimation (offset from screen center):**
```
x_cm = (nose_x_norm - 0.5) * screen_width_cm
y_cm = -((nose_y_norm - 0.5) * screen_height_cm)   # Y flipped: up is positive
```

### Coordinate System

All positions are in centimetres, with the screen center as the origin:
- **+X** = right
- **+Y** = up (camera image Y is flipped)
- **+Z** = toward the camera (distance from screen; always positive, nominal ~60 cm)

### Kalman Smoother

`HeadSmoother` (in `smoother.py`) runs three independent `KalmanFilter1D` instances — one per axis. The Z filter is pre-seeded at 60.0 cm. Parameters `process_noise` (q) and `measurement_noise` (r) are set from `config.yaml`; the launcher can also override `r` live via the `G3D_Settings` `smoothing_alpha` field.

Hold logic (in both `TrackingLoop` and `_SignallingLoop`):
- When no face is detected but `hold_ms` hasn't elapsed, the last smoothed position is replayed (no filter update)
- After `hold_ms` (default 500 ms), position resets to `(0.0, 0.0, 60.0)`

### Dual SHM Writers (CRITICAL)

Every tracker entry point writes to **both** shared memory channels on every frame. This is mandatory. If either writer is absent, one consumer sees zeros.

```python
# From tracker/main.py — the _MultiWriter pattern
class _MultiWriter:
    def write(self, x, y, z):
        ft_writer.write(x=x, y=y, z=z)    # FT_SharedMem (experimental compatibility channel)
        g3d_writer.write(x=x, y=y, z=z)   # G3D (overlay's primary channel)
```

`TrackerThread` in the launcher does the same: it holds both `FreetracWriter` and `SharedMemoryWriter` and calls both on every iteration.

---

## 3. Overlay Subsystem

**Source files:** `overlay/overlay.cpp`, `overlay/depth_infer.h`, `overlay/depth_infer.cpp`

### Initialization Sequence

1. **Screen size autodetect** — tries EDID via `GetDeviceCaps(HORZSIZE/VERTSIZE)`. Rejects the bogus `320×240 mm` driver default; falls back to DPI-based estimate. Result stored in `g_autoScreenW/H`.

2. **Window creation** — `WS_POPUP` (no title bar or border), positioned at `(0,0)` covering the primary monitor.

3. **Click-through** — Cross-process click-through requires both flags together:
   ```
   WS_EX_LAYERED | WS_EX_TRANSPARENT
   ```
   followed by `SetLayeredWindowAttributes(LWA_ALPHA, 255)` (fully opaque; D3D content renders through). `WS_EX_TRANSPARENT` alone only suppresses same-thread hit-testing.

4. **Self-exclusion from capture** — `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` hides the overlay window from DXGI Desktop Duplication, preventing the black feedback loop.

5. **D3D11 device + swap chain** — `DXGI_SWAP_EFFECT_DISCARD` (BitBlt model) is used instead of `FLIP_DISCARD` because `FLIP_DISCARD` fails with certain layered-window flag combinations on some drivers.

6. **DXGI Desktop Duplication** — `IDXGIOutput1::DuplicateOutput()` on adapter output 0 (primary monitor). Creates a `g_capTex` (`DXGI_FORMAT_B8G8R8A8_UNORM`) at full capture resolution.

7. **Depth inference init** — `DepthInferencer::init()` is called after the capture texture is ready so it knows the capture resolution. If the model file is missing or init fails, the overlay falls back to a 1×1 `R16F=0.0` texture; the shader still runs with zero parallax.

8. **Settings SHM** — `G3D_Settings` is opened read-only. Retried every frame if not yet present.

9. **Hotkeys registered:**
   - `Ctrl+Shift+G` — quit
   - `Ctrl+D` — toggle depth debug mode
   - `Ctrl+Shift+S` — save screenshot BMP next to the exe

### Per-Frame Loop

```
Frame()
  ├─ TryAttachShm()          // lazy open G3D
  ├─ TryAttachSettings()     // lazy open G3D_Settings
  ├─ ApplySettings()         // merge CLI > GUI SHM > autodetect → g_screenW, g_strength, etc.
  ├─ Read G3D SHM → hx, hy, hz
  ├─ AcquireNextFrame(16ms timeout)
  │    ├─ TIMEOUT: skip capture, continue with last frame
  │    ├─ ACCESS_LOST/INVALID_CALL: ResetDuplication() and return
  │    └─ SUCCESS:
  │         ├─ CopyResource(g_capTex, acquired_texture)
  │         └─ g_depth->run(g_capTex)  ← async: hands frame to worker thread
  ├─ Update cbuffer {hx,hy,hz, strengthX,Y, screenW,H, virtualDepth,
  │                  debugDepth, depthGamma, focusRadius, depthCurve}
  ├─ Draw fullscreen quad (4 vertices, TRIANGLESTRIP, no index buffer)
  │    t0 = g_srv        (captured desktop BGRA8)
  │    t1 = depth_srv    (518×518 R16F depth, or 1×1 fallback)
  └─ Present(0, 0)
```

**Settings priority** (applied every frame in `ApplySettings()`):
1. CLI arguments (highest priority; only override if provided)
2. `G3D_Settings` SHM (live GUI tuning)
3. Autodetected / hardcoded defaults (lowest)

### Parallax Pixel Shader

The full shader source is in `overlay.cpp` as embedded HLSL. The critical pixel shader math:

**Depth convention:** `0.0` = near/foreground (HUD, screen surface), `1.0` = far/background (sky, distant terrain). This is the output of `DepthInferencer` after percentile normalization.

**Depth curve** (applied before parallax):
```hlsl
float ApplyCurve(float rawD, float curve, float gamma) {
    if (curve < 0.5) return rawD;          // 0 = linear
    if (curve < 1.5) return sqrt(rawD);   // 1 = sqrt (default)
    return pow(max(rawD, 0.0001), gamma); // 2 = gamma
}
```

**Parallax math (pinhole-camera-through-window model):**
```hlsl
float oz = virtualDepth * depth;           // virtual cm behind screen
                                            //   depth=0 → oz=0 (screen plane, no shift)
                                            //   depth=1 → oz=virtualDepth (far plane, max shift)

float f  = oz / (headZ + oz);              // fraction of eye-offset that appears as UV shift
                                            //   similar-triangles derivation:
                                            //   shift_cm = headX * oz / (headZ + oz)

float2 sampleUV = float2(
    uv.x + (headX / screenW) * f * strengthX,
    uv.y - (headY / screenH) * f * strengthY
);

// UV out-of-bounds: return unshifted pixel.
// This prevents the CLAMP sampler from stretching the screen edge across
// large regions when the head is far to one side ("doubling" artifact).
if (sampleUV.x < 0.0 || sampleUV.x > 1.0 ||
    sampleUV.y < 0.0 || sampleUV.y > 1.0)
    return SceneTex.Sample(smp, uv);       // unshifted fallback

return SceneTex.Sample(smp, sampleUV);
```

All objects shift in the **same direction** as head movement; far objects shift more than near objects. This matches looking through a physical window: if you move left, objects beyond the window appear to shift left, and more-distant objects shift more.

The `strengthX`/`strengthY` multipliers amplify the effect beyond the physically-correct 1:1 ratio.

### Debug Mode (Ctrl+D)

When `g_debugDepth` is true, the shader returns:
```hlsl
float v = 1.0 - depth;
return float4(v, v, v, 1.0);
```
Near objects appear **bright**, far objects appear **dark**. This is standard disparity / inverse-depth display convention.

---

## 4. Depth Inference Pipeline

**Source files:** `overlay/depth_infer.h`, `overlay/depth_infer.cpp`

### Model

- **Model:** Depth Anything V2 Small — `depth_anything_v2_small_fp16.onnx`
- **"fp16" means internal weights only.** The ONNX input and output tensors are `float32`. The DirectML EP handles the fp16 conversion internally. Always feed and read `float32`.
- **Input shape:** `[1, 3, 518, 518]` NCHW (RGB, ImageNet-normalized)
- **Output shape:** `[1, 518, 518]` or `[1, 1, 518, 518]` — both handled
- **Backend:** ONNX Runtime + DirectML execution provider (GPU, device 0)

### Letterbox Preprocessing (Critical for Ultrawide Monitors)

Naively squashing the full desktop to 518×518 would distort a 5120×1440 (32:9) monitor by a 3.56× horizontal-vs-vertical factor, destroying depth map quality.

Instead, the capture is scaled uniformly so the **larger dimension** fits exactly within 518 pixels, then centered in the 518×518 frame. The bars are filled with `0.0f` — which equals the ImageNet-normalized mean grey, a neutral value for the model.

```
Ultrawide example (5120×1440):
  scale   = min(518/5120, 518/1440) = 0.3597
  lb_w    = round(5120 * 0.3597) = 518   (full width, no bars)
  lb_h    = round(1440 * 0.3597) = 145
  lb_off_x = (518 - 518) / 2 = 0
  lb_off_y = (518 - 145) / 2 = 186
  → content occupies rows 186..330 of the 518×518 frame
  → rows 0..185 and 331..517 are padding (0.0f)
```

`preprocess()` performs nearest-neighbor downsampling from the captured full-res `BGRA8` staging texture directly into the NCHW float32 tensor, applying ImageNet normalization per channel:
```
r_norm = (r/255 - 0.485) / 0.229
g_norm = (g/255 - 0.456) / 0.224
b_norm = (b/255 - 0.406) / 0.225
```

### Async Worker Thread

The ORT inference call (`session->Run(...)`) takes roughly 100 ms on a mid-range GPU. Blocking `Present()` for 100 ms would cap the overlay at 10 fps.

Architecture:
- `run()` (called from the main render thread) does the fast `GPU→CPU` staging map + `preprocess()`, then hands the `float32` tensor to the worker thread via a mutex-guarded swap and returns immediately.
- The **worker thread** runs `session->Run()`, then `postprocess()`, and publishes the result back.
- The main thread drains any finished result (an fp16 upload buffer) from the worker on the **next** call to `run()` and issues `UpdateSubresource` to the depth texture.

**Frame-drop policy:** If the worker is still busy when a new frame arrives, the new preprocessed input **overwrites** the pending input. The worker always processes the freshest available input. This prevents a growing backlog and keeps latency bounded at ~100 ms (one inference cycle), never more.

### Postprocessing Pipeline (Worker Thread)

After `session->Run()` returns a raw float32 depth map:

**Step 1 — Percentile normalization (content region only):**
- Collects a 1/4 subsample of depth values from the letterbox content region only (skipping the grey padding bars, which would skew the range)
- Computes the 2nd and 98th percentile using `std::nth_element` (O(n))
- Normalizes: `v = clamp((raw - p2) / (p98 - p2), 0, 1)`

Previous approach (per-frame min/max) caused visible "pulsing": a single bright or dark outlier pixel shifted the entire scene's depth.

**Step 2 — EMA temporal smoothing:**
```cpp
constexpr float kAlpha = 0.2f;
new_norm[i] = kAlpha * new_norm[i] + (1.0f - kAlpha) * prev_norm[i];
```
New frame weighted at 20%, prior history at 80%. This trades ~400 ms lag (4 inference cycles at 10 Hz) for substantially reduced "watery shimmer" from frame-to-frame depth flicker at object edges.

`prev_norm` is saved **before** the spatial filter below, so blur does not compound across frames into an increasingly smeared map.

**Step 3 — 3×3 median filter:**
Applied to remove salt-and-pepper outliers from the model's raw predictions.

Why median rather than Gaussian blur:
- Gaussian blur leaks foreground depth into background across edges, producing a "watery halo" around foreground silhouettes.
- A max/dilation filter pushes foreground depth onto adjacent background pixels; those pixels then get warped with foreground parallax, producing a ghost copy of each foreground edge (the "doubling" artifact).
- Median preserves edge positions exactly: the median of a 3×3 patch straddling an edge is determined by whichever side has 5+ pixels, leaving the edge where the model placed it.

**Step 4 — Pack to fp16:**
The normalized float32 values are packed to `uint16_t` (IEEE 754 half-precision) for upload to the `R16F` GPU texture via a custom software `float_to_half()` (round-to-nearest-even). The GPU-side texture is updated with `UpdateSubresource`.

---

## 5. Shared Memory Layout

### G3D (Head Pose)

**Named object:** `G3D`  
**Size:** 16 bytes  
**Format:** `struct HeadPose { float x; float y; float z; uint32_t ts; };` (packed, little-endian)

| Offset | Type | Field | Notes |
|--------|------|-------|-------|
| 0 | float32 | x | cm, right positive |
| 4 | float32 | y | cm, up positive |
| 8 | float32 | z | cm, distance from screen (positive) |
| 12 | uint32 | ts | Monotonic clock in ms, lower 32 bits |

Python format string: `"<fffI"` (16 bytes)

### G3D_Settings (Live Tuning)

**Named object:** `G3D_Settings`  
**Size:** 60 bytes  
**Owner:** Launcher `SharedSettingsWriter` (one writer, created when `MainWindow` opens)  
**Readers:** Overlay `overlay.cpp` (reads every frame), `TrackerThread` (reads per frame for `smoothing_alpha` and `deadzone_mm`)

Python format string: `"<fffffIfffffffII"` (60 bytes)

| Offset | Type | Field | Default | Notes |
|--------|------|-------|---------|-------|
| 0 | float32 | strength_x | 1.0 | Horizontal parallax multiplier |
| 4 | float32 | strength_y | 1.0 | Vertical parallax multiplier |
| 8 | float32 | virtual_depth_cm | 30.0 | Far-plane virtual distance in cm |
| 12 | float32 | screen_w_cm | 0.0 | 0 = overlay autodetects |
| 16 | float32 | screen_h_cm | 0.0 | 0 = overlay autodetects |
| 20 | uint32 | depth_curve | 1 | 0=linear, 1=sqrt, 2=gamma |
| 24 | float32 | depth_gamma | 1.0 | Exponent when depth_curve=2 |
| 28 | float32 | focus_radius | 0.1 | Reserved; unused in current shader |
| 32 | float32 | head_dist_cm | 60.0 | Nominal head distance (informational) |
| 36 | float32 | camera_fov_deg | 90.0 | Camera horizontal FOV |
| 40 | float32 | ipd_mm | 64.0 | Inter-pupillary distance |
| 44 | float32 | smoothing_alpha | 0.1 | Kalman measurement noise r |
| 48 | float32 | deadzone_mm | 5.0 | XY deadzone radius in mm |
| 52 | uint32 | display_backend | 0 | 0=desktop, 1=stereo, 2=quilt |
| 56 | uint32 | version | monotonic | Incremented on every write |

The overlay's `Settings` struct in `overlay.cpp` must stay byte-for-byte identical to this layout. The `#pragma pack(push, 1)` directive is required.

### FT_SharedMem (FreeTrack Protocol)

**Named object:** `FT_SharedMem`  
**Size:** 92 bytes  
**Format:** matches opentrack `fttypes.h` `FTData`

Python format string: `"<Iii6f6f8f"` (92 bytes)

| Offset | Type | Field |
|--------|------|-------|
| 0 | uint32 | DataID (sequence counter) |
| 4 | int32 | CamWidth |
| 8 | int32 | CamHeight |
| 12 | float32 | Yaw |
| 16 | float32 | Pitch |
| 20 | float32 | Roll |
| 24 | float32 | X (cm) |
| 28 | float32 | Y (cm) |
| 32 | float32 | Z (cm) |
| 36–59 | 6× float32 | Raw Yaw/Pitch/Roll/X/Y/Z (unused, zero) |
| 60–91 | 8× float32 | Tracking points X1/Y1..X4/Y4 (unused, zero) |

The tracker only writes `DataID`, `X`, `Y`, and `Z`; all other fields are zeroed. The experimental ReShade addon reads `DataID` (offset 0) and `X/Y/Z` (offsets 24/28/32).

---

## 6. Launcher Subsystem

**Source files:** `launcher/app.py`, `launcher/mainwindow.py`, `launcher/tracker_thread.py`, `launcher/overlay_process.py`, `launcher/wizard.py`

### First-Run Detection

`app.py` checks whether `%APPDATA%\Glassless3D\config.yaml` exists. If not, it runs `SetupWizard` (from `launcher/wizard.py`) before showing the main window. The wizard guides the user through:
- Downloading the ONNX depth model
- Preparing the standalone overlay runtime
- Initial screen-size calibration

### Main Window (mainwindow.py)

`MainWindow` (PySide6 `QMainWindow`) is frameless, always-on-top, and draggable. It has two tabs:

- **Tracker tab** — live camera preview, X/Y/Z readout tiles, Start/Stop button
- **Advanced tab** — scrollable settings for shader tuning, screen calibration, tracker calibration, preset management

On construction, `MainWindow` creates a `SharedSettingsWriter` and writes the initial `OverlaySettings` (loaded from `config.yaml` overlay section) immediately. From that point, any slider or spin-box change calls `_on_settings_change()` → `_settings_writer.write()`, pushing the new settings to `G3D_Settings` SHM in real time.

Settings priority (effective values, resolved each frame by the overlay):
```
CLI args  >  G3D_Settings SHM  >  autodetect  >  hardcoded defaults
```

### TrackerThread (tracker_thread.py)

`TrackerThread` is a `QThread` that runs `_SignallingLoop` in the background. It emits three Qt signals:
- `position_updated(float, float, float)` — current smoothed x/y/z
- `frame_ready(bytes)` — JPEG-encoded camera frame for the preview widget
- `status_changed(str)` — `"tracking"`, `"hold"`, `"paused"`, or `"error"`

`_SignallingLoop.run()` is the per-frame inner loop. It reads `G3D_Settings` on every detection to pick up live `smoothing_alpha` (Kalman r) and `deadzone_mm` changes. It applies a deadzone: XY movements smaller than `deadzone_mm / 10.0` cm are suppressed (Z always passes through). Both `FreetracWriter` and `SharedMemoryWriter` are written on every frame.

### OverlayProcess (overlay_process.py)

`OverlayProcess` is a thin subprocess wrapper around `Glassless3DOverlay.exe`. It:
- Searches for the exe in the project root, then dev build directories
- Launches with `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` so it survives launcher console closure
- Sets CWD to the project root so the overlay's `models/` directory search works
- Terminates (then kills if needed) on `stop()`

The overlay is started when tracking starts and stopped when tracking stops or the launcher closes.

---

## 7. Experimental ReShade Backend

**Source files:** `addon/Glassless3D.cpp`, `shaders/Glassless3D.fx`

The ReShade addon is retained for opt-in experimentation with process-injected
game rendering paths. It injects into a game's D3D rendering pipeline as a
ReShade effect plugin, reading head pose from `FT_SharedMem` (the FreeTrack
shared memory) rather than `G3D`.

**Why it exists:** DXGI Desktop Duplication cannot capture exclusive-fullscreen games. The ReShade addon runs inside the game's process and applies the parallax warp as a post-process effect directly on the game's back buffer, bypassing the capture limitation.

**How it works:**

1. The addon registers a `on_begin_effects` callback via `reshade::register_event`.
2. On each frame, it reads `FT_SharedMem` lazily (opens the mapping on first access).
3. It resolves the uniform variables `g3d_HeadX`, `g3d_HeadY`, `g3d_HeadZ` in `Glassless3D.fx` and sets their values from the tracker's `X/Y/Z` fields.
4. ReShade then runs `Glassless3D.fx` with the current head pose embedded, applying the same parallax shader logic.

**Policy note:** Protected or multiplayer titles may disable depth access or
treat injected third-party tooling as unsupported. World of Warcraft is a later
feasibility gate, not a default target.

The tracker must be running to populate `FT_SharedMem`. When it is not, head position defaults to `(0, 0, 60)` — a neutral no-op with zero parallax.

---

## 8. Known Issues and Sharp Edges

### Ultrawide Aspect Ratio and Letterboxing
Squashing the full desktop to 518×518 without letterboxing distorts a 32:9 (5120×1440) monitor by 3.56× horizontally relative to vertically. This destroys depth map quality: what the model sees as "tall and narrow" features are actually wide and flat. The letterbox preprocessing in `depth_infer.cpp` is mandatory and must be preserved when modifying the depth pipeline. See Section 4 for the exact math.

### UV Clamping "Doubling" Artifact
If the parallax shift pushes `sampleUV` outside `[0, 1]`, and the sampler is in `CLAMP` mode, it stretches the screen edge across potentially hundreds of pixels as the head moves far to one side. The fix is the out-of-bounds check in the pixel shader: if `sampleUV` is outside `[0, 1]`, return the unshifted pixel. This check must not be removed or replaced with a simple `saturate()` call.

### Both SHM Writers Are Required
Every tracker code path must write to both `G3D` and `FT_SharedMem`. Omitting either means one consumer (overlay or experimental ReShade addon) sees zeros. This applies to both `tracker/main.py` (standalone) and `launcher/tracker_thread.py` (embedded). See the `_MultiWriter` pattern in Section 2.

### POST_BUILD Copy Can Silently Fail
The CMake `POST_BUILD` command that copies `Glassless3DOverlay.exe` to the project root can fail silently (e.g., if the file is locked by a running instance). After rebuilding the overlay, verify that the root-level `.exe` was actually updated by checking its timestamp before running.

### WoW / dxgi Compatibility Note
World of Warcraft's DX12 renderer requires the ReShade proxy DLL to be named
`dxgi.dll`. Using `d3d11.dll` or `d3d12.dll` will fail silently or crash.
Blizzard briefly blocked `dxgi.dll` in July 2025 but reverted the block the
same day. Treat this as an experimental compatibility note, not a supported
product path.

### Depth Model I/O is float32, Not fp16
The model file is named `depth_anything_v2_small_fp16.onnx`. The `fp16` refers to the internal weight format only. The ONNX graph's input and output tensors are `float32`. Always feed `float32` to ORT and read `float32` back. The fp16 packing step is in `postprocess()` for upload to the GPU texture — it is not an ORT operation.

### MinGW SAL Annotation Stubs
ORT and DirectML headers use MSVC SAL source annotations (`_Maybenull_`, `_Frees_ptr_opt_`, etc.) that MinGW's `sal.h` does not define. The stubs at the top of `depth_infer.cpp` are required to compile under MinGW/g++. They are harmless under MSVC since MSVC provides these in its own `sal.h`.

### Depth Texture Initial Value
The depth texture is initialized to `0.5` (not `0.0`) before the first inference completes. This produces a mid-depth parallax on the first few frames rather than the far-plane behavior that `0.0` would give. The fallback 1×1 texture used when depth inference is entirely unavailable is set to `0.0`, which puts every pixel at the far plane (maximum parallax, but flat — equivalent to the pre-depth uniform-plane behavior).
