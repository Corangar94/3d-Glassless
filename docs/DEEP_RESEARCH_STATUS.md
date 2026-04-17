# Deep Research Implementation Status

This audit maps the deep-research recommendations to the current repository.
It distinguishes completed implementation from retained future work so the
roadmap does not imply that hardware-dependent or policy-gated items are done.

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
| Provide operator diagnostics | Launcher Advanced tab opens the tracking quality monitor; troubleshooting docs describe diagnostics and overlay-log warnings. |
| Add repeatable evaluation guidance | `docs/EVALUATION.md` defines tracking, depth, performance, comfort, and policy checks. |
| Provide combined benchmark entry point | `tracker.evaluation_suite` runs depth and frame-pacing benchmarks together and returns a worst-case quality result. |
| Provide shareable support artifacts | `launcher.support_bundle` writes diagnostics JSON and optional evaluation JSON into one support directory. |
| Keep WoW gated until explicit review | `tracker.feasibility_gate` provides a default closed WoW gate and CLI report. |

## Partially Implemented

| Recommendation | Current implementation | Remaining work |
|---|---|---|
| Temporal depth stabilization | Overlay already has asynchronous inference, previous/current depth textures, render-rate blend, EMA-style postprocess, crop handling, debug depth view, reusable depth-stability metrics, a `.npy` sequence benchmark CLI, and synthetic static/breathing depth fixture generation. | Add real captured-sequence fixtures and compare them against the synthetic baseline. |
| Performance hardening | Overlay logs render/acquisition/depth cadence, diagnostics parses the latest summary, reusable frame-pacing metrics cover average/p95/max frame time plus budget overruns, `tracker.performance_capture` writes compatible timing CSVs, and `tracker.performance_benchmark` classifies captured CSV timing data. | Add GPU timing queries and wire live overlay frame-time samples into the CSV writer/export path. |
| Depth-image-based reprojection | Current overlay performs depth-dependent inverse-warp parallax for a single desktop view. | Build a true stereo/two-view output path and later multiview/quilt synthesis. |
| Display abstraction layer | Product docs separate core overlay from experimental integrations, and `tracker.display_backends` defines stable backend IDs/status for desktop overlay, stereo autostereo, and light-field quilt targets. | Add real stereo/quilt renderer implementations behind the registered backend IDs. |
| Hooked game depth on friendly titles | ReShade path remains in the repo as experimental. | Build a policy-safe friendly-title depth capture prototype and compare it against monocular fallback. |

## Deferred By Design

| Recommendation | Reason |
|---|---|
| Dedicated eye tracker / Tobii integration | Requires hardware and SDK decisions after webcam tracking is proven to be the dominant error source. |
| RGB-D / stereo camera fusion | Useful research bench path, but not required before the overlay-first prototype is stable. |
| Phone-based tracking | Additional latency/calibration complexity; kept as an experimental future option. |
| Looking Glass / multiview backend | Requires display hardware and a separate quilt/multiview synthesis path. |
| WoW-specific runtime | Policy risk must be reviewed before any implementation beyond documentation and the closed-by-default `tracker.feasibility_gate` report. |
| Holographic display support | Research-level hardware/compute path, not a near-term implementation target. |

## Next Engineering Slices

1. Add real captured-frame fixtures for `tracker.depth_benchmark` and compare
   them against `tracker.depth_synthetic` baselines.
2. Add overlay frame-time and GPU timing instrumentation that can export CSV for
   `tracker.performance_benchmark`.
3. Implement real stereo or quilt output behind the `tracker.display_backends`
   IDs.
4. Add a friendly-title hooked-depth experiment only after the overlay
   diagnostics remain stable.
5. Run the WoW feasibility gate as a separate policy and technical review, not
   as an implementation task.
