# Overlay-First Pivot Design

**Date:** 2026-04-17

**Goal:** Reposition Glassless3D as a Windows desktop glasses-free overlay runtime with the standalone overlay as the primary supported backend, while demoting ReShade and WoW-oriented flows to explicitly experimental integrations.

## 1. Why This Pivot Exists

The current repository and UX still present two conflicting product stories:

- an overlay-first desktop runtime built around DXGI Desktop Duplication, shared-memory head tracking, and monocular depth inference
- a ReShade/game-install workflow that implies WoW and arbitrary protected titles are a normal target

The deep research report recommends a different product boundary:

- Windows-first prototype
- overlay and calibration bench first
- game/process-injected backends later
- WoW treated as a policy-aware feasibility gate, not a default milestone

This spec adopts that recommendation directly.

## 2. Product Boundary

Glassless3D is defined as:

> A Windows desktop glasses-free 3D runtime that uses webcam head tracking and a standalone overlay to create motion-parallax depth on the local display.

Primary supported workflow:

1. User runs the launcher.
2. User completes camera and screen setup.
3. Launcher starts the tracker.
4. Launcher starts `Glassless3DOverlay.exe`.
5. Overlay reads `G3D` and `G3D_Settings`, captures the desktop, infers depth, and renders the glasses-free effect.

Experimental workflow:

- ReShade addon
- game-directory installer
- per-game ReShade profiles
- setup flows targeting injected game integration

WoW is not part of the primary promise. Any WoW mention must be framed as an experimental or deferred feasibility question with policy risk.

## 3. Architecture Direction

The architecture is split into a `core pipeline` and `experimental integrations`.

### 3.1 Core Pipeline

Core pipeline ownership:

- `tracker/`
  - primary source of head pose
  - authoritative producer of `G3D`
  - may continue dual-writing `FT_SharedMem` for compatibility, but that is no longer the product center
- `overlay/`
  - primary rendering backend
  - primary runtime target for quality, stability, and performance work
  - authoritative path for onboarding, troubleshooting, and roadmap milestones
- `launcher/`
  - controller for tracker lifecycle
  - controller for overlay lifecycle
  - owner of overlay tuning and calibration UX
  - owner of first-run onboarding

### 3.2 Experimental Integrations

Experimental integration ownership:

- `addon/`
- `launcher/reshade_install.py`
- `setup.py`
- `profiles/*.json`
- ReShade-specific documentation and tests

These remain in the repository but are demoted to optional backend integrations. They are not removed in this pass because:

- the repository already contains active work in those areas
- the worktree is dirty
- preserving the code is lower risk than deleting it during a product-boundary pivot

## 4. Required Documentation Changes

The documentation must stop mixing the overlay-first and ReShade-first narratives.

### 4.1 New Canonical Message

Top-level docs and architecture docs should consistently communicate:

- the overlay is the primary supported runtime
- ReShade is an experimental backend
- WoW is a later feasibility gate with policy risk
- the current project focus is depth stability, tracking quality, overlay UX, and performance hardening

### 4.2 Architecture Document Changes

`docs/ARCHITECTURE.md` must be updated so that:

- the system overview identifies the overlay as the primary runtime
- `FT_SharedMem` is described as a compatibility or experimental integration channel, not a peer first-class path
- the ReShade section is explicitly labeled experimental
- any WoW-specific language is moved under an experimental/policy-risk heading

### 4.3 Roadmap Document

A new roadmap should be created from the deep research report and current codebase state.

Required milestone ordering:

1. Calibration and tracking reliability
2. Overlay quality and temporal depth stability
3. Overlay diagnostics and desktop UX hardening
4. Performance optimization
5. Optional experimental backends
6. WoW feasibility gate

The roadmap must distinguish:

- what exists now
- what is partially implemented
- what is deferred by design

## 5. Required UX Changes

The launcher and first-run UX must reflect the new primary path.

### 5.1 First-Run Wizard

The first-run wizard must no longer assume game installation or ReShade onboarding.

Current problems:

- it asks for a game folder
- it auto-detects WoW
- it performs a ReShade install sequence
- it tells the user to launch a game and press Home in ReShade

Required replacement flow:

1. Welcome
2. Camera selection and screen sizing
3. Optional overlay readiness check
4. Finish / write config

If the overlay executable or model is missing, the wizard should explain the missing dependency in overlay-first language. It must not redirect the user into ReShade as the default fallback.

### 5.2 Main Window Messaging

The main window must present `Start Tracking` as meaning:

- start tracker
- start overlay
- run the desktop overlay experience

User-facing copy should avoid implying that the normal next step is to open ReShade in a game.

### 5.3 Error Surfaces

Overlay startup failures should be surfaced clearly enough that the overlay path feels like the supported path.

Examples:

- overlay executable missing
- model missing
- overlay exits immediately

These errors should be phrased as primary product issues, not side notes.

## 6. Config and Runtime Direction

The default config and startup behavior should be explicitly overlay-centric.

Required outcomes:

- config examples and generated config include overlay-tuning defaults as first-class values
- launcher startup path assumes overlay participation by default
- docs explain overlay asset requirements before any mention of ReShade installation

No new backend abstraction layer is required in this pass. The pivot is behavioral and structural first:

- make overlay the primary path in docs and UX
- keep ReShade as a retained optional backend
- defer deeper backend interface refactors until after the product boundary is stable

## 7. Testing Strategy

This pivot must be enforced with tests so the repo does not regress back into mixed messaging.

### 7.1 Wizard Tests

Update first-run wizard tests to verify:

- no game directory page
- no WoW auto-detection requirement
- no ReShade install page in default onboarding
- finish-page text references the overlay workflow, not ReShade Home-key instructions

### 7.2 Main Window / Overlay Tests

Verify:

- starting tracking still triggers overlay startup
- overlay process discovery remains correct
- user-visible overlay startup failures remain actionable

### 7.3 ReShade Installer Tests

Keep ReShade installer tests, but treat them as experimental integration coverage. They should not drive the default onboarding path.

### 7.4 Documentation Consistency

Documentation review is part of the implementation acceptance criteria:

- no top-level promise that WoW is the primary target
- no onboarding copy that centers ReShade
- roadmap and architecture agree on overlay-first ownership

## 8. Non-Goals

This pass does not:

- remove the ReShade addon from the repository
- remove `FT_SharedMem` compatibility
- deliver a new display backend
- solve WoW policy feasibility
- complete a full backend abstraction framework

## 9. Risks and Tradeoffs

### 9.1 Risk: Existing ReShade Work Becomes Orphaned

Mitigation:

- keep the code
- keep the tests
- relabel it as experimental
- document when and why it should be used

### 9.2 Risk: UX Pivot Without Enough Overlay Diagnostics

Mitigation:

- include overlay readiness/error handling in the implementation plan
- preserve current overlay process tests
- treat missing overlay assets as first-class onboarding failures

### 9.3 Risk: Docs and Code Drift Again

Mitigation:

- update docs and product surfaces in the same implementation slice
- add tests that encode the new onboarding path

## 10. Success Criteria

This pivot is complete when:

- a new user can onboard without seeing ReShade or WoW in the default flow
- the launcher clearly behaves like an overlay-first controller
- architecture and roadmap docs describe overlay as primary and ReShade as experimental
- the ReShade path still exists, but only as an explicit opt-in integration
- WoW is documented as a later feasibility gate rather than an advertised default target
