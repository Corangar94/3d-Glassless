# Atomic MediaPipe live calibration

Glassless3D can update the viewer IPD and camera field of view while tracking is running. MediaPipe result callbacks execute asynchronously, so one pose must not read part of the old calibration and part of the new calibration.

## Snapshot boundary

Before reading landmarks or performing pose geometry, each result captures one locked calibration snapshot containing:

- the real interpupillary distance in centimeters;
- the camera horizontal field of view in degrees; and
- the active camera-geometry object.

The callback releases the lock immediately and performs the remaining landmark, rectification, depth, screen-coordinate, and orientation work from those local values. Settings updates therefore remain short while every pose uses one internally consistent calibration generation.

## Transactional updates

`FaceTracker.set_calibration()` parses and validates the complete requested update before acquiring the lock or mutating state.

- A valid IPD and valid FOV commit together.
- An invalid FOV cannot leave a simultaneously supplied IPD partially committed.
- A non-finite IPD cannot leave a simultaneously supplied FOV partially committed.
- Non-positive IPD values retain the historical direct-call behavior and are ignored.
- Bare legacy test doubles or downstream subclasses created without the normal tracker lock retain their existing behavior through a lock-free compatibility path.

## Geometry behavior

The snapshot is used by both pose paths:

- calibrated intrinsics/extrinsics use the snapshot IPD and geometry object; and
- FOV fallback geometry uses the snapshot IPD and FOV for both depth and X/Y reconstruction.

A callback already in progress finishes using the generation it captured. The next callback observes the newly committed values.
