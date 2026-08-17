# Glassless3D — Project Reference

## 0. Development Workflow

**Always search before implementing.** Use web search (via subagents with WebSearch/WebFetch tools) before writing new algorithms, postprocessing pipelines, or shader techniques. Prior art exists for nearly everything — depth map smoothing, parallax rendering, temporal filtering, artifact suppression. Find it, understand it, then adapt it. Don't reinvent the wheel.

- Spawn a research subagent for any non-trivial technical question
- Check papers, GitHub repos, and shader references (Shadertoy, GLSL sandbox, existing ReShade shaders)
- Prefer adapting a proven approach over writing net-new code

## 1. Project Overview

Glassless3D is a Windows real-time glasses-free 3D parallax system for the desktop. A Python/MediaPipe head tracker reads webcam pose and writes head position (x/y/z in cm) to Windows Named Shared Memory. A standalone C++/D3D11 overlay process captures the full desktop via DXGI Output Duplication, runs Depth Anything V2 ONNX at ~10 Hz to estimate per-pixel scene depth, and warps the captured frame with a HLSL pinhole-camera parallax shader. A PySide6 launcher/wizard handles first-run setup, settings, and subprocess lifecycle.

There are two delivery modes: the standalone `Glassless3DOverlay.exe` (direct D3D11 capture) and a ReShade addon `Glassless3D.addon` (injects into game process). Both read the same shared memory.

## 2. Build Commands

### Overlay (C++)

Toolchain: MinGW-w64 GCC 14.2.0 at `vendor/_mingw64/mingw64/`. Build dir: `overlay/build_mingw/`.

```bat
:: Configure (first time only)
vendor\_mingw64\mingw64\bin\cmake.exe -G "MinGW Makefiles" overlay -B overlay/build_mingw -DCMAKE_BUILD_TYPE=Release

:: Build
vendor\_mingw64\mingw64\bin\cmake.exe --build overlay/build_mingw --config Release
```

After a successful build the POST_BUILD step copies three files to the project root:
- `Glassless3DOverlay.exe`
- `onnxruntime.dll`
- `DirectML.dll`

**IMPORTANT**: Always verify `Glassless3DOverlay.exe` timestamp updated after rebuild. The `cmd /c copy ... & exit 0` POST_BUILD silently eats errors (antivirus lock, running process). Manually copy from `overlay/build_mingw/Glassless3DOverlay.exe` if needed.

### Python packages

```bat
pip install -e ".[dev]"
```

Installs tracker, launcher, and test dependencies from the project root. Alternatively install from requirements files:
- `tracker/requirements.txt` — tracker runtime (mediapipe, numpy, pyyaml)
- `requirements-gui.txt` — launcher (PySide6, wmi)
- `requirements-dev.txt` — dev/test extras

### Bootstrap (first-time dev setup)

```bat
python scripts/bootstrap.py
```

Downloads in order: face landmarker model, ReShade DLL (via 7-Zip), ReShade SDK headers, ONNX Runtime + DirectML NuGet packages, Depth Anything V2 ONNX model, then builds the addon and overlay. Requires 7-Zip on PATH or at the standard install location.

### Tests

```bat
pytest tests/
```

## 3. Run Instructions

| Component | Command | Notes |
|-----------|---------|-------|
| Tracker | `python -m tracker` | Reads webcam; writes G3D + FT_SharedMem |
| Overlay | `Glassless3DOverlay.exe` | Run from project root; needs depth model |
| Launcher GUI | `python -m launcher` | PySide6; first-run wizard then main window |
| Game installer | `python setup.py --game wow` | Copies addon + shaders; updates ReShade.ini |

Config file: `%APPDATA%\Glassless3D\config.yaml` (created by wizard on first run).

Debug key in overlay: `Ctrl+D` toggles depth visualization (near=bright, far=dark, renders `1.0 - depth`).

## 4. Key Architecture

### Tracker (`tracker/`)

```
webcam -> FaceTracker (MediaPipe Tasks) -> HeadSmoother (Kalman) -> _MultiWriter
                                                                         |
                                               +--------------------------+
                                               |                          |
                                    FreetracWriter              SharedMemoryWriter
                                  (FT_SharedMem, 92 bytes)      (G3D, 16 bytes)
                                  FreeTrack protocol        x/y/z float + uint32 ts
```

- `tracker/face_tracker.py` — `FaceTracker` class using MediaPipe Tasks API (mp.solutions removed in 0.10.x). Outputs `HeadPosition(x_cm, y_cm, z_cm)`.
- `tracker/shared_memory.py` — `SharedMemoryWriter("G3D")`: 16-byte struct `<fffI` (x, y, z, timestamp_ms).
- `tracker/freetrack.py` — `FreetracWriter`: FreeTrack protocol SHM for compatibility.
- `tracker/shared_settings.py` — `SharedSettingsWriter/Reader("G3D_Settings")`: 88-byte settings channel (see Section 4 below).
- `tracker/smoother.py` — `HeadSmoother`: Kalman filter for x/y/z.
- `tracker/main.py` — `TrackingLoop` + `main()`. Creates `_MultiWriter` that calls BOTH `ft_writer.write()` AND `g3d_writer.write()` on every frame.

### Overlay (`overlay/`)

- `overlay/overlay.cpp` — Win32 window (`WS_POPUP | WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TRANSPARENT`) + DXGI Desktop Duplication (primary monitor, 5120×1440). Message loop calls `DepthInferencer::run()` then renders with HLSL parallax shader. Reads G3D SHM for head pose; reads G3D_Settings SHM for live tuning.
- `overlay/depth_infer.h` / `overlay/depth_infer.cpp` — `DepthInferencer` class. Async 10 Hz ORT/DirectML inference. Input: 518×518 letterboxed tensor. Output: R16F depth texture (518×518). `depth_srv()` returns the SRV for the shader.
- HLSL constant buffer (20 floats): `headX, headY, headZ, strengthX, strengthY, screenW, screenH, virtualDepth, debugDepth, depthGamma, focusRadius, depthCurve, depthCropX0, depthCropW, depthBlend, displayBackend, ipdCm, stereoLayout, eyeOrder, focusPlaneCm`

### Launcher (`launcher/`)

- `launcher/app.py` — Entry point. First-run detection → `SetupWizard`; otherwise → `MainWindow`.
- `launcher/wizard.py` — PySide6 `QWizard`. First-run setup flow.
- `launcher/mainwindow.py` — Settings panel.
- `launcher/tracker_thread.py` — Manages tracker subprocess.
- `launcher/overlay_process.py` — `OverlayProcess`: spawns/terminates `Glassless3DOverlay.exe`. Search order: project root → `overlay/build_mingw/` → `overlay/build/Release/`.
- `launcher/reshade_install.py` — Installs ReShade DLL into game directory.

### Addon (`addon/`)

ReShade addon variant. Needs ReShade SDK headers in `vendor/reshade/include/`. Build via `addon/CMakeLists.txt`.

### Tests (`tests/`)

pytest. `tests/fake_tracker.py` writes synthetic G3D SHM values — use for end-to-end overlay tests without a webcam.

### G3D_Settings SHM — 88-byte struct `<fffffIfffffffIIIIIIIfI`

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| strength_x | float | 1.0 | |
| strength_y | float | 1.0 | |
| virtual_depth_cm | float | 30.0 | |
| screen_w_cm | float | 0.0 | 0 = overlay autodetect |
| screen_h_cm | float | 0.0 | 0 = overlay autodetect |
| depth_curve | uint32 | 1 | 0=linear, 1=sqrt, 2=gamma |
| depth_gamma | float | 1.0 | |
| focus_radius | float | 0.1 | UV radius for focus ring |
| head_dist_cm | float | 60.0 | |
| camera_fov_deg | float | 90.0 | |
| ipd_mm | float | 64.0 | |
| smoothing_alpha | float | 0.1 | Kalman measurement noise r |
| deadzone_mm | float | 5.0 | |
| display_backend | uint32 | 0 | 0=desktop, 1=stereo, 2=quilt |
| depth_mode | uint32 | 1 | 0=quality, 1=balanced, 2=fast |
| version | uint32 | — | Monotonic counter |
| stereo_layout | uint32 | 0 | 0=full_sbs, 1=half_sbs |
| eye_order | uint32 | 0 | 0=left_right, 1=right_left |
| panel_width_px | uint32 | 0 | 0 = unspecified |
| panel_height_px | uint32 | 0 | 0 = unspecified |
| focus_plane_cm | float | 0.0 | convergence plane behind screen |
| tracking_mode | uint32 | 0 | 0=glassless3d_managed, 1=vendor_managed |

## 5. Critical Gotchas

These have each caused multi-hour debugging sessions.

### 1. POST_BUILD exe copy can silently fail

The cmake POST_BUILD uses `cmd /c copy ... & exit 0` — it always exits 0 even when the copy silently does nothing (antivirus, file lock from running process). **After every overlay rebuild, check that `Glassless3DOverlay.exe` in the project root updated.** If the timestamp is stale, manually copy:

```bat
copy /Y overlay\build_mingw\Glassless3DOverlay.exe Glassless3DOverlay.exe
```

### 2. WoW / ReShade proxy must be `dxgi.dll`

`d3d11.dll` and `d3d12.dll` both fail to inject on WoW DX12. Use `dxgi.dll` as the proxy name when installing ReShade for WoW. Blizzard briefly blocked dxgi in July 2025 but reverted the same day.

### 3. Depth model fp16 = internal weights only

The model is `depth_anything_v2_small_fp16.onnx` — "fp16" refers only to internal weight storage. **Always feed float32 tensors and read float32 output.** ORT rejects fp16 I/O tensors with this model.

### 4. MinGW SAL stub macros

MinGW does not ship Windows SAL annotations. Stub `_Maybenull_`, `_Frees_ptr_opt_`, and related macros before including ORT and DirectML headers. See the top of `overlay/overlay.cpp` for the required stubs.

### 5. Ultrawide aspect ratio — letterbox for depth model

Display is **5120×1440** (3.56:1). The depth model input is 518×518 (1:1). Squashing 5120×1440 directly to 518×518 destroys the depth map — everything looks flat. Must letterbox:

- Scale so the long dimension fills 518 px.
- Center content; pad with `0.0f` (ImageNet-normalized grey).
- Result: content is **518×145** centered at y-offset **186** within the 518×518 tensor.

### 6. UV clamping causes image doubling

If parallax UV shift pushes sampling outside [0, 1], using `saturate()` or `clamp()` stretches the screen edge across many pixels (looks like image doubled or mirrored). Fix: when `sampleUV` is out of [0, 1] bounds, return the unshifted pixel instead of clamping.

### 7. Both SHM writers are required

Every tracker code path must write to **both** `G3D` (via `SharedMemoryWriter`) and `FT_SharedMem` (via `FreetracWriter`). If the FreeTrack writer is absent, some overlay code paths read zeros. The `_MultiWriter` in `tracker/main.py` is the canonical pattern.

### 8. Overlay window style for click-through

Required style: `WS_POPUP | WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TRANSPARENT` + `SetLayeredWindowAttributes(LWA_ALPHA, 255)` + `WDA_EXCLUDEFROMCAPTURE`. This combination is needed for click-through AND to exclude the overlay from its own DXGI capture.

## 6. Model / Asset Locations

| Asset | Path | Source |
|-------|------|--------|
| Depth model | `models/depth_anything_v2_small_fp16.onnx` | bootstrap.py step 5 (HuggingFace) |
| Face landmarker | `models/face_landmarker.task` | bootstrap.py step 1 (MediaPipe CDN) |
| ReShade DLL | project root `ReShade64.dll` | bootstrap.py step 2 (reshade.me) |
| ReShade SDK headers | `vendor/reshade/include/` | bootstrap.py step 3 (GitHub) |
| ONNX Runtime | `vendor/onnxruntime/` | bootstrap.py step 4 (NuGet) |
| DirectML | `vendor/directml/` | bootstrap.py step 4 (NuGet) |
| MinGW toolchain | `vendor/_mingw64/mingw64/` | bootstrap.py (GitHub winlibs) |

Runtime DLLs (`onnxruntime.dll`, `DirectML.dll`) must sit next to `Glassless3DOverlay.exe` — the POST_BUILD step copies them to the project root.

## 7. Log File

`Glassless3DOverlay.exe` writes `overlay.log` next to the exe at runtime.

Key log patterns:

| Pattern | Meaning |
|---------|---------|
| `depth[total=N]` | Depth inference count (expect ~10 Hz) |
| `shm[LIVE]` | Tracker connected and writing fresh data |
| `shm[STALE]` | Tracker not running or crashed |
