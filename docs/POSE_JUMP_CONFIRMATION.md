# Persistent confirmation of extreme pose jumps

A pose can be fresh, timestamp-correct, and above the normal confidence threshold while still representing a one-frame landmark failure or an accidental switch to another visible face. Passing that sample directly into translation limiting and the adaptive filter can tug the virtual camera away from the established viewer.

The normal source and frozen tracker entrypoints now apply `PoseJumpConfirmationGate` **after** the existing freshness and confidence admission boundary and **before** raw translation/orientation limiting.

## Behavior

Normal motion remains immediate. A result is treated as an extreme discontinuity only when at least one of these time-aware limits is exceeded relative to the last accepted pose:

- combined X/Y movement: the larger of 20 cm or 600 cm/s over the capture interval;
- Z movement: the larger of 25 cm or 720 cm/s over the capture interval;
- yaw, pitch, or roll: the larger of 35 degrees or 1,080 degrees/s over the capture interval.

The angular comparison uses the shortest circular path, so `179° → -179°` is a two-degree movement rather than a 358-degree jump.

An extreme result is held as a candidate rather than published. The next admitted result confirms the change only when it:

- arrives within 250 ms of the **first** candidate;
- remains within 12 cm in X/Y, 15 cm in Z, and 20 degrees of that first candidate; and
- has confidence of at least 0.45.

Two consistent samples, including the first candidate, confirm the new pose. This adds only one accepted-measurement interval to a genuine persistent viewer change while completely suppressing an isolated bad frame. Once confirmed, the existing physical-speed limiter still bounds how quickly the displayed pose moves toward the new measurement.

For a custom three-or-more-sample policy, the first candidate remains the fixed geometric reference and the timeout remains one fixed window measured from that first sample. Later candidates cannot move the reference by one tolerance radius per frame or extend the confirmation deadline indefinitely.

A normal result near the established viewer immediately cancels an unconfirmed candidate and resumes the ordinary path. A duplicate candidate timestamp is rejected rather than counted as another independent confirmation sample.

## Episode reset

The gate starts a new episode and accepts the next admitted pose directly when:

- the existing measurement-admission boundary is reset after camera reconnect or true face loss;
- the tracker backend transition generation changes; or
- at least 750 ms separates the accepted pose timestamps.

Confirmation reset runs even when the delegated admission reset raises, so a failed lifecycle reset cannot leave an old viewer anchor or candidate active. This prevents confirmation from dragging a newly reacquired viewer toward an obsolete pre-dropout anchor.

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

`enabled: false` preserves the admitted-pose path exactly. `confirmation_samples` must be an integer from 2 through 10. Candidate and reset timing values must be integers from 1 through 60,000 ms, with `reset_after_ms` at least as large as `candidate_timeout_ms`. Fractional or boolean timing/count values are rejected. Invalid configuration causes the complete optional block to fall back atomically to safe defaults.

## Runtime and packaging

`StableLatestFrameTrackingLoop` subclasses `LatestFrameTrackingLoop`, so latest-only camera acquisition remains active. Both real entrypoints select the stability runtime:

- `python -m tracker`;
- frozen launcher `--tracker-child`.

The two confirmation modules are listed explicitly in the PyInstaller hidden imports. Direct imports of `tracker.main.TrackingLoop` and `tracker.latest_frame_runtime.LatestFrameTrackingLoop` retain their existing behavior for library and test callers.

Opaque or historical zero-timestamp direct values pass through unchanged. The gate exposes lifetime accepted, suspected, confirmed, rejected, low-confidence, and duplicate-timestamp counts together with current candidate timestamps for diagnostics and focused tests.
