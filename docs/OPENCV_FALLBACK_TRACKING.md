# Temporal OpenCV fallback tracking

The `cv2` tracker is a lower-quality fallback for systems where MediaPipe cannot initialize or becomes unhealthy. It remains dependency-light: it uses OpenCV's built-in Haar cascades, Shi-Tomasi corner detection, and pyramidal Lucas-Kanade optical flow rather than a contrib-only object tracker.

## Runtime strategy

The fallback works on a grayscale image whose longest edge is capped at 640 pixels:

1. A face cascade establishes a face rectangle and an eye cascade selects a plausible upper-face eye pair.
2. Shi-Tomasi corners are seeded inside the face rectangle.
3. Sparse Lucas-Kanade optical flow estimates median translation and a bounded scale change between frames.
4. Every five frames, a cascade pass corrects flow drift. It searches an expanded face ROI first.
5. Every thirty frames, or when flow is unavailable/weak, a full-frame cascade scan is allowed.
6. Two periodic cascade misses may retain a healthy flow estimate; the third miss retires the track.

Cascades remain authoritative. Flow errors are contained and trigger an immediate detector pass on the same frame.

The constructor option retains its established name, `detection_width_px`, for compatibility. For a normal landscape camera the longest edge is still its width, so existing behavior is unchanged. Applying the same limit to image height prevents a rotated camera from bypassing the processing budget:

| Camera frame | Previous width-only result | Longest-edge result |
|---|---:|---:|
| 1280×720 | 640×360 | 640×360 |
| 720×1280 | about 640×1138 | 360×640 |
| 1200×1600 | 640×853 | 480×640 |

For the rotated 720×1280 case, the grayscale flow/cascade image falls from about 728,320 pixels to 230,400 pixels—roughly a 68% reduction from the previous fallback path. Sources whose width and height are already within 640 pixels are not upscaled.

## Jitter and continuity controls

A detected rectangle is blended 70% toward the cascade correction and 30% toward the flow prediction. This suppresses detector box jitter without allowing optical-flow drift to become permanent.

A fresh cascade eye pair records:

- inter-eye separation as a fraction of face width;
- eye midpoint inside the face rectangle; and
- eye-line roll.

Flow may propagate that geometry for at most eighteen frames. If a periodic face correction does not find eyes, the recent eye geometry is transformed into the corrected rectangle instead of snapping position from the eye midpoint to the face center. After the bounded hold expires, distance returns to the face-width estimate.

## Geometry preservation

The longest-edge bound remains one uniform aspect-preserving scale. Cascade boxes and eye points are mapped back through the same inverse scale before physical pose reconstruction. The original camera width and height are still used for focal-length and screen-plane geometry.

As a result, portrait and landscape input retain the same normalized center, face-size ratio, eye separation ratio, and inferred X/Y/Z pose they would have at full resolution. Only the fallback detector and optical-flow workload changes.

## Safety bounds

Sparse tracking is rejected when:

- fewer than six valid points remain;
- flow error exceeds the configured threshold;
- robust displacement inliers are insufficient;
- median motion exceeds a fraction of face size;
- scale changes by more than 12% in one frame; or
- the transformed rectangle exits the usable image area.

The cascade path escalates explicit classifier failures. The optical-flow path is optional and may fail back to cascade detection without terminating tracking.

## Default cadence

| Setting | Default |
|---|---:|
| Detection longest edge (`detection_width_px`) | 640 px |
| ROI/cascade correction | every 5 frames |
| Full-frame scan | every 30 frames |
| Maximum periodic cascade misses | 2 |
| Minimum accepted flow quality | 0.25 |
| Eye geometry hold | 18 frames |
| Cascade correction weight | 0.70 |

These defaults are constructor parameters on `tracker.face_tracker_cv2.FaceTracker`. They are deliberately internal while the fallback accumulates wider device coverage; the primary user-facing control remains `tracking.tracker_backend: auto|mediapipe|cv2`.

## Frozen package

`Glassless3D.spec` explicitly includes:

- `tracker.cv2_temporal_tracker`; and
- OpenCV XML files under `cv2/data`.

This keeps the face and eye cascades available in the standalone Windows package even when MediaPipe is not the active backend.
