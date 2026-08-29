# Camera Calibration

Glassless3D can measure webcam intrinsics and align the camera coordinate system to the screen. Stop tracking before running calibration so the calibration child has exclusive access to the webcam.

## Open the wizard

Source checkout:

```powershell
python -m launcher --config config.yaml --calibrate-camera
```

Standalone package:

```powershell
Glassless3D.exe --config config.yaml --calibrate-camera
```

The standalone application reuses its own executable for calibration children, so it does not require a separate Python installation.

## 1. Print the checkerboard

Use **Save checkerboard PNG**, print it at 100% scale, measure one printed square in millimeters, and enter that value in the wizard. The default board uses 9 x 6 inner corners.

## 2. Calibrate the webcam lens

Use **Calibrate webcam lens**. Move and tilt the board around the camera frame, including the center, edges, several distances, and moderate angles. The capture window accepts diverse views automatically and closes when enough samples are collected. Press Esc in that window to cancel.

The calibration estimates focal lengths, principal point, lens distortion, and reprojection error. Glassless3D derives horizontal FOV from the calibrated focal length and synchronizes it to both tracker and overlay runtime settings.

## 3. Align camera to screen

Enter the normal viewing distance and IPD, then use **Align camera to screen**. Sit at that distance, look at screen center, and remain still while samples are collected.

This stores the camera-to-screen translation. The entered viewing distance is also synchronized to the overlay display calibration and legacy head-distance field.

`Ctrl+R` remains the lightweight per-session recenter control. Full calibration describes the physical camera/display relationship; recentering describes the current seated position.

## Saved configuration

Full geometry is saved under `tracking.camera_calibration`. Compatibility values are synchronized to `tracking.camera_fov_deg`, `overlay.camera_fov_deg`, `overlay.head_dist_cm`, and `overlay.display_calibration.viewer_distance_cm`.

Configuration writes use a temporary file followed by atomic replacement so a failed step does not partially rewrite `config.yaml`.
