# Live producer-filter smoothing

The launcher already writes `OverlaySettings.smoothing_alpha` into the versioned `G3D_Settings` mapping. The field represents the Kalman measurement-noise value used to balance stability and responsiveness:

- a larger value trusts each measurement less and produces a steadier pose;
- a smaller value follows deliberate motion more quickly.

The native overlay receives a producer-filtered `G3D_PoseV2` pose, so applying another smoothing stage there would duplicate filtering and add latency. The packaged tracker now consumes the existing settings field directly through `AdaptivePoseFilter.set_measurement_noise()` before processing the next admitted measurement.

## Runtime path

```text
launcher manual control or TrackingAutoTuner
→ G3D_Settings.smoothing_alpha
→ LiveFilterTuningController
→ AdaptivePoseFilter.set_measurement_noise()
→ next producer pose update
→ G3D_PoseV2
→ native render-time prediction only
```

`LiveFilterTuningTrackingLoop` subclasses the complete camera-recovery, pose-stability, and latest-frame stack. The normal source and frozen entrypoints select it lazily through `tracker.pose_stability_runtime.main()`.

Direct callers of `TrackingLoop`, `LatestFrameTrackingLoop`, `StableLatestFrameTrackingLoop`, or `CameraControlRecoveryTrackingLoop` keep their existing static constructor behavior.

## Admission and polling

The controller polls the shared settings mapping at most every 100 ms. This is faster than the launcher's existing 250 ms auto-tune write interval while avoiding a process-boundary read on every camera frame.

A value is applied only when it is:

- a real finite number rather than a boolean or string;
- between `0.01` and `1.0`, inclusive; and
- more than `0.001` away from the last successfully applied value.

Sub-threshold changes accumulate relative to the last applied value, so the deadband suppresses numerical churn without permanently losing gradual motion toward a new setting.

The first valid value is applied immediately. An unavailable mapping, transient unstable read, malformed value, reader exception, or filter-setter exception leaves the previous configured or live value in place. A failed read still consumes the poll interval so a broken optional mapping cannot be hammered at camera rate.

If a supplied test or embedding clock moves backwards, the controller starts a new polling window rather than suppressing settings until the old timestamp catches up.

## Lifecycle

The Windows `SharedSettingsReader` import and mapping attachment are lazy. Standalone or non-Windows direct construction without a packaged configuration path does not import the Win32 shared-memory module and retains `tracking.smoothing_r`.

The settings reader is closed in `finally` when the tracking loop exits, including error paths. Cleanup and optional tuning failures never stop camera tracking or pose publication.

## Diagnostics

The runtime exposes a snapshot containing:

- performed and interval-skipped polls;
- unavailable and invalid settings counts;
- unchanged and applied values;
- read, setter, clock, and close errors;
- backwards-clock window resets;
- the last successfully applied measurement noise; and
- current closed/error state.

No shared-memory layout or native ABI changed. `deadzone_mm` continues to be consumed by the native overlay, while `smoothing_alpha` now controls the single producer Kalman filter rather than a second render-side filter.
