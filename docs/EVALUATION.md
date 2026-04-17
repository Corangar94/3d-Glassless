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

To generate controlled synthetic fixtures first:

```powershell
python scripts/generate_depth_fixture.py path\to\depth_frames --mode breathing --frames 60
```

These metrics are not a replacement for visual inspection, but they make
“watery depth” regressions measurable across repeated captures.

To compare a captured sequence against a synthetic or known-good baseline:

```powershell
python scripts/compare_depth_fixture.py path\to\captured_depth path\to\baseline_depth --max-ratio 2.0
```

To create a captured sequence from overlay depth-debug screenshots, press
`Ctrl+D` to enable depth view, use `Ctrl+Shift+S` to save several depth PNGs,
then import them:

```powershell
python scripts/import_depth_capture.py path\to\depth_screenshots path\to\captured_depth
```

For renderer experiments, `tracker.depth_reprojection.synthesize_views` provides
a tested CPU reference path for generating mono, stereo, or quilt view stacks
from an RGB image and normalized depth map. Pass `confidence_mask` when a depth
source can identify low-confidence or disoccluded pixels; invalid samples are
filled rather than reprojected as false geometry.

To render a concrete stereo strip or quilt PNG from one RGB frame plus a `.npy`
depth map:

```powershell
python scripts/render_views.py image.png depth.npy stereo.png --backend stereo_autostereo
python scripts/render_views.py image.png depth.npy quilt.png --backend lightfield_quilt
```

To select the intended backend in `config.yaml` for diagnostics and support
artifacts:

```powershell
python scripts/set_display_backend.py stereo_autostereo
python scripts/set_display_backend.py lightfield_quilt
```

To record backend-specific calibration metadata for diagnostics/support bundles:

```powershell
python scripts/calibrate_display_backend.py stereo_autostereo --viewer-distance-cm 65 --view-cone-deg 35
python scripts/calibrate_display_backend.py lightfield_quilt --viewer-distance-cm 65 --view-cone-deg 40
```

## Performance

Use `overlay.log` as the first pass. The overlay writes a once-per-second
summary containing render frames, depth inference cadence, head pose, relative
head motion, GPU draw timing (`gpu_ms`), wobble, strength, depth, and frame
availability.

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
Use `tracker.performance_capture.FrameTimingCsvWriter` for tools that need to
write compatible captures.

To export approximate live overlay cadence from `overlay.log` into the same CSV
format:

```powershell
python scripts/export_overlay_timings.py overlay.log timings.csv
```

To combine depth and frame-pacing checks in one report:

```powershell
python scripts/run_evaluation.py --depth-dir path\to\depth_frames --timing-csv path\to\timings.csv
```

For scripts or CI:

```powershell
python scripts/run_evaluation.py --depth-dir path\to\depth_frames --timing-csv path\to\timings.csv --format json --output evaluation.json
```

Targets:

- render loop should match the display refresh when possible
- depth inference should avoid backlog and process the freshest frame
- tracking-to-display latency should remain low enough that head-coupled motion
  feels attached to the display rather than lagging behind it

For measured tracking-to-display latency captures, write:

```csv
timestamp_ms,tracking_to_display_ms
0,10
16,12
```

Run:

```powershell
python scripts/run_latency_evaluation.py path\to\latency.csv --target-ms 20
python scripts/run_evaluation.py --latency-csv path\to\latency.csv --latency-target-ms 20
python scripts/collect_support.py --output-dir support_bundle --latency-csv path\to\latency.csv --latency-target-ms 20
```

## Comfort And Display Checks

Use conservative depth strength for longer sessions. Increase depth only after
tracking quality is `GOOD` and depth debug view is stable.

Check:

- text and UI remain readable
- ghosting/crosstalk is not obvious at the intended seating distance
- lateral head movement produces depth change without nausea or eye strain
- depth ordering tasks are easier than in flat mode, not just more dramatic

For repeatable subjective captures, write a CSV with 1-5 scores:

```csv
eye_strain,headache,nausea,disorientation,depth_realism,ui_readability,crosstalk
1,1,1,1,5,5,1
```

Discomfort and crosstalk are severity scores where `1` is best. Depth realism
and UI readability are quality scores where `5` is best.

Run:

```powershell
python scripts/run_comfort_evaluation.py path\to\comfort.csv
python scripts/run_evaluation.py --comfort-csv path\to\comfort.csv --format json --output evaluation.json
python scripts/collect_support.py --output-dir support_bundle --comfort-csv path\to\comfort.csv
```

For objective display-zone and crosstalk measurements, record a workspace grid:

```csv
x_cm,z_cm,crosstalk_percent,view_locked
-10,60,8,true
0,60,6,true
10,60,12,false
```

Run:

```powershell
python scripts/run_display_quality.py path\to\display_quality.csv
python scripts/run_evaluation.py --display-quality-csv path\to\display_quality.csv --format json --output evaluation.json
python scripts/collect_support.py --output-dir support_bundle --display-quality-csv path\to\display_quality.csv
```

## Policy Boundary

World of Warcraft and protected multiplayer titles remain a later feasibility
gate. Do not use process injection or game-depth hooks as default validation for
the overlay runtime.

For explicitly approved offline/friendly titles, compare an external depth
capture against the monocular baseline without injecting into a protected game:

```powershell
python scripts/run_friendly_depth_experiment.py --title "Friendly Offline Title" --external-depth-dir path\to\external_depth --monocular-depth-dir path\to\monocular_depth --policy-approved --offline-title
```
