# Glassless3D Troubleshooting

Start with the overlay-first diagnostics report:

```powershell
python -m launcher.diagnostics
```

To save a shareable support report:

```powershell
python -m launcher.diagnostics --output diagnostics.txt
```

For scripts or issue templates:

```powershell
python -m launcher.diagnostics --format json --output diagnostics.json
```

To collect a support directory with diagnostics and optional evaluation data:

```powershell
python scripts/collect_support.py --output-dir support_bundle
```

When `overlay.log` has multiple runtime summaries, the bundle also includes
`overlay_timings.csv` for frame-pacing analysis.

With benchmark inputs:

```powershell
python scripts/collect_support.py --output-dir support_bundle --depth-dir path\to\depth_frames --timing-csv path\to\timings.csv
```

With generated display-acceptance assets that require an already-running fresh
overlay runtime summary:

```powershell
python scripts/collect_support.py --output-dir support_bundle --config config.yaml --require-live-runtime
```

With physical glassless/autostereo hardware observations added to the acceptance
report. The support bundle preserves the original observation file inside
`display_acceptance/` so the parsed report remains auditable:

```powershell
python scripts/collect_support.py --output-dir support_bundle --config config.yaml --hardware-observation hardware_observation.yaml --require-live-runtime
python scripts/collect_support.py --output-dir support_bundle --config config.yaml --hardware-observation hardware_observation.yaml --require-live-runtime --crosstalk-limit-percent 7.5
python scripts/collect_support.py --output-dir support_bundle --config config.yaml --hardware-observation hardware_observation.yaml --require-live-runtime --require-display-acceptance-ready
```

When sharing a support bundle, include:

- `manifest.json`
- `diagnostics.json`
- `display_acceptance/acceptance_report.json` when present
- `display_acceptance/hardware_observation.yaml` or `.json` when present
- `overlay_timings.csv` when present

For a standalone pass/fail display gate, use
`scripts/run_display_acceptance.py`. It writes the acceptance JSON even when it
exits nonzero because the runtime or hardware checklist is not ready.
For the full physical target-display workflow, use
`docs/HARDWARE_ACCEPTANCE_CHECKLIST.md`.

Run `scripts/run_live_runtime_check.py --config config.yaml` first when you want
the toolchain to launch the settings writer, fake tracker, and overlay for a
controlled live smoke check.

In `acceptance_report.json`, inspect `ready`, `problems`, and the `checklist`
fields first. The checklist covers runtime freshness, configured-vs-runtime
backend match, calibration match, hardware observation status, crosstalk limit,
and validation asset generation. The report also embeds `display_inventory` so
the acceptance artifact shows which monitors Windows detected during collection.
When the report is not ready, `next_steps` lists the immediate follow-up actions
derived from the failed checklist fields.
Non-desktop backends require this inventory to include a known target display
class before physical acceptance can pass. Always copy the exact
`display_inventory[].device_id` for the checked target display into
`hardware_observation.yaml` as `target_display_device_id` and set
`target_display_type` to one of `autostereo`, `glassless`, `lightfield`,
`spatial`, `simulated_reality`, or `sr`. A matching `device_id` on an ordinary
generic monitor is not enough for physical target-display acceptance. If the
report says `target_display_type ... is not compatible`, use a type that
matches the configured backend: SBS stereo uses `autostereo`, `glassless`,
`spatial`, `simulated_reality`, or `sr`; quilt uses `lightfield`, `glassless`,
or `spatial`.

External stereo-3D setup resources help choose the content path, not the final
hardware proof. 3DGameBridge/SR setups, Geo-11/Geo3D fixes, Depth3D/Rendepth
fallbacks, and SBS glasses can all be useful while debugging source stereo
content or eye order. The final non-desktop support bundle still needs the
actual target display inventory entry plus a passing hardware observation from
that display.

The support-bundle `manifest.json` also records `display_acceptance`,
`display_acceptance_ready`, and `display_acceptance_problems`; the console
output prints the same status after the bundle is written. Use
`--require-display-acceptance-ready` when a scripted hardware gate should fail
if display acceptance is not ready or no acceptance report was generated.

For the final prompt-to-artifact completion check, run
`scripts/audit_completion.py` against the saved diagnostics, acceptance, and
support JSON files. It returns `0` only when desktop, stereo, and quilt
acceptance/support artifacts are ready, problem-free, and tied to a known target
display in `display_inventory`. Support manifests must point to an existing
ready/problem-free `display_acceptance` report inside the same bundle. For
stereo and quilt artifacts, the audit also requires a preserved
`hardware_observation_path` plus true runtime, backend, calibration,
hardware-observation, and target-display-observation checklist fields.

The report checks:

- overlay executable discovery
- depth model discovery
- config file readability
- configured display backend ID and output layout
- connected display inventory in `display_inventory`
- latest `overlay.log` runtime summary, when available
- primary and experimental display backend status
- project root and Python executable
- useful recovery commands

If the report says `NOT READY`, fix the listed problems before tuning depth or
tracking. The most common recovery step is:

```powershell
python scripts/bootstrap.py
```

## Tracking Feels Weak Or Jittery

Open the live monitor:

```powershell
python -m tracker.debug_monitor
```

Or use the launcher Advanced tab: `Diagnostics` -> `Open tracking quality
monitor`.

Treat the tracking-quality panel as the first gate:

- `GOOD`: proceed to depth tuning
- `WARN`: usable for debugging, but not for judging final 3D quality
- `DANGER`: fix lighting, camera placement, camera selection, or occlusion first

## Depth Looks Flat

Check the diagnostics report for a missing depth model. The overlay can still
run without the model, but it will use flat fallback depth.

Then press `Ctrl+D` in the overlay to show the depth debug view. If the depth
view is almost uniform, the depth model is not producing useful scene structure
for the current content.

## Overlay Does Not Start

Run:

```powershell
python -m launcher.diagnostics
```

If `Glassless3DOverlay.exe` is missing, rebuild with:

```powershell
python scripts/bootstrap.py
```

If the launcher status shows `OVERLAY ERROR`, hover the status label for the
actionable startup message.

## Overlay Log Warnings

When `overlay.log` is present, `python -m launcher.diagnostics` parses the
latest once-per-second summary and warns about:

- stale tracker shared memory
- no active depth inference
- no captured desktop frame

These warnings are runtime health signals. They do not replace the live tracking
quality monitor, but they make support reports useful even after the overlay has
been closed.

### Capture unavailable or recovering

The standalone overlay uses desktop capture only. It never changes into an
injected game backend when capture cannot be used.

- `target_spans_output`: Move the game's client area fully onto one display.
  Cross-display capture is intentionally disabled.
- `no_matching_output`: Enable or reconnect the display that contains the game
  window, then wait for the overlay to rebind.
- `duplicate_unavailable`: Use a normal local desktop session and check whether
  protected or display-only content is active.
- `device_lost`: Wait for the graphics driver/display to settle. The overlay
  rebuilds its renderer and capture binding with bounded retries.

## ReShade And Protected Games

ReShade and game-injected integrations are experimental. Do not use them as the
default troubleshooting path for the standalone overlay. World of Warcraft and
protected multiplayer titles remain policy-gated feasibility work, not the
primary runtime target.

To print the current WoW feasibility gate:

```powershell
python -m tracker.feasibility_gate wow
```

For a machine-readable gate artifact:

```powershell
python -m tracker.feasibility_gate wow --format json --output feasibility_wow.json
```
