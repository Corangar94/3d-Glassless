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

The repository also has manifest-backed depth fixtures. List or benchmark them
with:

```powershell
python -m tracker.depth_fixtures --list
python -m tracker.depth_fixtures --benchmark-all
python scripts/run_evaluation.py --depth-fixture synthetic_static_smoke
python scripts/collect_support.py --output-dir support_bundle --depth-fixture synthetic_static_smoke
```

Manifest-backed fixture benchmarks exit non-zero when a fixture is `DANGER` or
when its result is worse than the fixture's `expected_quality`.

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

To register that imported capture as a manifest-backed benchmark fixture, place
the output under `fixtures/depth` and provide a fixture name:

```powershell
python scripts/import_depth_capture.py path\to\depth_screenshots fixtures\depth\live_overlay_smoke --fixture-root fixtures\depth --fixture-name live_overlay_smoke --description "Live overlay depth-debug capture smoke fixture." --expected-quality WARN
python -m tracker.depth_fixtures --benchmark live_overlay_smoke
```

For renderer experiments, `tracker.depth_reprojection.synthesize_views` provides
a tested CPU reference path for generating mono, stereo, or quilt view stacks
from an RGB image and normalized depth map. Pass `confidence_mask` when a depth
source can identify low-confidence or disoccluded pixels; invalid samples are
filled rather than reprojected as false geometry.

To render a concrete stereo strip or quilt PNG from one RGB frame plus a `.npy`
depth map:

```powershell
python scripts/generate_depth_confidence.py depth.npy confidence.npy --max-gradient 0.25
python scripts/render_views.py image.png depth.npy stereo.png --backend stereo_autostereo --stereo-layout full_sbs --eye-order left_right
python scripts/render_views.py image.png depth.npy half_sbs.png --backend stereo_autostereo --stereo-layout half_sbs --eye-order right_left
python scripts/render_views.py image.png depth.npy top_bottom.png --backend stereo_autostereo --stereo-layout top_bottom
python scripts/render_views.py image.png depth.npy anaglyph.png --backend stereo_autostereo --stereo-layout anaglyph
python scripts/render_views.py image.png depth.npy configured.png --config config.yaml
python scripts/render_views.py image.png depth.npy quilt.png --backend lightfield_quilt
python scripts/render_views.py image.png depth.npy masked.png --backend stereo_autostereo --confidence-mask confidence.npy --fill-value 0
```

Offline stereo layouts include `full_sbs`, `half_sbs`, `top_bottom`,
`half_top_bottom`, `anaglyph`, `crossview`, and `parallelview`. These are
inspection and source-routing aids for community workflows such as SBS/OU,
red-cyan anaglyph, crossview/parallelview, and external stereo tools. They do
not change the live overlay ABI, which currently publishes `full_sbs` and
`half_sbs` calibration to the runtime.

To generate a deterministic validation card plus stereo/quilt output for a
display hardware check:

```powershell
python scripts/generate_stereo_validation.py validation_out --backend stereo_autostereo --width 640 --height 360 --stereo-layout full_sbs --eye-order left_right
python scripts/generate_stereo_validation.py validation_half_sbs --backend stereo_autostereo --width 640 --height 360 --stereo-layout half_sbs --eye-order right_left
python scripts/generate_stereo_validation.py validation_top_bottom --backend stereo_autostereo --width 640 --height 360 --stereo-layout top_bottom
python scripts/generate_stereo_validation.py validation_anaglyph --backend stereo_autostereo --width 640 --height 360 --stereo-layout anaglyph
python scripts/generate_stereo_validation.py validation_configured --config config.yaml --width 640 --height 360
python scripts/generate_stereo_validation.py validation_out_quilt --backend lightfield_quilt --width 320 --height 180
```

To create a single acceptance folder with diagnostics, configured validation
assets, and a pass/fail checklist for the active display backend:

```powershell
python scripts/run_display_acceptance.py acceptance_out --config config.yaml --require-live-runtime
```

The command always writes `acceptance_report.json`; its process exit code is
`0` only when that report has `"ready": true`.

For the required webcam/head-tracked desktop product gate, use the
`desktop_overlay` backend on a normal display. Physical stereo/quilt
target-display checks are optional extended evidence; for that handoff, use
`docs/HARDWARE_ACCEPTANCE_CHECKLIST.md`.

For physical glassless/autostereo hardware, add a manual observation YAML after
checking the generated validation image on the device:

```yaml
target_display_device_id: DISPLAY\ABC123\UID0
target_display_type: autostereo
eye_order_correct: true
depth_direction_correct: true
ui_readable: true
head_tracking_stable: true
crosstalk_percent: 8.0
crosstalk_limit_percent: 10.0
notes: view locks across the sweet spot
```

Hardware observation fields:

| Field | Required | Meaning |
|---|---|---|
| `eye_order_correct` | Yes | `true` only when left/right eye content is not swapped on the target display. |
| `depth_direction_correct` | Yes | `true` only when near objects appear nearer and far objects recede. |
| `ui_readable` | Yes | `true` only when desktop/UI text remains readable through the target display mode. |
| `head_tracking_stable` | Yes | `true` only when the view remains stable through the usable head box. |
| `crosstalk_percent` | Yes | Measured crosstalk percentage; must be finite, numeric, non-negative, and not above the active limit. |
| `target_display_device_id` | Required for physical non-desktop acceptance | Exact `display_inventory[].device_id` for the connected target display that was actually checked. |
| `target_display_type` | Required with `target_display_device_id` | One of `autostereo`, `glassless`, `lightfield`, `spatial`, `simulated_reality`, or `sr`. |
| `crosstalk_limit_percent` | Optional | Observation-file threshold. The CLI `--crosstalk-limit-percent` value overrides it when supplied. |
| `notes` | Optional | Free-form operator notes about the viewing zone, lighting, calibration, or display mode. |

If `crosstalk_limit_percent` is present in the observation file, that value is
used for acceptance. The command-line `--crosstalk-limit-percent` option
overrides the observation value when supplied. When an observation is supplied,
the acceptance folder also preserves it as `hardware_observation.yaml` or
`hardware_observation.json`, and the report records that relative path.

```powershell
python scripts/run_display_acceptance.py acceptance_out --config config.yaml --require-live-runtime --hardware-observation hardware_observation.yaml
python scripts/run_display_acceptance.py acceptance_out --config config.yaml --require-live-runtime --hardware-observation hardware_observation.yaml --crosstalk-limit-percent 7.5
```

For `stereo_autostereo` and `lightfield_quilt`, acceptance remains `ready:
false` until the hardware observation is provided. That prevents a software-only
runtime smoke check from being mistaken for physical-device acceptance.
These backends also require `display_inventory` to show a known target display
class, such as SpatialLabs, Odyssey 3D, ThinkVision 27 3D, Lume Pad,
Lume Pad 2/LeiaSR, Looking Glass, or Simulated Reality, before acceptance can
become ready. The hardware observation must still set
`target_display_device_id` to the exact `display_inventory[].device_id` value
for the display that was checked. Set
`target_display_type` to `autostereo`, `lightfield`, `glassless`, `spatial`,
`simulated_reality`, or `sr`. If `target_display_device_id` is provided with any
other type value, the acceptance report stays not ready and reports the allowed
values. A matching `device_id` on an ordinary generic monitor is still not
physical target-display evidence. The type must also match the configured
backend: `stereo_autostereo` accepts `autostereo`, `glassless`, `spatial`,
`simulated_reality`, or `sr`, while `lightfield_quilt` accepts `lightfield`,
`glassless`, or `spatial`.

Support bundles include the same display-acceptance folder when
`--require-live-runtime` is used, and include the hardware observation results
when `--hardware-observation` is also supplied:

```powershell
python scripts/collect_support.py --output-dir support_bundle --config config.yaml --require-live-runtime
python scripts/collect_support.py --output-dir support_bundle --config config.yaml --require-live-runtime --hardware-observation hardware_observation.yaml
python scripts/collect_support.py --output-dir support_bundle --config config.yaml --require-live-runtime --hardware-observation hardware_observation.yaml --crosstalk-limit-percent 7.5
python scripts/collect_support.py --output-dir support_bundle --config config.yaml --require-live-runtime --hardware-observation hardware_observation.yaml --require-display-acceptance-ready
```

Add `--source-stereo-path` when the acceptance artifact depends on a specific
upstream stereo/depth source. Accepted values are `overlay_depth_reprojection`,
`native_stereo`, `geo11`, `geo3d`, `depth3d`, `rendepth`, `3dgamebridge`,
`external_sbs`, `external_ou`, and `other`. This metadata is copied into the
acceptance report and support manifest so the source path is auditable, but it
does not affect pass/fail readiness.

```powershell
python scripts/run_display_acceptance.py acceptance_out --config config.yaml --require-live-runtime --source-stereo-path 3dgamebridge --source-stereo-notes "SBS converted for SR display"
python scripts/collect_support.py --output-dir support_bundle --config config.yaml --require-live-runtime --source-stereo-path geo11 --source-stereo-notes "Game-specific full SBS source"
```

The support bundle manifest includes `display_acceptance_ready` and
`display_acceptance_problems`, and the command prints the same acceptance status
after writing the bundle. Add `--require-display-acceptance-ready` for final
hardware gates that should exit nonzero when acceptance is not ready or no
acceptance report was generated.

To select the intended backend in `config.yaml` for diagnostics and support
artifacts:

```powershell
python scripts/set_display_backend.py stereo_autostereo
python scripts/set_display_backend.py lightfield_quilt
```

To record backend-specific calibration metadata for diagnostics/support bundles:

```powershell
python scripts/calibrate_display_backend.py stereo_autostereo --viewer-distance-cm 65 --view-cone-deg 35
python scripts/calibrate_display_backend.py stereo_autostereo --viewer-distance-cm 65 --view-cone-deg 35 --panel-resolution 3840x1080 --panel-width-cm 34.4 --panel-height-cm 19.3 --ipd-mm 63.5 --stereo-layout half_sbs --eye-order right_left --tracking-mode vendor_managed
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

To run a controlled live-runtime smoke check with the settings writer, fake
tracker, overlay process, and fresh diagnostics gate. The script removes the
previous `overlay.log` before launch so readiness is based on the current run,
including the configured-vs-runtime backend check:

```powershell
python scripts/run_live_runtime_check.py --config config.yaml --timeout 20
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
