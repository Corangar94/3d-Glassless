# Monotonic pose-result delivery

Every timestamped tracker result must move forward on the same wrap-safe uint32 capture clock used by the pose filter and native overlay.

A duplicate or out-of-order pose is not a new face observation. Treating one as fresh can:

- refresh the `tracking` state indefinitely even though inference stopped progressing;
- inject stale yaw, pitch, roll, or confidence into the Kalman filter;
- shrink filter covariance repeatedly around an old sample;
- keep the hold timer alive and delay the neutral/paused safety state;
- make the capture-time speed limiter appear to reject translation while other pose fields still change.

## Delivery boundary

`FrameProcessorAdapter` now applies `PoseResultTimelineGate` immediately after the tracker returns and before `TrackingLoop` performs validation, hold-state refresh, spike limiting, filtering, or publication.

The gate behavior is:

- the first nonzero timestamped result is accepted;
- a strictly newer timestamp is accepted;
- the exact uint32 uptime rollover is accepted;
- a duplicate timestamp is returned as `None`;
- a backward or half-range-ambiguous timestamp is returned as `None`;
- a malformed timestamped result is returned as `None` instead of raising later;
- a result without `capture_timestamp_ms`, or with the historical value `0`, remains a legacy untimestamped result and passes through without becoming the ordering anchor;
- a MediaPipe/OpenCV backend transition starts a new timeline during the same `process_frame` call.

Returning `None` deliberately reuses the camera loop's existing no-new-measurement behavior. A recent valid pose enters `hold` prediction; once the hold window expires, output becomes neutral and `paused`. Rejected results therefore cannot refresh face presence or reach the speed limiter and Kalman filter.

## Compatibility

The call-signature adapter still resolves current, positional-only, `**kwargs`, and frame-only tracker APIs once before processing. Opaque legacy return values are unchanged. The gate only constrains objects that explicitly advertise a nonzero `capture_timestamp_ms`.

`PoseStepLimiter.limit()` retains its lower-level duplicate/out-of-order tuple behavior for direct callers. In the packaged camera pipeline, the earlier result gate means those invalid measurements never reach the limiter.

## Diagnostics and lifecycle

The gate records accepted timestamped and untimestamped results, duplicate results, out-of-order results, malformed timestamp results, the current backend-transition generation, and the last rejection reason. Its ordering anchor can also be reset explicitly without clearing lifetime counters.
