# Capture-time-aware pose step limiting

Raw face trackers can occasionally produce a one-frame translation spike after a difficult detection, partial occlusion, backend transition, or eye-distance error. Glassless3D limits those raw translations before they enter the Kalman pose filter.

## Why the limiter uses time

The previous compatibility helper used fixed limits of 10 cm in the screen plane and 12 cm in depth per accepted measurement. A per-frame value changes its physical meaning with tracker cadence:

- 10 cm per frame at 30 fps permits 300 cm/s;
- 10 cm per frame at 60 fps permits 600 cm/s;
- 10 cm after a 100 ms inference interval permits only 100 cm/s.

The production tracking loop now derives each permitted step from the actual capture-timestamp interval.

## Default policy

```yaml
tracking:
  pose_step_limit:
    max_xy_speed_cm_s: 300.0
    max_z_speed_cm_s: 360.0
    reset_after_ms: 500
```

For a normal 33 ms measurement interval, these defaults permit approximately 9.9 cm of combined X/Y movement and 11.88 cm of Z movement, closely matching the old 30 fps behavior. At 60 fps the bound becomes proportionally smaller; at lower tracker cadence it permits proportionally more legitimate travel.

The X/Y limit is radial, so diagonal motion receives the same total screen-plane allowance as horizontal or vertical motion.

## Measurement ordering

The limiter uses the project’s wrap-safe uint32 capture clock:

- duplicate measurements cannot claim additional travel time;
- an out-of-order measurement is ignored without moving the accepted timestamp anchor;
- the exact Windows uptime rollover is handled normally;
- after a gap of 500 ms or more, the next valid pose starts a fresh episode and is accepted directly.

Starting a fresh episode after the hold window prevents a newly reacquired viewer from being pulled toward an obsolete pre-dropout position.

## Runtime lifecycle

The limiter anchor is cleared when:

- a new tracking loop starts;
- the webcam capture session is replaced;
- the active face-tracker backend changes.

Orientation, confidence, and capture timestamp are preserved when translation is limited. The historical `_limit_pose_step` helper remains available for direct compatibility tests and callers, but the production camera loop uses `PoseStepLimiter`.

Setting either speed to `0` disables that axis bound. Invalid negative or non-finite settings are rejected and the tracker falls back to the safe defaults.
