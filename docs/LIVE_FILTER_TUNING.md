# Live producer-filter smoothing

The launcher writes `OverlaySettings.smoothing_alpha` into the versioned `G3D_Settings` mapping. The field represents the Kalman measurement-noise value used to balance stability and responsiveness:

- a larger value trusts each measurement less and produces a steadier pose;
- a smaller value follows deliberate motion more quickly.

The native overlay receives a producer-filtered `G3D_PoseV2` pose, so applying another smoothing stage there would duplicate filtering and add latency. The packaged tracker consumes the existing settings field directly through `AdaptivePoseFilter.set_measurement_noise()` before processing the next admitted measurement.

## Runtime path

```text
launcher manual control or TrackingAutoTuner
→ coordinated G3D_Settings publication
→ version-aware scalar projection
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

## Version-aware scalar projection

The shared settings ABI uses an odd/even version marker around each complete 88-byte publication. The packaged `SharedSettingsReader` offers `read_smoothing_alpha()`, which reads:

1. the committed version marker;
2. the single four-byte `smoothing_alpha` field; and
3. the version marker again.

The sample is accepted only when both version reads match and are even. On the normal unchanged-settings path, the controller compares the complete `(version, value)` pair with the last processed pair and returns immediately. It no longer needs two full 88-byte snapshots, a complete struct unpack, validation of every unrelated overlay field, or construction of an `OverlaySettings` object ten times per second.

A new version with an unchanged or sub-epsilon smoothing value is marked processed without calling the filter setter. A malformed or out-of-range value is also remembered for that stable version/value pair, preventing repeated validation until either member changes. A setter exception does **not** commit the pair, allowing the same snapshot to retry at the next bounded poll.

A reused even version carrying a changed scalar is processed defensively and increments a collision diagnostic. Coordinated current writers should never create this condition, but the fallback lets live tuning recover from an older writer restart instead of remaining pinned to the prior session's value.

Readers without the projection method continue through the historical full `read()` path. The full settings reader, binary layout, field offsets, mapping name, and native consumers remain unchanged.

## Coordinated writer lifecycle

Every current `SharedSettingsWriter` serializes publication through a named Windows mutex derived from the mapping name. While holding that mutex, it reads the current mapping version and derives the next odd writing marker and even committed version from the shared channel itself. Two launcher or utility processes therefore cannot interleave the two body slices or publish the same even version.

Attaching a second writer preserves the existing committed snapshot instead of writing defaults over live user settings. A genuinely new zero-filled mapping receives one complete default snapshot. If a process died after leaving an odd marker, the next coordinated writer treats that transaction as abandoned and publishes a complete even default snapshot before accepting new writes. Version-zero snapshots with a nonzero payload are preserved because zero is a legitimate uint32 rollover commit.

Settings are fully validated and packed before the version is made odd. The version remains odd while both sides of the mid-structure marker are copied, then the final aligned four-byte store publishes the even commit. A writer waiting more than one second for the named mutex fails that update rather than blocking the UI indefinitely.

The coordination protects all updated writers, including the launcher and standalone writer utility. The reader still relies only on the existing odd/even ABI, so native and Python consumers require no mutex and no layout change.

## Reader recovery

A failed or invalid mapping access now detaches both the stale view and its handle. The next bounded poll can attach a fresh mapping cleanly, without leaking one handle per recovery cycle or retaining a dead view.

See [Shared settings writer coordination](SHARED_SETTINGS_COORDINATION.md) for the publication and crash-recovery invariants.

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
- version-fast-path, unchanged-pair, malformed-version, and reused-version collision counts;
- the latest processed settings version and scalar;
- the last successfully applied measurement noise; and
- current closed/error state.

No shared-memory layout or native ABI changed. `deadzone_mm` continues to be consumed by the native overlay, while `smoothing_alpha` controls the single producer Kalman filter rather than a second render-side filter.