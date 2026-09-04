# Time-aware live auto-tuning

The launcher adapts overlay smoothing and dead-zone values from the filtered viewer position it receives while tracking. Low measured speed favors stability; deliberate motion favors responsiveness.

## Frame-rate invariance

The original tuner applied fixed exponential-moving-average coefficients once per callback. That made its time response depend on callback rate: a 60 Hz stream applied four times as many updates per second as a 15 Hz stream, so the same physical movement became more responsive—and potentially noisier—on the faster path.

The tuner now converts the established 30 Hz coefficients into an elapsed-time coefficient:

```text
adjusted_alpha = 1 - (1 - nominal_alpha) ** (elapsed_seconds * 30)
```

At 30 Hz, behavior is unchanged. At 15 Hz, one update has the same effect as two nominal updates. At 60 Hz, each update has a smaller effect, and the complete one-second response matches the 15 Hz and 30 Hz paths.

This conversion is applied independently to:

- the viewer-speed EMA that blends stability and responsiveness; and
- the viewing-distance EMA that scales the still-head dead zone.

## Tracking episode boundaries

A callback gap of 500 ms or more starts a new tuning episode. The first post-gap pose becomes the new position and distance anchor with zero measured speed. It is not compared with the last pose from a stopped, stalled, or reconnected tracker session.

Duplicate and backward timestamps are ignored without changing state. This prevents an out-of-order callback from creating a synthetic high-speed movement.

## Input and outlier safety

Non-finite coordinates or timestamps, booleans used as measurements, and negative timestamps are rejected without poisoning the long-lived EMA state. Before entering the speed EMA, instantaneous three-dimensional speed is capped at 300 cm/s. The output was already fully responsive above 20 cm/s, so the cap does not delay deliberate motion; it only bounds how long one pathological sample can keep the tuner in its responsive state.

The tuner exposes rejected-sample and long-gap episode-reset counters for focused diagnostics. An explicit `reset()` is also available to clear position, speed, and distance history when an owner has a stronger session boundary.

## Existing output behavior

The established tuning ranges are unchanged:

- at or below 1 cm/s: smoothing `0.28` with the distance-scaled still-head dead zone;
- at or above 20 cm/s: smoothing `0.06` and dead zone `0.5 mm`;
- between those speeds: linear blending of those output values.

Only the temporal estimation and failure behavior changed.
