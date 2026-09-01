# Pose-filter reacquisition after tracking gaps

The adaptive pose filter estimates both position and velocity. During a short face-tracking miss, preserving that state is useful: the camera loop can publish a bounded hold prediction instead of snapping immediately to neutral.

After a longer measurement gap, however, the old velocity and covariance no longer describe the viewer. Reusing them when a face is reacquired can pull the first new pose toward the old location, extrapolate obsolete motion, or preserve an old unwrapped orientation turn.

## Default behavior

`AdaptivePoseFilter` starts a fresh estimator episode when the next accepted pose measurement is at least **500 ms** newer than the previous accepted measurement on the wrap-safe capture clock.

The first pose after that gap:

- initializes X, Y, and Z exactly at the new measurement;
- starts translation velocity at zero;
- initializes yaw, pitch, and roll from the new canonical orientation;
- starts angular velocity at zero;
- discards old covariance and confidence;
- preserves the new pose's confidence and capture timestamp.

The reset happens only when a new measurement arrives. Calling `predict()` during the existing hold period does not reset or neutralize the filter; `TrackingLoop` retains ownership of hold-versus-paused output policy.

## Backend transitions

Backend-transition synchronization runs before measurement-gap evaluation.

- A stale/absent transition source already performs a full filter reset, so the subsequent pose is not double-counted as a gap reset.
- A recent transition first clears backend-specific dynamics. If the measurement gap is also long, the estimator then starts fresh from the pose already aligned by the backend continuity bridge.

This keeps MediaPipe/OpenCV transition continuity while preventing old motion from surviving a genuine tracking outage.

## Timestamp behavior

Gap detection uses forward uint32 subtraction:

- normal forward timestamps are measured exactly;
- the Windows uptime rollover is handled normally;
- duplicate, backward, and half-range-ambiguous values are not interpreted as enormous gaps.

The generic pose-result timeline gate still rejects nonmonotonic tracker results before they reach this filter.

## Direct API and diagnostics

The constructor argument is:

```python
AdaptivePoseFilter(measurement_gap_reset_ms=500.0)
```

Set `measurement_gap_reset_ms=0` to disable automatic gap resets for a direct caller. `set_measurement_gap_reset_ms()` changes the threshold at runtime.

`gap_snapshot()` reports:

- the configured threshold;
- the number of gap resets in the current filter session;
- the most recent reset gap.

Calling `reset()` clears both filter state and these session counters.
