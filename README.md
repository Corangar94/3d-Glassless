# Glassless3D

Glassless3D creates a **single-view, webcam-tracked virtual-window effect** on an ordinary Windows monitor. As the viewer moves, scene layers reproject using head position and depth, producing motion parallax.

An ordinary monitor cannot deliver separate left/right images to the eyes, so this is not binocular stereoscopic 3D. The current target is convincing head-coupled 2.5D for one tracked viewer.

## Current runtime

The desktop backend captures a selected window or display, estimates depth with Depth Anything through DirectML, tracks the viewer with a webcam, and renders a depth-dependent inverse warp.

```powershell
python scripts/bootstrap.py
python -m launcher
```

The bootstrap command builds the native overlay and verifies that `Glassless3DOverlay.exe`, `onnxruntime.dll`, `DirectML.dll`, the face model, and a supported depth model are present. Startup now fails clearly instead of showing a no-op effect when depth is unavailable.

## Controls

- `Ctrl+R`: recenter at the current viewing position
- `Ctrl+D`: cycle depth/confidence debug views
- `Ctrl+Shift+S`: save an overlay screenshot
- `Ctrl+Shift+G`: quit the native overlay

## Direction

1. Stabilize the head-coupled projection, calibration, diagnostics, and packaging.
2. Build a dedicated image/native-scene viewer using off-axis projection and precomputed depth.
3. Use real game depth through a supported ReShade path where appropriate.
4. Keep arbitrary desktop AI-depth conversion as an experimental fallback.

See `docs/HEAD_COUPLED_3D_DIRECTION.md` for the engineering plan and acceptance criteria.
