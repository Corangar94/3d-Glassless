# Bounded MediaPipe input resolution

Glassless3D keeps the original webcam frame for camera-quality analysis, the OpenCV fallback, and any other camera-loop consumers. Only the frame accepted by MediaPipe is optionally reduced.

## Processing order

For asynchronous MediaPipe tracking, the camera frame passes these gates in order:

1. capture timestamp expansion;
2. async-health check;
3. backlog admission;
4. optional BGR resize;
5. BGR-to-RGB conversion;
6. contiguous RGB allocation and `mp.Image` construction;
7. `detect_async` submission.

Duplicate, out-of-order, unhealthy, or backpressured inputs therefore remain cheap: they are rejected before resize, color conversion, and allocation. When resizing is needed, it happens while the frame is still BGR so the expensive RGB buffer is created only at the bounded dimensions.

Synchronous MediaPipe tracking uses the same preparation path and passes the prepared width and height into pose geometry.

## Default

```yaml
tracking:
  mediapipe_runtime:
    max_input_width_px: 960
```

The default 1280×720 input becomes 960×540:

- original pixels: 921,600;
- submitted pixels: 518,400;
- reduction: 403,200 pixels, or 43.75%.

Frames at or below the cap are passed through without a resize or upscale. The aspect ratio is preserved, with the target height rounded to the nearest pixel. `cv2.INTER_AREA` is used for downscaling.

Set the cap to `0` to keep full-resolution MediaPipe input. A nonzero cap must be between 320 and 8192 pixels.

## Pose geometry

MediaPipe landmarks are normalized to the submitted image. When a frame is scaled uniformly:

- iris coordinates and iris separation scale with image width and height;
- the focal length derived from FOV scales with image width;
- calibrated intrinsics are scaled to the active image dimensions;
- normalized landmark centers are unchanged.

The ratios used for physical depth and screen-plane reconstruction therefore remain the same. The tracker uses the prepared image dimensions both in asynchronous callbacks and in synchronous result conversion, avoiding a mixed original/prepared coordinate system.

## Compatibility

`FaceTracker` accepts `max_input_width_px` directly. A value of `0` preserves the historical full-resolution path. Bare `__new__` test doubles and downstream subclasses that predate the setting also fall back to full resolution when the attribute is absent.

The central MediaPipe runtime policy forwards the setting to strict MediaPipe, automatic primary MediaPipe, and every shadow recovery candidate. It strips the setting from OpenCV constructors.
