# Hardware Acceptance Checklist

Use this checklist only after connecting the real target
glassless/autostereo/light-field display. Software-only stereo or quilt runs are
smoke tests, not final hardware acceptance.

## 1. Confirm Display Inventory

Run diagnostics and save the current monitor inventory:

```powershell
python -m launcher.diagnostics --format json --config config.yaml --output hardware_diagnostics.json
```

In `hardware_diagnostics.json`, find the target display in
`display_inventory`. Copy its exact `device_id`; this value must be used in the
hardware observation file. The inventory entry must identify a known
glassless/autostereo/light-field target class, such as SpatialLabs, Odyssey 3D,
ThinkVision 27 3D, Lume Pad or Lume Pad 2/LeiaSR, Looking Glass, or Simulated
Reality. A matching `device_id` on an ordinary generic monitor is not enough
for physical non-desktop acceptance.

Community stereo-3D resources such as the r/Stereo3Dgaming wiki are useful for
choosing the upstream stereo path. For example, 3DGameBridge can convert SBS
content for Simulated Reality targets such as SpatialLabs and Odyssey 3D, and
Geo-11, Geo3D, Depth3D, or Rendepth can help produce stereo/depth content for
specific games. Those tools do not replace this inventory gate: final
acceptance still requires the actual checked display to appear in
`display_inventory` as the target device.

## 2. Configure The Target Backend

For side-by-side autostereo panels:

```powershell
python scripts/set_display_backend.py stereo_autostereo
python scripts/calibrate_display_backend.py stereo_autostereo --panel-resolution 3840x1080 --ipd-mm 63.5 --stereo-layout half_sbs --eye-order right_left
```

For quilt/light-field panels:

```powershell
python scripts/set_display_backend.py lightfield_quilt
python scripts/calibrate_display_backend.py lightfield_quilt --panel-resolution 3840x2160 --ipd-mm 63.5
```

Adjust panel resolution, IPD, layout, eye order, focus plane, and tracking mode
to match the actual device.

SBS glasses or viewers such as Xreal/Rokid can be useful for checking the
side-by-side image path, especially with `full_sbs`, but they are not final
glassless/autostereo/light-field acceptance hardware for this checklist unless
the device is also the intended target display class and appears in
`display_inventory` as such.

## 3. Verify Fresh Runtime

Run the live runtime gate:

```powershell
python scripts/run_live_runtime_check.py --config config.yaml --timeout 30 --poll-interval 1
```

Required result:

- diagnostics status is `READY`
- runtime backend matches the configured backend
- `hasFrame: True`
- no problems
- no warnings that affect the target backend

## 4. Fill Hardware Observation

Copy `docs/hardware_observation.example.yaml` to `hardware_observation.yaml`,
then replace every value after viewing the generated validation image on the
physical target display:

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

For quilt/light-field hardware, use:

```yaml
target_display_type: lightfield
```

Required rules:

- `target_display_device_id` must exactly match a connected
  `display_inventory[].device_id` for a known target display entry.
- `target_display_type` must be one of `autostereo`, `glassless`, `lightfield`,
  `spatial`, `simulated_reality`, or `sr`.
- `target_display_type` must also match the configured backend:
  `stereo_autostereo` accepts `autostereo`, `glassless`, `spatial`,
  `simulated_reality`, or `sr`; `lightfield_quilt` accepts `lightfield`,
  `glassless`, or `spatial`.
- all boolean fields must be true for acceptance
- `crosstalk_percent` must be finite, numeric, non-negative, and not above the
  active crosstalk limit

## 5. Run Display Acceptance

```powershell
python scripts/run_display_acceptance.py acceptance_out --config config.yaml --require-live-runtime --require-face-tracking --hardware-observation hardware_observation.yaml
```

Required result in `acceptance_out/acceptance_report.json`:

- `"ready": true`
- `"problems": []`
- `checklist.runtime_ready: true`
- `checklist.backend_match: true`
- `checklist.calibration_match: true`
- `checklist.hardware_observation_passed: true`
- `checklist.target_display_observation_matched: true`
- `hardware_observation_path` points to the preserved observation file

## 6. Collect Support Bundle

Collect the support bundle from the same setup:

```powershell
python scripts/collect_support.py --output-dir support_bundle --config config.yaml --require-live-runtime --require-face-tracking --hardware-observation hardware_observation.yaml --require-display-acceptance-ready
```

Required result in `support_bundle/manifest.json`:

- command exit code is `0`
- `display_acceptance_ready: true`
- `display_acceptance_problems: []`
- `display_acceptance: "display_acceptance/acceptance_report.json"`
- `display_acceptance/acceptance_report.json` exists inside the same support
  bundle and is itself `"ready": true` with `"problems": []`
- for stereo/quilt hardware, that bundled acceptance report also includes the
  preserved `hardware_observation_path` and the true hardware checklist fields
  listed in step 5

With `--require-display-acceptance-ready`, the command exits nonzero if the
display-acceptance report is missing or not ready.

## 7. Run Completion Audit

The normal completion audit only requires the webcam/head-tracked desktop
acceptance/support path. After collecting optional stereo and quilt
target-display acceptance/support artifacts, run the strict target-display audit
against the saved JSON files:

```powershell
python scripts/audit_completion.py --require-target-display-hardware --inventory hardware_diagnostics.json --desktop-acceptance acceptance_out\acceptance_report.json --desktop-support support_bundle\manifest.json --stereo-acceptance stereo_acceptance\acceptance_report.json --stereo-support stereo_support\manifest.json --quilt-acceptance quilt_acceptance\acceptance_report.json --quilt-support quilt_support\manifest.json
```

Required result:

- command exit code is `0`
- output includes `completion audit: READY`

The audit exits nonzero if target-display inventory is missing, any
acceptance/support artifact is not ready, or any ready artifact still reports
problems. It also rejects support manifests whose `display_acceptance` path is
missing, points outside the support bundle, or points to an acceptance report
that is not ready. For stereo/quilt support bundles, the referenced acceptance
report must also carry the same hardware-observation and target-display
checklist evidence required of the standalone acceptance report.

Keep these files together as the final hardware evidence:

- `support_bundle/manifest.json`
- `support_bundle/diagnostics.json`
- `support_bundle/display_acceptance/acceptance_report.json`
- `support_bundle/display_acceptance/hardware_observation.yaml`
- `support_bundle/overlay_timings.csv` when present
