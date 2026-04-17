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

## Performance

Use `overlay.log` as the first pass. The overlay writes a once-per-second
summary containing render frames, depth inference cadence, head pose, relative
head motion, wobble, strength, depth, and frame availability.

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
