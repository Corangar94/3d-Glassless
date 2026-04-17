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

## Partially Implemented

| Recommendation | Current implementation | Remaining work |
|---|---|---|
| Temporal depth stabilization | Overlay already has asynchronous inference, previous/current depth textures, render-rate blend, EMA-style postprocess, crop handling, debug depth view, reusable depth-stability metrics, and a `.npy` sequence benchmark CLI. | Add captured-sequence fixtures and compare against known-depth synthetic scenes. |
| Performance hardening | Overlay logs render/acquisition/depth cadence and diagnostics parses the latest summary. | Add frame-time percentiles, GPU timing queries, and explicit pass/fail performance thresholds. |
| Depth-image-based reprojection | Current overlay performs depth-dependent inverse-warp parallax for a single desktop view. | Build a true stereo/two-view output path and later multiview/quilt synthesis. |
| Display abstraction layer | Product docs now separate core overlay from experimental integrations. | Add code-level backend interfaces for stereo autostereo, quilt/light-field, and future vendor SDKs. |
| Hooked game depth on friendly titles | ReShade path remains in the repo as experimental. | Build a policy-safe friendly-title depth capture prototype and compare it against monocular fallback. |

## Deferred By Design

| Recommendation | Reason |
|---|---|
| Dedicated eye tracker / Tobii integration | Requires hardware and SDK decisions after webcam tracking is proven to be the dominant error source. |
| RGB-D / stereo camera fusion | Useful research bench path, but not required before the overlay-first prototype is stable. |
| Phone-based tracking | Additional latency/calibration complexity; kept as an experimental future option. |
| Looking Glass / multiview backend | Requires display hardware and a separate quilt/multiview synthesis path. |
| WoW-specific runtime | Policy risk must be reviewed before any implementation beyond documentation and feasibility gating. |
| Holographic display support | Research-level hardware/compute path, not a near-term implementation target. |

## Next Engineering Slices

1. Add captured-frame fixtures for `tracker.depth_benchmark` and known-depth
   synthetic scenes.
2. Add overlay frame-time and GPU timing instrumentation beyond the current
   once-per-second summary log.
3. Define a code-level display backend interface before implementing stereo or
   quilt output.
4. Add a friendly-title hooked-depth experiment only after the overlay
   diagnostics remain stable.
5. Run the WoW feasibility gate as a separate policy and technical review, not
   as an implementation task.
