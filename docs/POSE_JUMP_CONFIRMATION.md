# Persistent confirmation of extreme pose jumps

A pose can be fresh, timestamp-correct, and above the normal confidence threshold while still representing a one-frame landmark failure or an accidental switch to another visible face. Passing that sample directly into translation limiting and the adaptive filter can tug the virtual camera away from the established viewer.

The packaged/source runtime now applies `PoseJumpConfirmationGate` **after** the existing freshness and confidence admission boundary and **before** raw translation/orientation limiting.

## Behavior

Normal motion remains immediate. A result is treated as an extreme discontinuity only when at least one of these time-aware limits is exceeded relative to the last accepted pose:

- combined X/Y movement: the larger of 20 cm or 600 cm/s over the capture interval;
- Z movement: the larger of 25 cm or 720 cm/s over the capture interval;
- yaw, pitch, or roll: the larger of 35 degrees or 1,080 degrees/s over the capture interval.

The angular comparison uses the shortest circular path, so `179° → -179°` is a two-degree movement rather than a 358-degree jump.

An extreme result is held as a candidate rather than published. The next admitted result confirms the change only when it:

- arrives within 250 ms;
- is within 12 cm in X/Y, 15 cm in Z, and 20 degrees of the candidate;
- has confidence of at least 0.45.

Two consistent samples, including the first candidate, confirm the new pose. This adds only one accepted-measurement interval to a genuine persistent viewer change while completely suppressing an isolated bad frame. Once confirmed, the existing physical-speed limiter still bounds how quickly the displayed pose moves toward the new measurement.

A normal result near the established viewer immediately cancels an unconfirmed candidate and resumes the ordinary path.

## Episode reset

The gate starts a new episode and accepts the next admitted pose directly when:

- the existing measurement-admission boundary is reset after camera reconnect or true face loss;
- the tracker backend transition generation changes;
- at least 750 ms separates the accepted pose timestamps.

This prevents confirmation from dragging a newly reacquired viewer toward an obsolete pre-dropout anchor.

## Optional configuration

The defaults work without adding configuration keys. They can be overridden with:

```yaml
tracking:
  pose_jump_confirmation:
    enabled: true
    minimum_xy_jump_cm: 20.0
    minimum_z_jump_cm: 25.0
    minimum_angle_jump_deg: 35.0
    trigger_xy_speed_cm_s: 600.0
    trigger_z_speed_cm_s: 720.0
    trigger_angular_speed_deg_s: 1080.0
    confirmation_samples: 2
    candidate_xy_tolerance_cm: 12.0
    candidate_z_tolerance_cm: 15.0
    candidate_angle_tolerance_deg: 20.0
    candidate_timeout_ms: 250
    reset_after_ms: 750
    minimum_candidate_confidence: 0.45
```

`enabled: false` preserves the admitted-pose path exactly. Invalid configuration falls back atomically to the safe defaults.

## Compatibility

Direct imports of `tracker.main.TrackingLoop` and `tracker.latest_frame_runtime.LatestFrameTrackingLoop` retain their existing behavior. The normal source entrypoint and frozen `--tracker-child` entrypoint use `StableLatestFrameTrackingLoop`, which subclasses the latest-frame runtime and wraps its existing measurement-admission object.

Opaque or historical zero-timestamp direct values pass through unchanged. The gate exposes lifetime accepted, suspected, confirmed, rejected, and low-confidence counts together with current candidate state for diagnostics and focused tests.
