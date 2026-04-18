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
