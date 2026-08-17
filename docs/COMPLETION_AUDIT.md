# Completion Audit

This file maps the active "fully implement everything" request to concrete
artifacts and verification evidence. It should be updated whenever a new local
gate or hardware acceptance run is completed.

## Objective

Deliver the deep-research Glassless3D improvement pass far enough that the
normal-display webcam/head-tracked glassless path can run, diagnose itself,
package support evidence, and prove depth/parallax readiness without requiring
3D monitors or glasses. Stereo/quilt target-display artifacts remain optional
extended evidence.

## Last Verified

- Date: 2026-07-29
- Git revision: `34a80b2` with the current audit changes still uncommitted
- Full regression command: `python -m pytest tests/ -q`
- Full regression result after the final face-evidence hardening:
  `606 passed in 61.45s`; the focused diagnostics/acceptance/support/audit
  suite reports `114 passed in 36.41s`.
- Production static check:
  `pyright launcher tracker scripts`
  -> `0 errors, 0 warnings, 0 informations`.
- Native overlay build and recovery test pass; the root executable matches the
  build output at SHA-256
  `99DBE1E74E1630DDE72D7F4B90527460E59887C2FEF039C090911C13FCF9EC07`.
- PyInstaller 6.21 builds `dist/Glassless3D.exe` successfully, and a frozen
  `--tracker-child` missing-config smoke test exits nonzero instead of opening
  a second GUI.
- Synthetic live runtime diagnostics are READY. A real webcam run reached
  live capture, 25 Hz tracker writes, and 8 Hz depth inference, but the fresh
  `G3D_State` remained `paused` because no face was visible. Completion
  evidence now requires `--require-face-tracking`, so the desktop acceptance
  gate remains honestly pending until a face is visible to camera 0.
- Python compile check:
  `python -m compileall -q launcher tracker scripts tests` -> exit code `0`.
- Focused GUI calibration compile check:
  `python -m compileall -q launcher\mainwindow.py tests\test_mainwindow.py`
  -> exit code `0`.
- Focused runtime/settings static check:
  `pyright launcher\mainwindow.py launcher\settings_gui.py
  scripts\run_settings_writer.py tracker\shared_settings.py
  launcher\diagnostics.py` -> `0 errors, 0 warnings, 0 informations`.
- Focused stereo/calibration static check:
  `pyright tracker\view_renderer.py tracker\stereo_validation.py
  tracker\display_calibration.py scripts\generate_stereo_validation.py
  scripts\calibrate_display_backend.py` -> `0 errors, 0 warnings, 0 informations`.
- Focused acceptance/support gate check:
  `python -m pytest tests/test_display_acceptance.py tests/test_support_bundle.py
  tests/test_collect_support.py -q` -> `56 passed in 23.68s`; `pyright
  tracker\display_acceptance.py launcher\support_bundle.py
  scripts\collect_support.py scripts\run_display_acceptance.py` -> `0 errors,
  0 warnings, 0 informations`.
- Direct wrapper smoke check:
  `python -m pytest tests/test_script_wrappers_direct.py -q` -> `1 passed in
  3.79s`; Pyright on the operator-facing script wrappers reports `0 errors, 0
  warnings, 0 informations`.
- Completion-audit verifier check:
  `python -m pytest tests/test_completion_audit.py tests/test_script_wrappers_direct.py -q`
  -> `12 passed in 3.57s`; `pyright scripts\audit_completion.py
  tests\test_completion_audit.py` -> `0 errors, 0 warnings, 0 informations`.
- Latest focused completion-audit test:
  `python -m pytest tests/test_completion_audit.py -q` -> `15 passed in
  0.29s`, confirming the default completion audit accepts the camera-tracked
  desktop path on an ordinary monitor and the optional strict hardware mode
  still rejects incomplete target-display evidence.
- Community stereo-resource follow-up check:
  `python -m pytest tests/test_view_renderer.py tests/test_stereo_validation.py
  tests/test_display_acceptance.py tests/test_support_bundle.py
  tests/test_collect_support.py tests/test_script_wrappers_direct.py -q`
  -> `77 passed in 27.59s`; `pyright tracker\view_renderer.py
  tracker\stereo_validation.py tracker\display_acceptance.py
  launcher\support_bundle.py tests\test_view_renderer.py
  tests\test_stereo_validation.py tests\test_display_acceptance.py
  tests\test_support_bundle.py tests\test_collect_support.py` -> `0 errors,
  0 warnings, 0 informations`; `python -m compileall -q launcher tracker
  scripts tests` -> exit code `0`.
- Target-display matcher refactor check:
  `python -m pytest tests/test_target_displays.py tests/test_display_acceptance.py
  tests/test_completion_audit.py -q` -> `54 passed in 2.59s`; `pyright
  tracker\target_displays.py tracker\display_acceptance.py
  scripts\audit_completion.py tests\test_target_displays.py
  tests\test_display_acceptance.py tests\test_completion_audit.py` -> `0
  errors, 0 warnings, 0 informations`.
- Hardware-observation handoff and placeholder-evidence hardening check:
  `python -m pytest tests/test_completion_audit.py tests/test_display_acceptance.py -q`
  -> `54 passed in 2.51s`; `pyright scripts\audit_completion.py
  tracker\display_acceptance.py tests\test_completion_audit.py
  tests\test_display_acceptance.py` -> `0 errors, 0 warnings, 0
  informations`; focused `git diff --check` on the touched audit, acceptance,
  tests, and hardware-observation docs exits `0`.
- Overlay build check:
  `vendor\_mingw64\mingw64\bin\cmake.exe --build overlay/build_mingw --config Release`
  -> exit code `0`; root and build `Glassless3DOverlay.exe` are both 271870
  bytes with timestamp `2026-05-14 00:00:13`.
- Patch hygiene check:
  `git diff --check` -> exit code `0` after removing a trailing-space issue in
  `docs/ARCHITECTURE.md`; only CRLF conversion warnings remain.
- Referenced artifact check: the desktop acceptance report, ready support bundle
  manifest, stereo hardware-gate report, quilt hardware-gate report, and quilt
  `hardware_observation_template.yaml` all exist under `E:\CodexTemp` with the
  statuses documented below.
- Parsed artifact status check: the desktop acceptance report and desktop
  support manifests report ready; the stereo and quilt reports/manifests report
  not ready with only the expected target-display-inventory and
  hardware-observation blockers.
- Latest artifact readback check:
  a direct JSON parse of the current inventory, desktop acceptance, strict
  desktop support bundle, stereo hardware-gate acceptance/support artifacts, and
  quilt hardware-gate acceptance/support artifacts confirms the same state:
  desktop acceptance is ready, strict desktop support is ready, and stereo/quilt
  target-display support remains blocked only by missing target display
  inventory plus missing physical hardware observation.
- Automated completion-audit command:
  `python scripts/audit_completion.py` reads the current saved desktop
  acceptance/support artifacts and exits nonzero while the required
  webcam/head-tracked desktop gate is not ready, still reports problems, or its
  support manifest has a missing/non-string `display_acceptance` path, missing
  referenced acceptance-report file, non-self-contained bundle-relative path,
  or non-ready/problemful referenced acceptance-report content. Passing
  `--require-target-display-hardware` adds the optional strict target-display
  gate for stereo and quilt artifacts, including hardware-observation and
  target-display-observation checklist evidence.
- Current completion-audit command result:
  `python scripts\audit_completion.py` exits `0` with `completion audit: READY`
  for the required webcam/head-tracked desktop path. Optional stereo/quilt
  target-display evidence is checked only when
  `--require-target-display-hardware` is supplied.
- Conflict-marker scan:
  `rg -n "^(<<<<<<<|=======|>>>>>>>)" launcher tracker scripts tests docs overlay -S -g "!overlay/build_mingw/**"`
  -> no source conflict markers found.
- Current display-inventory artifact:
  refreshed with `python -m launcher.diagnostics --format json --config
  config.yaml --output E:\CodexTemp\glassless_current_display_inventory.json`.
  This is a desktop diagnostics inventory snapshot, not fresh overlay runtime
  acceptance; it warns that the overlay log is not fresh and skips runtime
  health checks. It shows default/generic monitors plus Samsung `SAM71AC`
  `5120x1440` primary, configured backend `desktop_overlay`, and no diagnostics
  problems; no known target glassless/autostereo/light-field panel is connected.
- Latest display-inventory refresh:
  the same diagnostics command was rerun and still reports no diagnostics
  problems, the stale-overlay-log warning, default/generic monitors plus Samsung
  `SAM71AC` `5120x1440` primary, with device id
  `DISPLAY\SAM71AC\5&1FBFC9D7&0&UID4352`, and no known target
  glassless/autostereo/light-field panel.
- Direct Windows monitor inventory check:
  `Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorID` reports one
  active physical monitor, `DISPLAY\SAM71AC\5&1fbfc9d7&0&UID4352_0`, manufacturer
  `SAM`, product `71AC`, user-friendly name `LS49AG95`.
- Windows PnP monitor cross-check:
  `Get-PnpDevice -Class Monitor` lists only Samsung monitor entries: one OK
  `Generic Monitor (LS49AG95)` at
  `DISPLAY\SAM71AC\5&1FBFC9D7&0&UID4352` and older/unknown
  `Generic Monitor (LC49G95T)` entries. No known SpatialLabs, Odyssey 3D,
  ThinkVision 27 3D, Lume/LeiaSR, Looking Glass, or Simulated Reality target
  display is present.
- Shared target-display matcher check:
  applying `tracker.target_displays.inventory_text_is_known_target(...)` to the
  current diagnostics inventory marks every `Default Monitor` entry and the
  Samsung `DISPLAY\SAM71AC\5&1FBFC9D7&0&UID4352` entry as `known_target: false`,
  confirming the audit blocker is not a missed allowlist match.
- Latest hardware-bound audit refresh:
  after refreshing `E:\CodexTemp\glassless_current_display_inventory.json`,
  `python scripts\audit_completion.py` still exits `1` with the same not-ready
  blockers: no known target display in inventory, stereo acceptance/support not
  ready, and quilt acceptance/support not ready.
- Stale artifact rejection check:
  running `python scripts\audit_completion.py` with older saved stereo/quilt
  `glassless_*_acceptance_audit` artifacts still exits `1`; the audit rejects
  those artifacts because the current inventory has no known target display,
  standalone stereo/quilt reports do not have
  `checklist.target_display_observation_matched: true`, and their support bundle
  acceptance evidence is not ready under the current hardware-observation
  requirements.
- Hardware-observation sweep:
  a scan of non-template `E:\CodexTemp\hardware_observation*.y*ml` files outside
  pytest scratch dirs found no valid target-display observation evidence. The
  only concrete device id was the current Samsung
  `DISPLAY\SAM71AC\5&1FBFC9D7&0&UID4352` marked as `ordinary_monitor`; the rest
  had empty, missing, or placeholder-style device ids.
- Hardware handoff doc:
  `docs/HARDWARE_ACCEPTANCE_CHECKLIST.md` lists the exact commands, observation
  fields, pass criteria, support-bundle files, and
  `docs/hardware_observation.example.yaml` handoff template required once
  target hardware is connected. The example YAML parses with all required
  observation keys but is explicitly marked as sample data.
- Hardware checklist command validation: `--help` output confirms the documented
  flags exist for `launcher.diagnostics`, `scripts/run_live_runtime_check.py`,
  `scripts/run_display_acceptance.py`, `scripts/collect_support.py`,
  `scripts/set_display_backend.py`, `scripts/calibrate_display_backend.py`,
  `scripts/generate_stereo_validation.py`, `scripts/render_views.py`, and
  `scripts/audit_completion.py`.
  Hardware support-bundle examples pass `--config config.yaml` so the collected
  evidence stays tied to the same target-backend setup used for runtime and
  display acceptance. Final hardware support-bundle examples also pass
  `--require-display-acceptance-ready`, and the hardware checklist documents
  the expected exit-code contract: exit `0` only when display acceptance is
  generated and ready, otherwise nonzero.
- Local live runtime command: `python scripts/run_live_runtime_check.py --config config.yaml --timeout 30 --poll-interval 1`
- Local live runtime result after overlay rebuild: diagnostics `READY`, runtime
  backend `desktop_overlay`, `hasFrame: True`, no problems, no warnings.
- Local desktop acceptance artifact:
  `E:\CodexTemp\glassless_desktop_acceptance_20260514_framegate\acceptance_report.json`
  with `"ready": true` and no problems.
- Local support bundle artifact:
  `E:\CodexTemp\glassless_support_20260514_postrebuild_ready\manifest.json`,
  with `display_acceptance_ready: true`, no display-acceptance problems, and
  `display_acceptance/acceptance_report.json` `"ready": true`.
- Strict desktop support-bundle ready artifact:
  `E:\CodexTemp\glassless_desktop_strict_support_ready_20260514\manifest.json`;
  after a fresh `desktop_overlay` live runtime, `collect_support.py
  --require-live-runtime --require-display-acceptance-ready` exited `0` with
  `display_acceptance_ready: true` and no display-acceptance problems.
- Current non-desktop hardware-gate artifact:
  `E:\CodexTemp\glassless_stereo_devicebind_gate_20260514\acceptance\acceptance_report.json`,
  with runtime/backend/calibration ready but `"ready": false` because target
  display inventory and hardware observation are missing.
- Strict stereo support-bundle hardware gate:
  `E:\CodexTemp\glassless_stereo_strict_support_gate_20260514\manifest.json`;
  after a fresh `stereo_autostereo` live runtime and the current strict
  target-display predicate, including compact/punctuated target-name matching
  and the latest observation-fix versus observation-fill `next_steps`,
  `collect_support.py --require-live-runtime --require-display-acceptance-ready`
  exited `1` with
  `display_acceptance_ready: false` and the expected missing target-display and
  hardware-observation problems.
- Current quilt hardware-gate artifact:
  `E:\CodexTemp\glassless_quilt_devicebind_gate_20260514\acceptance\acceptance_report.json`,
  with runtime/backend/calibration ready but `"ready": false` because target
  display inventory and hardware observation are missing. Its generated
  `hardware_observation_template.yaml` defaults `target_display_type` to
  `lightfield`.
- Strict quilt support-bundle hardware gate:
  `E:\CodexTemp\glassless_quilt_strict_support_gate_20260514\manifest.json`;
  after a fresh `lightfield_quilt` live runtime and the current strict
  target-display predicate, including compact/punctuated target-name matching
  and the latest observation-fix versus observation-fill `next_steps`,
  `collect_support.py --require-live-runtime --require-display-acceptance-ready`
  exited `1` with
  `display_acceptance_ready: false` and the expected missing target-display and
  hardware-observation problems.
- Local backend status: desktop software gates pass; non-desktop gates remain
  hardware-bound until a target display is connected and observed.

## Prompt-To-Artifact Checklist

| Requirement | Artifact or command | Current evidence |
|---|---|---|
| Reliable overlay-first runtime | `Glassless3DOverlay.exe`, `launcher/mainwindow.py`, `launcher/tracker_process.py`, `scripts/run_live_runtime_check.py` | After rebuilding the overlay, `python scripts/run_live_runtime_check.py --config config.yaml --timeout 30 --poll-interval 1` reached diagnostics `READY` with no problems or warnings on the desktop backend and `hasFrame: True`. Live acceptance now also requires the overlay log to report a captured frame. Overlay rebuild exits `0`, and root/build executable timestamps match. |
| Runtime backend/config calibration must match the overlay | `launcher/diagnostics.py`, `tracker/shared_settings.py`, `overlay/overlay.cpp`, `launcher/mainwindow.py`, `scripts/run_settings_writer.py` | Live checks verified desktop, `stereo_autostereo`, and `lightfield_quilt` configs with matching runtime backend, panel, IPD, focus plane, layout, and eye order where applicable. Latest quilt run reported runtime backend `lightfield_quilt`, panel `3840x2160`, `hasFrame: True`. The main window now preserves nested `display_calibration` fields across live settings changes and saves, so `stereo_layout`, `eye_order`, panel resolution, focus plane, and tracking mode do not fall back to defaults after a GUI edit. |
| Display validation artifacts for desktop, SBS stereo, and quilt | `tracker/stereo_validation.py`, `scripts/generate_stereo_validation.py`, `tracker/view_renderer.py` | `run_display_acceptance.py` generates `validation_source.png`, `validation_depth.npy`, and backend output PNGs referenced from `acceptance_report.json`. The offline renderer and validation CLI now also expose community inspection/source-routing layouts: `top_bottom`, `half_top_bottom`, `anaglyph`, `crossview`, and `parallelview`, in addition to `full_sbs` and `half_sbs`. These are software-only aids and do not change the live overlay ABI. |
| Acceptance gate with diagnostics and hardware observation | `tracker/display_acceptance.py`, `tracker/target_displays.py`, `scripts/run_display_acceptance.py`, `tests/test_display_acceptance.py`, `tests/test_target_displays.py` | Desktop acceptance can reach `"ready": true`; non-desktop acceptance requires matching runtime state, a passing observation, target-display evidence in `display_inventory`, and a `target_display_device_id` that binds the observation to a connected display that also looks like a known glassless/autostereo/light-field target. A matching id on an ordinary/generic monitor is not enough. Supplied observation device IDs must match connected inventory even on desktop reports. Known-target matching includes compact and punctuated device-id spellings such as `LOOKINGGLASS`, `SIMULATEDREALITY`, `ThinkVision27-3D`, `LeiaSR`, and `LumePad2`, not only spaced marketing names. The shared target-display matcher is used by both runtime acceptance and `scripts/audit_completion.py`, so their allowlists cannot drift. `target_display_detected` now reflects known target inventory, while `target_display_observation_matched` reflects the observation's bound device id/type and remains false when the type is incompatible with the configured backend; known target inventory without an observation points the operator to fill `hardware_observation.yaml` instead of reconnecting hardware. Incompatible or invalid supplied observations point the operator to fix `hardware_observation.yaml`, while an incomplete observation without connected target hardware reports both target-display connection and observation-fix next steps. `target_display_type` is validated whenever present, is required whenever a `target_display_device_id` is supplied, and must be compatible with the configured backend (`stereo_autostereo` versus `lightfield_quilt`). Hardware observation load/copy/type/device-id problems now block `"ready": true`, set `checklist.hardware_observation_passed: false`, and make the acceptance CLI exit nonzero instead of only appearing in `problems`. Not-ready reports include `next_steps` derived from failed checklist fields. Latest stereo and quilt runs on the current Samsung-only monitor exited nonzero with `target_display_detected: false` and `hardware_observation_required: true`; quilt templates now default `target_display_type: lightfield`. |
| Support bundle carries acceptance evidence | `launcher/support_bundle.py`, `scripts/collect_support.py`, `tests/test_support_bundle.py`, `tests/test_collect_support.py` | `collect_support.py --require-live-runtime` writes `manifest.json`, `diagnostics.json`, `overlay_timings.csv` when available, and `display_acceptance/acceptance_report.json`; the manifest also records `display_acceptance_ready` and `display_acceptance_problems`, and the CLI prints the same status. `--require-display-acceptance-ready` returns nonzero when a display-acceptance report is missing or not ready, giving final hardware scripts a strict gate while preserving normal diagnostic bundle collection. Acceptance reports and support manifests now also record optional `source_stereo` metadata so upstream paths such as `overlay_depth_reprojection`, `geo11`, `geo3d`, `depth3d`, `rendepth`, `3dgamebridge`, `external_sbs`, or `external_ou` are auditable without affecting readiness. Direct wrapper coverage asserts the strict/source flags are exposed and verifies the subprocess exits `1` when strict mode is requested without an acceptance report. Latest strict desktop bundle exited `0` with `display_acceptance_ready: true`; strict stereo/quilt bundles exited `1` with hardware-only blockers. |
| Top-level completion audit is machine-checkable | `scripts/audit_completion.py`, `tests/test_completion_audit.py` | `python scripts/audit_completion.py` reads the current saved desktop acceptance/support JSON files. It exits `0` when the required camera-tracked desktop artifact is ready, problem-free, and the support manifest points with a string path to an existing self-contained ready/problem-free `display_acceptance` artifact inside the same support bundle. `--require-target-display-hardware` keeps the stricter optional target-display audit for stereo/quilt artifacts, including inventory, hardware-observation payload, and true runtime/backend/calibration, hardware-observation, and target-display-observation checklist fields. |
| Connected display state is auditable | `launcher/diagnostics.py`, `tracker/display_acceptance.py` | Diagnostics and acceptance JSON include `display_inventory`. Current artifact `E:\CodexTemp\glassless_current_display_inventory.json` shows default/generic monitors plus one Samsung `SAM71AC` `5120x1440` primary monitor, with no known target glassless/autostereo/light-field panel. |
| Depth stability fixtures are repeatable | `tracker/depth_fixtures.py`, `tracker/depth_capture_import.py`, `scripts/import_depth_capture.py` | Imported depth-debug PNG captures can be registered into `fixtures/depth/manifest.json`; fixture `expected_quality` is enforced by `--benchmark` and `--benchmark-all`. |
| Operator docs describe the workflow | `docs/EVALUATION.md`, `docs/TROUBLESHOOTING.md`, `docs/DEEP_RESEARCH_STATUS.md`, `docs/HARDWARE_ACCEPTANCE_CHECKLIST.md`, `docs/hardware_observation.example.yaml` | Docs include live runtime checks, acceptance commands, support bundle commands, hardware observation YAML, crosstalk threshold behavior, external Stereo3Dgaming resource alignment, source-stereo metadata, and a standalone target-display acceptance checklist. The checklist commands were validated against current `--help` output, and target-display support-bundle examples explicitly pass `--config config.yaml` to keep runtime, acceptance, and support evidence on the same setup. The standalone observation example is marked as sample data and must be copied and replaced with real target-display observations before acceptance. |
| Direct-run script commands work from repo root | `scripts/*.py`, `tests/test_script_wrappers_direct.py` | Direct `--help` smoke coverage exists for documented wrappers after adding repo-root import setup, including completion-audit, support-bundle, display-acceptance, stereo-validation, settings-writer, and live-runtime wrappers. Latest direct wrapper smoke: `python -m pytest tests/test_script_wrappers_direct.py -q` -> `1 passed in 3.79s`; Pyright on `launcher tracker scripts` reports `0 errors`, including the new completion-audit wrapper. |
| Regression suite remains green | `tests/` | Latest full run: `python -m pytest tests/ -q` -> `490 passed in 50.74s`. Focused runtime/settings/stereo/calibration slice `python -m pytest tests/test_mainwindow.py tests/test_settings_gui.py tests/test_run_settings_writer.py tests/test_shared_settings.py tests/test_diagnostics.py tests/test_view_renderer.py tests/test_stereo_validation.py tests/test_display_calibration.py tests/test_generate_stereo_validation.py tests/test_calibrate_display_backend.py -q` -> `110 passed in 14.85s`. Focused acceptance/support slice `python -m pytest tests/test_display_acceptance.py tests/test_support_bundle.py tests/test_collect_support.py -q` -> `56 passed in 23.68s`. Production Pyright on `launcher tracker scripts` reports `0 errors`; acceptance/support Pyright reports `0 errors`; `python -m compileall -q launcher tracker scripts tests` exits `0`; `git diff --check` exits `0`. |

## Deep-Research Report Coverage

The original deep-research report proposed risk-retiring milestones. This table
maps those milestones to the current repo so the broad "fully implement
everything" request is not treated as complete until hardware-bound and
policy-bound gates have real evidence.

| Report milestone or recommendation | Current artifact | Completion status |
|---|---|---|
| Calibration bench with display-mounted tracking and display-space head/eye coordinates | `launcher/calibration.py`, `tracker/evaluation.py`, `tracker/debug_monitor.py`, launcher readiness flow, diagnostics, and calibration persistence tests | Software path implemented and regression-covered. Physical no-drift seated-session evidence still depends on live hardware/session capture. |
| Stereo autostereo proof with correct left/right separation, comfortable static/moving depth, and acceptable crosstalk | `stereo_autostereo` backend, `tracker/stereo_validation.py`, `tracker/view_renderer.py`, `scripts/run_display_acceptance.py`, hardware observation schema | Software artifact generation exists. Final proof is not complete until a real autostereo target display passes strict acceptance with observed eye order, depth direction, readability, tracking stability, and crosstalk. |
| Hooked depth prototype on friendly/offline title | Experimental ReShade/friendly-depth tooling and `tracker.friendly_depth_experiment` remain isolated from the overlay-first default | Not a current product-completion gate. It remains intentionally policy-gated and should only move after explicit approval for a friendly-title live prototype. |
| Temporal stabilization pass for watery depth | Overlay async depth inference, previous/current depth textures, render-rate blending, confidence masks, depth fixtures, fixture import, comparison tooling, and benchmark gates | Implemented for synthetic/offline fixtures. Real live-captured sequence fixtures are still missing until representative runtime captures are selected. |
| Multiview/light-field backend | `lightfield_quilt` backend, 9x5 layout, shared-settings backend/calibration ABI, quilt validation output, strict hardware acceptance gate | Software path implemented. Final multiview quality remains hardware-gated until a physical light-field target passes acceptance. |
| Performance hardening with stage timing and native-refresh evidence | Overlay cadence logs, GPU draw timing, diagnostics parsing, timing CSV export, performance benchmarks, support bundle `overlay_timings.csv` capture | Software instrumentation exists. Native-refresh proof on target display in representative scenes remains unavailable without target hardware/session data. |
| WoW feasibility gate | `tracker.feasibility_gate`, docs/roadmap/troubleshooting policy framing | Deferred by design. It is not implemented as runtime support and must remain a separate explicit policy/technical go/no-go review. |
| Evaluation methodology covering depth, tracking, display quality, performance, and comfort | `docs/EVALUATION.md`, support bundle evaluation inputs, diagnostics, display acceptance, comfort/display survey hooks | Method and tooling exist. Full subjective/physical display study is not complete without target hardware and session observations. |

## Optional Target-Display Gate

The required product path is the webcam/head-tracked desktop mode on an ordinary
display. Stereo/quilt target-display quality remains optional extended
evidence. To audit that optional path, connect a real target display and run
`python scripts/audit_completion.py --require-target-display-hardware`.

The optional hardware evidence is:

- `display_inventory` showing a known target glassless/autostereo/light-field panel.
- A fresh live runtime summary for the target backend.
- A real `hardware_observation.yaml` or `.json` filled after viewing the
  validation output on that physical display, including a
  `target_display_device_id` that matches a connected inventory entry and a
  valid `target_display_type`.
- `acceptance_report.json` with `"ready": true`, no problems, and preserved
  `hardware_observation_path`.
- A support bundle collected from the same run.

Use `docs/HARDWARE_ACCEPTANCE_CHECKLIST.md` for the exact optional
target-display workflow and expected evidence.

Without those physical measurements, any stereo/quilt run is a software-path
smoke test, not final target-display acceptance, but it does not block the
webcam/head-tracked desktop completion gate.
