# Multiplayer-Safe Game Profiles Design

**Status:** Approved design, pending written-spec review
**Date:** 2026-07-10

## Goal

Let Glassless3D provide the broadest practical experience across games while making stronger integrations explicit, opt-in, title-specific, and fail-closed. Multiplayer use must never depend on process injection, memory access, anti-cheat detection, or attempts to bypass protection.

## Product language and safety boundary

The default mode is named **Non-injecting desktop**, not “online-safe” or “multiplayer compatible.” It is the lower-risk baseline, not a guarantee that any game, publisher, anti-cheat product, capture path, or account policy will permit it.

The application must use this wording wherever it presents a multiplayer-capable profile:

> Online compatibility is title-specific and subject to the game publisher and anti-cheat policy.

The project must not add, document, or suggest any of the following for online or multiplayer profiles:

- DLL or proxy-DLL installation into a game directory;
- ReShade add-on loading or other in-process extension paths;
- game-process memory access, graphics-command interception, depth-buffer access, or input synthesis;
- anti-cheat/protection detection, disabling, evasion, or workarounds;
- automatic classification of a title as safe, approved, or multiplayer-compatible.

If capture is protected, unavailable, or unsupported, Glassless3D disables the effect gracefully. It never substitutes an advanced backend automatically.

## Modes and capability policy

### Non-injecting desktop

This is the default for every new profile and the only mode available for a profile marked as online or multiplayer.

Allowed capabilities:

- OS-level desktop duplication through the standalone overlay;
- an explicit Windows Graphics Capture fallback when the platform supports it;
- tracker, settings, diagnostics, and display output that remain outside the game process.

Forbidden capabilities:

- every advanced/backend capability listed in the safety boundary above.

### Offline advanced

This mode is available only when the profile is explicitly marked offline/single-player and the user has acknowledged that they confirmed permission for the selected title.

It may expose a project-supported advanced backend, such as the existing ReShade add-on path, but only after both gates succeed:

1. `play_context` is `offline_singleplayer`.
2. The profile stores an acknowledgement for the current policy version.

Changing the play context to `online_multiplayer` immediately disables the advanced backend. The launcher does not preserve it as a hidden active setting.

### Publisher-approved integration

This mode is reserved for a future project-maintained approval registry. It is unavailable unless the profile matches a versioned title record containing:

- publisher and title;
- exact executable identity and supported build range;
- allowed backend and play context;
- publisher approval source and approval date;
- revocation state and reason.

An unknown, expired, revoked, or mismatched title record falls back to Non-injecting desktop. A user acknowledgement cannot override a missing or revoked publisher-approved record.

## Profile data model

Profiles are manually created or selected by the user. The application must not auto-detect an executable, anti-cheat product, or online status in order to select a stronger mode.

```yaml
game_profiles:
  example-game:
    display_name: "Example Game"
    executable_path: "C:/Games/Example/ExampleGame.exe"
    play_context: online_multiplayer # online_multiplayer | offline_singleplayer
    requested_mode: non_injecting_desktop
    active_mode: non_injecting_desktop
    capture_preference: desktop_duplication # desktop_duplication | windows_graphics_capture
    advanced_acknowledgement:
      policy_version: null
      confirmed_permitted: false
      accepted_at_utc: null
    publisher_approval_id: null
```

The launcher derives `active_mode`; users never write it directly. It is the most restrictive mode permitted by play context, acknowledgement state, approval state, platform support, and current capture availability.

## Launcher experience

1. The user selects or creates a profile before starting Glassless3D.
2. The launcher shows the selected play context, requested mode, active mode, capture backend, and a plain-language compatibility disclaimer.
3. Selecting Offline advanced opens an acknowledgement dialog. The dialog requires the user to confirm that they have checked the title’s policy and are using an offline/single-player context.
4. Selecting online/multiplayer disables advanced controls and explains why; it does not offer a workaround.
5. If the requested capture backend is unavailable, the launcher displays the reason and either uses the permitted fallback or disables the effect. It never escalates to injection.
6. Diagnostics expose the active profile ID, requested mode, active mode, capture backend, downgrade reason, and whether an advanced acknowledgement exists. They do not collect game-process contents.

## Runtime flow

```text
selected profile
  -> policy evaluator
  -> permitted capability set
  -> capture backend selection
  -> standalone overlay / disabled effect
```

The policy evaluator must run before any overlay, add-on installer, or backend process starts. It returns one of:

- `non_injecting_desktop` with a permitted capture backend;
- `offline_advanced` with an explicit supported backend;
- `publisher_approved_integration` with a valid approval record;
- `disabled` with a user-visible reason.

The existing ReShade installation path must be behind the evaluator. It must not copy a proxy DLL, add-on, or shader for a non-injecting or online/multiplayer profile.

## Capture behavior

Desktop Duplication remains the primary non-injecting capture backend. Windows Graphics Capture is an explicit fallback, not an invisible replacement, because it has platform support checks and user-visible consent/capture behavior.

Protected or display-only content is an expected failure condition. The overlay must present a clear unavailable state rather than continuously retrying, using stale frames, or switching to an advanced backend.

The native remediation work must make capture reset safe across display changes: no null duplication use, no invalid crop boxes, no stale depth-resource dimensions, and no mixed-DPI or rotated-output assumptions.

## Configuration and ownership

There is one canonical user configuration path and one settings authority at a time. Profile changes, tracker calibration, and live settings must use an atomic read-modify-write path protected across processes. A second settings client may observe or request changes, but cannot reset an existing shared mapping to defaults.

The tracking calibration contract is canonical:

- tracker IPD is stored in `tracking.ipd_cm`;
- tracker FOV is stored in `tracking.camera_fov_deg`;
- display/output settings remain under the overlay/profile configuration;
- live UI changes that affect tracker calibration explicitly restart or reinitialize tracking.

All user-controlled numeric settings must be finite and within field-specific bounds before they are published to shared memory or applied to the Kalman filter.

## Verification requirements

The implementation must add and keep automated tests for:

- a new profile defaulting to Non-injecting desktop;
- online/multiplayer profiles rejecting Offline advanced and ReShade installation;
- acknowledgement gating for Offline advanced;
- unknown, revoked, or mismatched publisher approval failing closed;
- unavailable desktop capture disabling the effect rather than escalating;
- profile persistence through the canonical configuration path;
- one settings writer preserving the current mapping when a second client opens;
- finite-value validation for every shared runtime setting;
- tracker IPD/FOV settings reaching the active tracker;
- native source contracts for capture reset, crop bounds, and unsupported output modes.

The test suite must be repaired so the Qt test does not replace the global `QApplication.instance()` with an incompatible fake. Full test execution must be green in the project virtual environment before release.

## Delivery and release requirements

Bootstrap and packaging remediation are part of this work:

- all downloaded models, DLLs, archives, and toolchains use immutable source records, SHA-256 validation, temporary downloads, and atomic finalization;
- archive extraction rejects absolute and traversal paths;
- build staging fails on copy errors and verifies the staged binary hash;
- the packaged distribution includes the overlay, runtime DLLs, both models, and a frozen-compatible tracker worker path;
- the ReShade installer preserves existing INI sections and backs up existing proxy/configuration files before any modification.

## External constraints

- [Microsoft Desktop Duplication](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/desktop-duplication-api) documents OS-level capture and its constraints.
- [Windows Graphics Capture](https://learn.microsoft.com/en-us/windows/apps/develop/media-authoring-processing/screen-capture) documents explicit platform support checks and capture consent behavior.
- [ReShade](https://reshade.me/) identifies its post-processing path as in-process injection and disables depth access in multiplayer.
- [ReShade 5.0](https://reshade.me/releases/7749-5-0) states that the full add-on build is not anti-cheat allow-listed and is intended for single-player use.
