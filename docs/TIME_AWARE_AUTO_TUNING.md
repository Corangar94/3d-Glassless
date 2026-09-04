# Time-aware live auto-tuning

The launcher adapts overlay smoothing and dead-zone values from the filtered viewer position it receives while tracking. Low measured speed favors stability; deliberate motion favors responsiveness.

## Producer-time sampling

The tracker child publishes each legacy `G3D` pose with a wrap-safe uint32 producer timestamp. `TrackerProcess` now exposes that timestamp through a new four-value `position_sampled` signal while retaining the historical three-value `position_updated` signal for compatibility.

The active runtime window disconnects its legacy pose slot only after the timestamped signal is available. It expands producer timestamps across the uint32 Windows-uptime rollover and passes those seconds into `TrackingAutoTuner`. Duplicate, backward, malformed, or out-of-range producer timestamps drop the corresponding launcher pose instead of being relabeled with the current Qt callback time.

This prevents UI scheduling from changing measured motion. A 33 ms movement is still a 33 ms movement when the event loop delivers it early, late, or after a temporary stall. The first sample after a tracking boundary starts a new arbitrary producer-time epoch; only elapsed time is relevant to the tuner.

Two clocks remain intentionally separate:

- **producer pose time** drives velocity and elapsed-time EMA updates;
- **launcher monotonic time** enforces the existing 250 ms shared-settings write throttle.

Queued samples therefore preserve their physical timing but cannot create a burst of settings writes when the UI resumes.

## Frame-rate invariance

The original tuner applied fixed exponential-moving-average coefficients once per callback. That made its time response depend on callback rate: a 60 Hz stream applied four times as many updates per second as a 15 Hz stream, so the same physical movement became more responsive—and potentially noisier—on the faster path.

The tuner converts the established 30 Hz coefficients into an elapsed-time coefficient:

```text
adjusted_alpha = 1 - (1 - nominal_alpha) ** (elapsed_seconds * 30)
```

At 30 Hz, behavior is unchanged. At 15 Hz, one update has the same effect as two nominal updates. At 60 Hz, each update has a smaller effect, and the complete one-second response matches the 15 Hz and 30 Hz paths.

This conversion is applied independently to:

- the viewer-speed EMA that blends stability and responsiveness; and
- the viewing-distance EMA that scales the still-head dead zone.

## Tracking episode boundaries

A producer-sample gap of 500 ms or more starts a new tuning episode. The first post-gap pose becomes the new position and distance anchor with zero measured speed. It is not compared with the last pose from a stopped, stalled, or reconnected tracker session.

The active launcher also resets both the tuner and the producer timestamp expander on every real transition into or out of `tracking`. This covers short `tracking → hold/paused → tracking` round trips that complete before the 500 ms gap threshold and lets a restarted child begin from a lower uint32 uptime value safely.

The previous episode’s write-throttle timestamp is cleared at the same boundary, allowing the first accepted pose of the new episode to publish stable distance and smoothing values immediately.

Repeated identical status notifications and transitions that stay entirely outside `tracking` do not reset the tuner. During launcher construction, the status boundary remains compatible with an absent or legacy tuner object.

Direct legacy callers without the timestamped signal continue using local callback time. The runtime reinstalls its timestamp adapter if the operator toggles automatic tuning and the base window creates a new tuner instance.

## Applying the tuned outputs

The launcher writes the tuned values through the existing versioned `G3D_Settings` mapping.

- `deadzone_mm` is consumed by the native overlay's soft hysteretic dead zone.
- `smoothing_alpha` is polled by the packaged tracker and applied to `AdaptivePoseFilter.set_measurement_noise()` before the next accepted measurement.
- `head_dist_cm` remains available to the launcher/settings contract as the smoothed viewing-distance estimate; the render path already uses the live filtered Z position for physical eye distance.

The smoothing bridge polls at most every 100 ms and ignores changes within `0.001` of the last successfully applied value. It therefore follows the launcher's 250 ms auto-tune updates without reading shared memory on every camera frame or repeatedly applying numerical noise.

The native overlay does not add a second smoothing pass. `G3D_PoseV2` remains filtered once at the producer, followed only by bounded render-time prediction. See [Live producer-filter smoothing](LIVE_FILTER_TUNING.md).

## Input and outlier safety

Non-finite coordinates or timestamps, booleans used as measurements, and negative timestamps are rejected without poisoning the long-lived EMA state. Before entering the speed EMA, instantaneous three-dimensional speed is capped at 300 cm/s. The output was already fully responsive above 20 cm/s, so the cap does not delay deliberate motion; it only bounds how long one pathological sample can keep the tuner in its responsive state.

The tuner exposes rejected-sample and long-gap episode-reset counters. The producer timeline separately reports accepted, rejected, and reset counts together with its latest wire and expanded timestamps for focused diagnostics.

## Existing output behavior

The established tuning ranges are unchanged:

- at or below 1 cm/s: smoothing `0.28` with the distance-scaled still-head dead zone;
- at or above 20 cm/s: smoothing `0.06` and dead zone `0.5 mm`;
- between those speeds: linear blending of those output values.

The temporal source, estimation, failure behavior, and producer-filter application are now explicit end to end.
