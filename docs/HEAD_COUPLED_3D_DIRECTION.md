# Head-Coupled 3D Direction

## Product contract

The ordinary-monitor product is a one-viewer **virtual window** driven by webcam head tracking. Motion parallax is the primary depth cue. True binocular stereo requires glasses or a directional/autostereoscopic panel and is a separate backend.

## Backend order

1. **Image and native-scene viewer:** precompute depth for images or use real geometry, then render with an off-axis camera. This is the primary product milestone.
2. **Supported-game mode:** consume a real game depth buffer where integration is permitted.
3. **Desktop conversion:** estimate temporal video depth and reproject captured content. This remains experimental because monocular depth, disocclusion, and capture latency limit quality.

## Phase 0 invariants

- The visible overlay never claims success without a working depth pipeline.
- Positive viewer motion produces the physically correct virtual-window direction.
- Rest position is explicit and user-controlled (`Ctrl+R`), not continuously absorbed by a moving baseline.
- The XY dead-zone is applied once, after rest subtraction.
- Camera width, height, FPS, FOV, and IPD are real runtime settings.
- Bootstrap verifies the executable, runtime DLLs, and models as one deployable unit.

## Acceptance tests

- A point behind the display moves in the same screen direction as the viewer.
- A point in front of the display moves in the opposite direction.
- Two depth layers move by measurably different amounts.
- Missing depth or runtime DLLs is a blocking error, not a flat fallback.
- Recenter makes current pose neutral on the next valid camera sample.
- A configured camera mode is requested from OpenCV and the actual mode is logged.

## Next implementation milestone

Create a `Glassless3D Viewer` that loads an image, computes depth once, builds a 2.5D mesh or layered-depth representation, fills disocclusions, and renders it with an asymmetric off-axis projection derived from the calibrated screen and eye position.
