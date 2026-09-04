# Time-aware live auto-tuning

The launcher adapts overlay smoothing and dead-zone values from the filtered viewer position it receives while tracking. Low measured speed favors stability; deliberate motion favors responsiveness.

## Producer-time sampling

The tracker child publishes each legacy `G3D` pose with a wrap-safe uint32 producer timestamp. `TrackerProcess` exposes that timestamp through a four-value `position_sampled` signal while retaining the historical three-value `position_updated` signal for compatibility.

The active runtime window disconnects its legacy pose slot only after the timestamped signal is available. It expands producer timestamps across the uint32 Windows-uptime rollover and passes those seconds into `TrackingAutoTuner`. Duplicate, backward, malformed, or out-of-range producer timestamps drop the corresponding launcher pose instead of being relabeled with the current Qt callback time.

This prevents UI scheduling from changing measured motion. A 33 ms movement is still a 33 ms movement when the event loop delivers it early, late, or after a temporary stall. The first sample after a tracking boundary starts a new arbitrary producer-time epoch; only elapsed time is relevant to the tuner.

Two clocks remain intentionally separate:

- **producer pose time** drives velocity and elapsed-time EMA updates;
- **launcher monotonic time** controls shared-settings publication.

Queued samples therefore preserve their physical timing but cannot create a burst of settings writes when the UI resumes.

## Bounded shared-settings publication

The base window retains its established 250 ms auto-tune write-attempt throttle. The active runtime adds a second boundary around only the shared-memory write reached from a live auto-tune pose update.

A candidate is published on the next allowed attempt when at least one value differs from the last actually published auto-tune baseline by:

- viewer distance: **0.25 cm**;
- smoothing value: **0.005**;
- dead zone: **0.10 mm**.

Smaller drift is coalesced rather than incrementing the `G3D_Settings` version four times per second. Candidates are always compared with the last published baseline, not the immediately preceding candidate, so several tiny changes accumulate and publish as soon as their total reaches a threshold.

A changed candidate that remains below every threshold is forced through after **2 seconds** since the last successful publication. This bounds convergence when the tuner settles gradually. An exactly unchanged candidate is not forced periodically, avoiding version churn during a perfectly stable pose.

The 250 ms attempt throttle remains authoritative for responsiveness: a meaningful change publishes at the next attempt, never through a faster parallel path. A suppressed attempt still consumes that throttle interval, so a delayed UI callback cannot create a burst of writes.

## Manual-write isolation

The coalescing writer is armed only for the synchronous base `_on_position` call while automatic tuning is enabled and tracking is active. All other settings writes remain immediate, including:

- manual sliders and spin boxes;
- comfort presets;
- screen or head-distance calibration;
- display-backend and depth-mode changes; and
- any write from another thread.

Each successful non-auto-tune write also becomes the new publication baseline. Automatic tuning therefore compares against the settings the overlay most recently received rather than an obsolete pre-edit value.

Arming is thread-local and nest-safe. A manual write on another thread cannot be suppressed merely because the UI thread is processing an auto-tune pose.

## Publication failure behavior

A malformed candidate or invalid launcher clock fails open and is offered to the established `SharedSettingsWriter` rather than silently dropped. The invalid value can still be rejected by that writer's normal finite/range validation. A corrupt candidate or clock never becomes a comparison baseline, so the next valid candidate publishes immediately.

The publication baseline and counters advance only after the delegated shared-memory write succeeds. A write exception therefore remains visible to the caller and cannot make an uncommitted value appear published.

The runtime exposes a snapshot with published, suppressed, forced, fail-open, external-seed, and reset counts together with the last published values and decision reason.

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

The active launcher also resets the tuner, producer timestamp expander, publication baseline, and base write-throttle deadline on every real transition into or out of `tracking`. This covers short `tracking → hold/paused → tracking` round trips that complete before the 500 ms gap threshold and lets a restarted child begin from a lower uint32 uptime value safely.

The first accepted pose of a new episode can therefore publish stable distance and smoothing values immediately. Toggling automatic tuning also resets the producer timeline and publication baseline, and reinstalls either runtime adapter if the base window replaced its tuner.

Repeated identical status notifications and transitions that stay entirely outside `tracking` do not reset the tuner. During launcher construction, the status boundary remains compatible with absent or legacy tuner/writer objects.

Direct legacy callers without the timestamped signal continue using local callback time. Direct base-window users without the runtime publication wrapper retain the historical write behavior.

## Applying the tuned outputs

The launcher writes accepted tuned values through the existing versioned `G3D_Settings` mapping.

- `deadzone_mm` is consumed by the native overlay's soft hysteretic dead zone.
- `smoothing_alpha` is polled by the packaged tracker and applied to `AdaptivePoseFilter.set_measurement_noise()` before the next accepted measurement.
- `head_dist_cm` remains available to the launcher/settings contract as the smoothed viewing-distance estimate; the render path already uses the live filtered Z position for physical eye distance.

The smoothing bridge polls at most every 100 ms and ignores changes within `0.001` of the last successfully applied value. It therefore follows meaningful or forced launcher publications without reading shared memory on every camera frame or repeatedly applying numerical noise.

The native overlay does not add a second smoothing pass. `G3D_PoseV2` remains filtered once at the producer, followed only by bounded render-time prediction. See [Live producer-filter smoothing](LIVE_FILTER_TUNING.md).

## Input and outlier safety

Non-finite coordinates or timestamps, booleans used as measurements, and negative timestamps are rejected without poisoning the long-lived EMA state. Before entering the speed EMA, instantaneous three-dimensional speed is capped at 300 cm/s. The output was already fully responsive above 20 cm/s, so the cap does not delay deliberate motion; it only bounds how long one pathological sample can keep the tuner in its responsive state.

The tuner exposes rejected-sample and long-gap episode-reset counters. The producer timeline separately reports accepted, rejected, and reset counts together with its latest wire and expanded timestamps for focused diagnostics.

## Existing output behavior

The established tuning ranges are unchanged:

- at or below 1 cm/s: smoothing `0.28` with the distance-scaled still-head dead zone;
- at or above 20 cm/s: smoothing `0.06` and dead zone `0.5 mm`;
- between those speeds: linear blending of those output values.

The temporal source, estimation, publication, failure behavior, and producer-filter application are now explicit end to end.
