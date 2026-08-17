# Deep Research Implementation Status

This audit maps the deep-research recommendations to the current repository.
It distinguishes completed implementation from retained future work so the
roadmap does not imply that hardware-dependent or policy-gated items are done.
For prompt-to-artifact completion evidence, see `docs/COMPLETION_AUDIT.md`.

## Implemented

| Recommendation | Current implementation |
|---|---|
| Make the standalone Windows overlay the primary runtime | First-run wizard, architecture docs, roadmap, diagnostics, and launcher startup now center `launcher -> tracker -> overlay`. |
| Demote ReShade and game-injected paths | ReShade tooling is retained as experimental; default onboarding no longer installs or explains ReShade first. |
| Treat World of Warcraft as a later policy-aware feasibility gate | Roadmap, architecture, troubleshooting, and evaluation docs frame WoW as deferred policy/technical review. |
| Validate overlay readiness before tuning | Wizard readiness page and `python -m launcher.diagnostics` check overlay executable, depth model, config, and overlay-log health. |
| Surface actionable overlay startup failures | `OverlayProcess` raises actionable errors; the main window shows `OVERLAY ERROR` with tooltip detail. |
| Keep tracker and overlay startup reliable | `TrackerProcess.start()` returns success/failure; launcher rolls back UI on launch failure; regression tests cover the path. |
| Persist verified calibration updates | Screen-size detection and head-distance measurement save only known-good values; failed measurement keeps existing config. |
| Measure tracking jitter, loss, and reacquisition | `tracker.evaluation` computes metrics; `tracker.debug_monitor` shows rolling quality status. |
| Provide operator diagnostics | Launcher Advanced tab opens the tracking quality monitor; diagnostics include connected display inventory, and troubleshooting docs describe diagnostics and overlay-log warnings. |
| Add repeatable evaluation guidance | `docs/EVALUATION.md` defines tracking, depth, performance, tracking-to-display latency, comfort, crosstalk, view-zone width, UI readability, and policy checks. |
| Provide combined benchmark entry point | `tracker.evaluation_suite` runs depth, frame-pacing, optional latency, optional comfort/display survey, and optional objective display-zone benchmarks together and returns a worst-case quality result. |
| Provide shareable support artifacts | `launcher.support_bundle` writes diagnostics JSON, optional evaluation JSON including latency, comfort/display survey, and display-zone data, display-backend acceptance reports with optional hardware observations, and policy gate artifacts into one support directory. |
| Add repeatable depth validation fixtures | `tracker.depth_fixtures` loads manifest-backed `.npy` depth fixtures, the repo includes a `synthetic_static_smoke` baseline, both evaluation suite/support bundles accept `--depth-fixture`, and `scripts/import_depth_capture.py` can import depth-debug PNG captures directly into a manifest entry with an enforced `expected_quality` gate. |
| Keep WoW gated until explicit review | `tracker.feasibility_gate` provides a default closed WoW gate and CLI report. |
| Keep low-cadence depth usable | The overlay now exposes quality/balanced/fast depth performance modes through `G3D_Settings`, the launcher persists `overlay.depth_performance_mode`, diagnostics parses/logs active mode, and the root overlay executable is rebuilt from the committed CMake source. |
| Provide an offline temporal-depth upgrade path | `scripts/export_vda_onnx.py` can export Video-Depth-Anything-Small to the same fixed 518x518 ONNX shape that the overlay consumes; this is retained as an optional/offline quality benchmark path, not the default runtime. |

## Partially Implemented

| Recommendation | Current implementation | Remaining work |
|---|---|---|
| Temporal depth stabilization | Overlay already has asynchronous inference, previous/current depth textures, render-rate blend, edge-aware EMA-style postprocess, crop handling, debug depth view, reusable depth-stability metrics, a `.npy` sequence benchmark CLI, manifest-backed fixture discovery, synthetic static/breathing depth fixture generation, depth-debug screenshot import/registration, spatial/temporal confidence-mask generation, captured-vs-baseline comparison tooling, and manifest `expected_quality` enforcement. | Add real live-captured sequence fixtures after selecting representative hardware/runtime captures. |
| Performance hardening | Overlay logs render/acquisition/depth cadence plus D3D11 timestamp-query GPU draw timing, diagnostics parses the latest summary, reusable frame-pacing metrics cover average/p95/max frame time plus budget overruns, `tracker.performance_capture` writes compatible timing CSVs, exports approximate overlay-log cadence samples, support bundles include `overlay_timings.csv` when available, `tracker.performance_benchmark` classifies captured CSV timing data, and `tracker.latency_evaluation` classifies measured tracking-to-display latency captures against the deep-research target. | Add deeper present/vsync latency instrumentation if needed after live testing. |
| Depth-image-based reprojection | Current overlay performs depth-dependent inverse-warp parallax for desktop, side-by-side stereo, and 9x5 quilt layouts; `tracker.depth_reprojection` provides a tested CPU reference synthesizer with optional confidence masking for unreliable/disoccluded samples; `tracker.view_renderer` renders offline stereo/quilt PNG artifacts from RGB+depth inputs; and desktop, stereo, and quilt runtime acceptance paths have been software-verified with fresh overlay diagnostics. | Live-test perceived stereo/quilt quality on target display hardware. |
| Display abstraction layer | Product docs separate core overlay from experimental integrations, `tracker.display_backends` defines stable backend IDs/status plus concrete 1x1, 2x1 stereo, and 9x5 quilt layout contracts, `overlay.display_backend` can be validated and updated through operator tooling, the live `G3D_Settings` ABI publishes backend mode plus calibration fields to the overlay shader, diagnostics validates configured-vs-runtime backend/calibration, and display acceptance reports package validation assets plus optional hardware observations. Non-desktop acceptance now requires target-display evidence in `display_inventory`. | Live-tune calibration values and replace software-path smoke observations with real target-display measurements on autostereo/light-field panels. |
| Hooked game depth on friendly titles | ReShade path remains in the repo as experimental, and `tracker.friendly_depth_experiment` provides a policy-safe offline comparator for approved friendly-title external depth captures against monocular fallback. | Build a live friendly-title capture prototype only after explicit policy and technical approval. |

## Deferred By Design

| Recommendation | Reason |
|---|---|
| Dedicated eye tracker / Tobii integration | Requires hardware and SDK decisions after webcam tracking is proven to be the dominant error source. |
| RGB-D / stereo camera fusion | Useful research bench path, but not required before the overlay-first prototype is stable. |
| Phone-based tracking | Additional latency/calibration complexity; kept as an experimental future option. |
| Looking Glass / vendor Bridge integration | The generic 9x5 quilt path exists, but device-specific Bridge calibration, SDK handoff, and hardware validation require target display hardware. |
| WoW-specific runtime | Policy risk must be reviewed before any implementation beyond documentation and the closed-by-default `tracker.feasibility_gate` report. |
| Holographic display support | Research-level hardware/compute path, not a near-term implementation target. |

## Community Resource Alignment

The r/Stereo3Dgaming wiki was reviewed as an external solution map. Its software
page points to established game-specific stereo paths such as Geo-11/3Dmigoto,
Geo3D, Depth3D, and 3DGameBridge. Those tools are useful references and
fallbacks, but the current prototype intentionally remains overlay-first:
desktop capture, monocular depth reprojection, offline SBS/quilt validation, and
support-bundle acceptance artifacts. Its hardware page also reinforces the
remaining blocker: SR/autostereo targets such as Acer SpatialLabs, Samsung
Odyssey 3D, Lenovo ThinkVision 27 3D, and Lume Pad-style devices require
physical-device validation before any final stereo/quilt quality claim.
The same resource also frames SBS glasses and viewers as useful output checks;
for this project they remain diagnostic aids, not substitutes for the
glassless/autostereo/light-field hardware acceptance gate.

## Next Engineering Slices

1. Add real live-captured depth-debug sequences to `fixtures/depth/manifest.json`
   after live validation.
2. Live-test stereo/quilt output on target display hardware using
   `scripts/run_display_acceptance.py acceptance_out --config config.yaml --require-live-runtime --hardware-observation hardware_observation.yaml`
   or `scripts/collect_support.py --output-dir support_bundle --config config.yaml --require-live-runtime --hardware-observation hardware_observation.yaml --require-display-acceptance-ready`,
   then tune hardware-specific calibration values. Use
   `--crosstalk-limit-percent` when the target display has a stricter
   acceptance threshold.
3. Run the WoW feasibility gate as a separate policy and technical review, not
   as an implementation task.
