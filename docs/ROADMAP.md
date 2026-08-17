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
- Completion evidence and remaining hardware gates are tracked in `docs/COMPLETION_AUDIT.md`

## Milestone Status

| Milestone | Status | Evidence or remaining gate |
|---|---|---|
| Calibration and tracking reliability | Done for the overlay-first software path | Calibration metadata is persisted and diagnostics validate configured-vs-runtime settings. Tracking quality is measured by the debug monitor/evaluation tools. |
| Overlay quality and temporal stability | Partially done | Async depth inference, render-rate blending, edge-aware smoothing, crop handling, depth fixtures, and validation tooling are implemented. Real live-captured sequence fixtures still need target-runtime captures. |
| Overlay UX and diagnostics | Done for local software gates | Readiness checks, startup errors, diagnostics, live-runtime check, support bundles, and troubleshooting docs are implemented. |
| Performance hardening | Partially done | Depth modes, cadence logging, frame-timing export, GPU draw timing, and performance benchmarks exist. Deeper present/vsync latency instrumentation remains optional after hardware testing. |
| Experimental backends | Hardware-gated | SBS stereo and 9x5 quilt software paths, validation images, and acceptance reports exist. Final quality needs physical autostereo/light-field hardware acceptance. |
| WoW feasibility gate | Deferred by design | The policy/technical gate remains closed by default and is separate from the overlay-first prototype. |
| Target-display acceptance | Hardware-gated | Connect the target display, collect a fresh live runtime, fill `hardware_observation.yaml`, then run display acceptance and a support bundle from the same setup. |

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
