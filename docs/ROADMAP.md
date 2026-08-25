# Glassless3D Roadmap

## Product Direction

Glassless3D is a Windows desktop glasses-free overlay runtime.
The standalone overlay is the primary supported backend.
ReShade and game-injected paths are experimental.
World of Warcraft is a later policy-aware feasibility gate, not a current target.

## Current State

- Tracker writes `G3D` and `FT_SharedMem`
- Launcher starts tracker and overlay
- Overlay captures the desktop and runs monocular depth inference
- Overlay depth inference has quality/balanced/fast modes, async worker inference, render-rate depth blending, and center-crop handling for ultrawide displays
- ReShade tooling still exists in the repo, but it should no longer define the main product story
- Video-Depth-Anything export is available as an optional offline/quality-benchmark tool, not the default low-latency runtime
- Deep research implementation status is tracked in `docs/DEEP_RESEARCH_STATUS.md`
- Deterministic pose replay and virtual-window geometry acceptance now provide the release-blocking software gate
- Physical hardware observations remain optional field-validation evidence rather than a release blocker
- Completion evidence is tracked in `docs/COMPLETION_AUDIT.md`

## Milestone Status

| Milestone | Status | Evidence or remaining gate |
|---|---|---|
| Calibration and tracking reliability | Software-gated | Calibration metadata is persisted, deterministic delayed/noisy/dropout replay is enforced in CI, and the replay tuner can atomically recommend filter values. |
| Overlay quality and temporal stability | Software-gated | Async adaptive depth, motion compensation, edge/disocclusion handling, deterministic parallax geometry, depth fixtures, and generated virtual-window validation frames are implemented. |
| Overlay UX and diagnostics | Done for local software gates | Readiness checks, startup errors, diagnostics, live-runtime check, support bundles, and troubleshooting docs are implemented. |
| Performance hardening | Software-gated | Adaptive depth modes, cadence/depth-age telemetry, persistent DirectML binding with fallback, timing export, and deterministic pose-latency replay are enforced. |
| Experimental backends | Hardware-gated | SBS stereo and 9x5 quilt software paths, validation images, and acceptance reports exist. Final quality needs physical autostereo/light-field hardware acceptance. |
| WoW feasibility gate | Deferred by design | The policy/technical gate remains closed by default and is separate from the overlay-first prototype. |
| Target-display acceptance | Optional field validation | Hardware observations and support bundles remain useful for device-specific defects, but deterministic software acceptance is the active release gate. |

## Milestone 1: Calibration And Tracking Reliability

- stabilize camera selection
- validate screen sizing and head-distance defaults
- keep tracker + overlay startup reliable
- measure tracking jitter, loss rate, and reacquisition in the debug monitor

## Milestone 2: Overlay Quality And Temporal Stability

- improve temporal depth stability
- improve edge handling and disocclusion behavior
- improve debug surfaces and operator feedback

## Milestone 3: Overlay UX And Diagnostics

- first-run overlay readiness checks
- actionable startup errors
- settings and troubleshooting docs
- repeatable evaluation guide for tracking, depth, performance, and comfort
- command-line diagnostics report for support and readiness checks

## Milestone 4: Performance Hardening

- inference cadence
- selectable depth performance mode
- frame pacing
- GPU/CPU pipeline profiling

## Milestone 5: Experimental Backends

- ReShade path retained as opt-in
- other display/game integrations only after overlay path is stable

## Milestone 6: WoW Feasibility Gate

- policy review
- technical viability review
- explicit go/no-go decision
