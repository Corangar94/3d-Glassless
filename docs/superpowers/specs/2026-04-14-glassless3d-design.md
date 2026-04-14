# Glassless3D — Design Spec
**Date:** 2026-04-14  
**Status:** Approved

---

## Overview

A glasses-free 3D gaming overlay for Windows PC games. A webcam tracks the player's head position in real-time; a ReShade shader uses that data plus the game's native depth buffer to warp each frame so that objects at different depths shift with head movement — creating the illusion of looking through a window into a 3D world. No glasses required. No special monitor required.

Primary target: World of Warcraft (DirectX 11). Designed so any DirectX game can be added via a JSON profile.

---

## Architecture

### Pipeline

```
Python Tracker → Shared Memory → C++ ReShade Addon → HLSL Shader → Game Frame
```

### Components

**1. Python Head Tracker (`tracker/`)**

- Uses MediaPipe FaceMesh to detect 468 facial landmarks from webcam
- Derives head X/Y/Z position in centimetres relative to screen centre
- Applies Kalman filter smoothing to eliminate jitter
- Runs at 60 fps in a background loop
- Writes `{x, y, z, timestamp}` (16 bytes) to Windows Named Shared Memory: `Global\G3D`
- Configurable via `config.yaml`: camera index, screen dimensions, smoothing factor, IPD
- Per-game calibration profiles loaded from `profiles/<game>.json`
- `calibration.py`: interactive CLI tool — user measures their physical screen width/height in cm and sets their IPD; writes values to `config.yaml`

**2. Shared Memory Bridge**

- Windows Named Shared Memory segment: `Global\G3D`
- Structure: `struct HeadData { float x; float y; float z; uint32_t timestamp; }`
- Written by Python tracker at ~60 fps
- Read by the C++ addon once per rendered frame
- Latency: ~0.1 ms (no polling, no disk I/O)

**3. C++ ReShade Addon (`addon/`)**

- Minimal DLL (~150 lines) loaded automatically by ReShade from the game directory
- On each `ReShade::addon_event::present` event: opens shared memory, reads `HeadData`, sets three shader uniforms: `g3d_HeadX`, `g3d_HeadY`, `g3d_HeadZ`
- Graceful fallback: if shared memory is unavailable (tracker not running), uniforms default to `(0, 0, 60)` — effect disabled, no crash
- Built with MSVC via `build.bat` (one-click); output: `Glassless3D.addon`

**4. HLSL ReShade Shader (`shaders/Glassless3D.fx`)**

- Inputs: color buffer, depth buffer, `g3d_HeadX/Y/Z`, and user-tunable ReShade UI knobs
- Core algorithm per pixel:
  ```hlsl
  float depth = LineariseDepth(depthTex.Sample(uv), nearClip, farClip);
  float2 offset = float2(g3d_HeadX, g3d_HeadY)
                  * (1.0 - depth / convergenceDist)
                  * strength / screenSize;
  float4 color = colorTex.Sample(uv + offset);
  ```
- Objects at `convergenceDist` appear "on" the screen surface
- Objects closer than convergence pop out toward the viewer
- Objects beyond convergence recede into the screen
- Edge fill: replicate border pixels (avoids black edges)
- Depth discontinuity softening: bilateral blur at depth edges
- All parameters exposed in ReShade UI and overridable per game profile

**5. Game Profiles (`profiles/`)**

JSON files containing per-game ReShade tuning:
- `depthBufferType`: `linear`, `reversed`, or `logarithmic`
- `nearClip`, `farClip`: depth range for linearisation
- `convergenceDist`: default convergence distance in world units
- `effectStrength`: default warp multiplier
- `depthMultiplier`: rescale depth range if needed

Ships with `wow.json` and `default.json`.

**6. Setup Script (`setup.py`)**

- Detects WoW install path (registry + common locations)
- Uses a bundled ReShade binary (pinned version 5.9, included in repo under `vendor/`)
- Installs ReShade into the game directory
- Copies `Glassless3D.addon`, `Glassless3D.fx` to correct ReShade directories
- Writes initial `ReShade.ini` with the shader enabled

---

## 3D Warp Algorithm Detail

The effect is a **depth-based parallax warp** — a 2D image-space approximation of off-axis projection reprojection.

For each screen pixel at UV coordinate `uv`:

1. **Sample depth**: read the game's depth buffer at `uv`
2. **Linearise**: convert raw depth to world-space distance using near/far clip planes and the game's projection type (linear, reversed, log)
3. **Compute parallax offset**:
   - Head X and Y are the physical displacement of the viewer's eyes from screen centre (in cm)
   - The offset is proportional to how far the object is from the convergence plane
   - Formula: `offset = headXY * (1 - depth/convergence) * strength`
   - At `depth == convergence`: offset is zero (object appears "on screen")
   - At `depth < convergence`: offset is positive (object pops out)
   - At `depth > convergence`: offset is negative (object recedes)
4. **Sample color**: look up `colorBuffer[uv + offset]`
5. **Edge handling**: clamp and replicate border for out-of-bounds samples

**Key tuning knob**: `convergenceDist` — sets the depth plane that appears to sit at the physical screen surface. Per-game profiles set this. Users can also adjust live via the ReShade UI overlay.

---

## Data Flow

```
Webcam frame
  → MediaPipe FaceMesh (Python)
    → head X/Y/Z in cm
      → Kalman filter
        → write to Global\G3D (16 bytes, ~60 fps)
          → C++ addon reads on each game frame
            → sets g3d_HeadX/Y/Z uniforms
              → HLSL shader reads uniforms + depth buffer + color buffer
                → outputs warped color per pixel
                  → player sees glassless 3D
```

---

## Error Handling

| Failure | Behaviour |
|---|---|
| Tracker not running | Addon defaults uniforms to `(0,0,60)` — shader passthrough, no crash |
| No face detected | Python holds last known position for 500 ms, then fades to centre |
| Depth buffer unavailable | Shader detects flat depth, disables warp, logs to ReShade log |
| Game not supported | `default.json` profile applied; user prompted to tune convergence |
| Build fails | `build.bat` outputs clear MSVC error; fallback: pre-built `.addon` in releases |

---

## Project Structure

```
Glassless 3d/
├── tracker/
│   ├── main.py              # entry point, runs tracking loop
│   ├── face_tracker.py      # MediaPipe head pose estimation
│   ├── shared_memory.py     # Windows Named Shared Memory (mmap)
│   ├── smoother.py          # Kalman filter smoothing
│   ├── calibration.py       # screen dimension + IPD calibration
│   └── requirements.txt     # mediapipe, opencv-python, numpy, pywin32
├── addon/
│   ├── Glassless3D.cpp      # ReShade addon, ~150 lines
│   ├── CMakeLists.txt
│   └── build.bat            # one-click MSVC build
├── shaders/
│   ├── Glassless3D.fx       # main effect shader (HLSL)
│   └── Glassless3D.fxh      # shared structs and helpers
├── profiles/
│   ├── wow.json             # WoW depth + convergence settings
│   └── default.json         # generic fallback
├── docs/
│   └── superpowers/specs/
│       └── 2026-04-14-glassless3d-design.md
├── setup.py                 # auto-installs ReShade + addon into game
├── config.yaml              # screen size, IPD, camera index, smoothing
└── README.md
```

---

## Dependencies

| Component | Dependency | Notes |
|---|---|---|
| Python tracker | `mediapipe`, `opencv-python`, `numpy`, `pywin32` | pip install |
| C++ addon | MSVC 2022, ReShade SDK headers | free, bundled in repo |
| Shader | ReShade 5.9+ | free, auto-installed by setup.py |
| Game integration | DirectX 11 or 12 | WoW uses DX11 by default |

---

## Build Order

1. **Tracker** — `pip install -r tracker/requirements.txt` then `python tracker/main.py` — runs immediately, no build step
2. **Shader** — copy `shaders/` into ReShade `Shaders/` folder, enable in ReShade UI — tweak live
3. **Addon** — run `addon/build.bat` once, copy `.addon` file to game dir — rarely touched after

---

## Non-Goals

- No support for macOS or Linux (Windows shared memory and ReShade are Windows-only)
- No VR/stereoscopic output — this is a flat-monitor parallax effect
- No AI depth estimation fallback — real depth buffer only (ensures quality)
- No per-eye rendering — the warp is a post-process, not true stereo rendering
