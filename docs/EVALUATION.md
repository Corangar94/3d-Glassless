# Glassless3D Evaluation Guide

This guide turns the deep-research recommendations into repeatable checks for
the overlay-first runtime.

## Tracking Quality

Use `python -m tracker.debug_monitor` while the tracker is running. The rolling
quality panel reports:

- `loss`: percentage of samples that are missing or stale
- `jitter`: RMS frame-to-frame pose movement in centimeters
- `reacq`: worst reacquisition time after a lost/stale run
- `quality`: `GOOD`, `WARN`, or `DANGER` using conservative two-view
  autostereo thresholds

Targets:

- `GOOD`: loss below 2%, jitter below 0.5 cm, reacquisition below 150 ms
- `WARN`: usable for debugging, but view-zone instability may be visible
- `DANGER`: fix camera placement, lighting, or tracking before judging depth
  quality

## Depth Quality

Use the overlay depth debug hotkey (`Ctrl+D`) and controlled scenes before
judging arbitrary games or desktop content.

Check:

- static geometry should not visibly breathe while the head is still
- thin edges should not shimmer excessively during slow head movement
- the center-crop mapping should preserve depth variation on ultrawide displays
- fallback flat depth should be obvious in logs and should not be mistaken for
  a working depth model

For offline depth-frame sequences, use `tracker.depth_evaluation`:

- `compute_depth_stability(frames)` returns mean, 95th percentile, and max
  frame-to-frame absolute depth deltas
- `classify_depth_stability(metrics)` classifies normalized temporal stability
  as `GOOD`, `WARN`, or `DANGER`

For a directory of `.npy` depth frames, run:

```powershell
python -m tracker.depth_benchmark path\to\depth_frames
```

The command exits non-zero when the sequence classifies as `DANGER`.

These metrics are not a replacement for visual inspection, but they make
“watery depth” regressions measurable across repeated captures.

## Performance

Use `overlay.log` as the first pass. The overlay writes a once-per-second
summary containing render frames, depth inference cadence, head pose, relative
head motion, wobble, strength, depth, and frame availability.

For frame-time captures, use `tracker.performance_evaluation`:

- `compute_frame_timing_metrics(samples, target_fps=60.0)` reports average,
  p95, max frame time, average FPS, and over-budget rate
- `classify_frame_pacing(metrics)` classifies pacing as `GOOD`, `WARN`, or
  `DANGER`

For a CSV capture with `timestamp_ms,frame_time_ms` columns, run:

```powershell
python -m tracker.performance_benchmark path\to\timings.csv --target-fps 60
```

The command exits non-zero when frame pacing classifies as `DANGER`.

Targets:

- render loop should match the display refresh when possible
- depth inference should avoid backlog and process the freshest frame
- tracking-to-display latency should remain low enough that head-coupled motion
  feels attached to the display rather than lagging behind it

## Comfort And Display Checks

Use conservative depth strength for longer sessions. Increase depth only after
tracking quality is `GOOD` and depth debug view is stable.

Check:

- text and UI remain readable
- ghosting/crosstalk is not obvious at the intended seating distance
- lateral head movement produces depth change without nausea or eye strain
- depth ordering tasks are easier than in flat mode, not just more dramatic

## Policy Boundary

World of Warcraft and protected multiplayer titles remain a later feasibility
gate. Do not use process injection or game-depth hooks as default validation for
the overlay runtime.
