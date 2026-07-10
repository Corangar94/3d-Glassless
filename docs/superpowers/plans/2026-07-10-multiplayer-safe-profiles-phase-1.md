# Multiplayer-Safe Profiles — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit per-game safety profiles that default to non-injecting desktop capture, fail closed for online/multiplayer play, and gate the ReShade installer behind an offline acknowledgement.

**Architecture:** A pure `launcher.game_profiles` policy module owns the profile schema and capability rules. The publisher-approved mode is represented but deliberately fails closed as unavailable in this phase; a future versioned approval registry can be added without widening the other modes. `launcher.game_profile_store` serializes those profiles under the canonical YAML configuration using atomic replacement. The launcher presents and persists the active profile before starting runtime components; ReShade entry points require a policy decision rather than inferring permissiveness from a game name.

**Tech Stack:** Python 3.11, PySide6, PyYAML, pytest 8, pytest-qt, Windows desktop overlay process.

---

## Scope and sequencing

This plan implements the profile/policy boundary only. It intentionally does not add anti-cheat detection, automatic game classification, injection workarounds, game-memory access, or input synthesis.

The next independent plans are:

1. Native desktop-capture resilience: device-loss recovery, monitor/output selection, DPI/rotation, and depth-resource reset.
2. Shared-state and calibration hardening: one settings authority, atomic configuration mutation, finite-value validation, and tracker IPD/FOV consistency.
3. Delivery hardening: verified bootstrap artifacts, safe archive extraction, reliable staging, complete frozen packaging, and CI.

## File structure

- Create: `launcher/game_profiles.py` — immutable profile models, policy evaluator, backend capability checks, and a reserved publisher-approved mode.
- Create: `launcher/game_profile_store.py` — YAML profile load/save with atomic replace, fallback configuration support, and preservation of unrelated configuration.
- Create: `tests/test_game_profiles.py` — pure policy and approval tests.
- Create: `tests/test_game_profile_store.py` — persistence and atomic-write tests.
- Modify: `launcher/mainwindow.py:17-44, 215-267, 536-580, 1129-1153` — profile controls, policy display, profile persistence, and startup enforcement.
- Modify: `launcher/wizard.py:123-140` — initialize a default non-injecting profile.
- Modify: `launcher/reshade_install.py:31-99` — require an explicit offline-advanced policy decision before installing files.
- Modify: `setup.py:126-159` — require a profile-config/active-profile policy decision before the CLI installs ReShade assets.
- Modify: `launcher/diagnostics.py:77-98, 120-226` — report active profile, requested/active mode, and downgrade reason.
- Modify: `tests/test_mainwindow.py`, `tests/test_reshade_install.py`, `tests/test_wizard.py`, `tests/test_diagnostics.py` — behavior coverage and Qt test isolation.

### Task 1: Define the pure game-profile policy contract

**Files:**

- Create: `launcher/game_profiles.py`
- Test: `tests/test_game_profiles.py`

- [ ] **Step 1: Write failing policy tests**

```python
from launcher.game_profiles import (
    Backend,
    GameProfile,
    PlayContext,
    RequestedMode,
    evaluate_profile,
)


def test_online_profile_fails_closed_to_non_injecting_desktop():
    profile = GameProfile(
        profile_id="arena",
        display_name="Arena",
        executable_path="C:/Games/Arena/Arena.exe",
        play_context=PlayContext.ONLINE_MULTIPLAYER,
        requested_mode=RequestedMode.OFFLINE_ADVANCED,
        advanced_acknowledged=True,
    )

    decision = evaluate_profile(profile)

    assert decision.active_mode is RequestedMode.NON_INJECTING_DESKTOP
    assert decision.allows(Backend.DESKTOP_OVERLAY)
    assert not decision.allows(Backend.RESHADE_ADDON)
    assert decision.reason == "online profiles permit non-injecting desktop only"


def test_offline_advanced_requires_acknowledgement():
    profile = GameProfile(
        profile_id="story",
        display_name="Story",
        executable_path="C:/Games/Story/Story.exe",
        play_context=PlayContext.OFFLINE_SINGLEPLAYER,
        requested_mode=RequestedMode.OFFLINE_ADVANCED,
        advanced_acknowledged=False,
    )

    decision = evaluate_profile(profile)

    assert decision.active_mode is RequestedMode.NON_INJECTING_DESKTOP
    assert not decision.allows(Backend.RESHADE_ADDON)
    assert decision.reason == "offline advanced requires acknowledgement"


def test_acknowledged_offline_profile_allows_reshade_only():
    profile = GameProfile(
        profile_id="story",
        display_name="Story",
        executable_path="C:/Games/Story/Story.exe",
        play_context=PlayContext.OFFLINE_SINGLEPLAYER,
        requested_mode=RequestedMode.OFFLINE_ADVANCED,
        advanced_acknowledged=True,
    )

    decision = evaluate_profile(profile)

    assert decision.active_mode is RequestedMode.OFFLINE_ADVANCED
    assert decision.allows(Backend.DESKTOP_OVERLAY)
    assert decision.allows(Backend.RESHADE_ADDON)


def test_publisher_approved_mode_remains_reserved_and_fails_closed():
    profile = GameProfile(
        profile_id="approved",
        display_name="Approved",
        executable_path="C:/Games/Approved/Approved.exe",
        play_context=PlayContext.OFFLINE_SINGLEPLAYER,
        requested_mode=RequestedMode.PUBLISHER_APPROVED_INTEGRATION,
        approval_id="publisher/example/1.2.3",
    )

    decision = evaluate_profile(profile)

    assert decision.active_mode is RequestedMode.NON_INJECTING_DESKTOP
    assert not decision.allows(Backend.RESHADE_ADDON)
    assert decision.reason == "publisher-approved integration is not implemented"
```

- [ ] **Step 2: Run the policy tests to verify they fail because the module is absent**

Run:

```powershell
& .venv\Scripts\python.exe -B -m pytest tests/test_game_profiles.py -q -p no:cacheprovider
```

Expected: collection fails with `ModuleNotFoundError: No module named 'launcher.game_profiles'`.

- [ ] **Step 3: Implement the policy module**

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PlayContext(str, Enum):
    ONLINE_MULTIPLAYER = "online_multiplayer"
    OFFLINE_SINGLEPLAYER = "offline_singleplayer"


class RequestedMode(str, Enum):
    NON_INJECTING_DESKTOP = "non_injecting_desktop"
    OFFLINE_ADVANCED = "offline_advanced"
    PUBLISHER_APPROVED_INTEGRATION = "publisher_approved_integration"


class Backend(str, Enum):
    DESKTOP_OVERLAY = "desktop_overlay"
    WINDOWS_GRAPHICS_CAPTURE = "windows_graphics_capture"
    RESHADE_ADDON = "reshade_addon"


@dataclass(frozen=True)
class GameProfile:
    profile_id: str
    display_name: str
    executable_path: str
    play_context: PlayContext = PlayContext.ONLINE_MULTIPLAYER
    requested_mode: RequestedMode = RequestedMode.NON_INJECTING_DESKTOP
    advanced_acknowledged: bool = False
    approval_id: str | None = None


@dataclass(frozen=True)
class PolicyDecision:
    active_mode: RequestedMode
    allowed_backends: frozenset[Backend]
    reason: str | None = None

    def allows(self, backend: Backend) -> bool:
        return backend in self.allowed_backends


_DESKTOP_BACKENDS = frozenset({Backend.DESKTOP_OVERLAY, Backend.WINDOWS_GRAPHICS_CAPTURE})


def evaluate_profile(profile: GameProfile) -> PolicyDecision:
    if profile.play_context is PlayContext.ONLINE_MULTIPLAYER:
        return PolicyDecision(
            RequestedMode.NON_INJECTING_DESKTOP,
            _DESKTOP_BACKENDS,
            "online profiles permit non-injecting desktop only",
        )
    if profile.requested_mode is RequestedMode.OFFLINE_ADVANCED:
        if not profile.advanced_acknowledged:
            return PolicyDecision(
                RequestedMode.NON_INJECTING_DESKTOP,
                _DESKTOP_BACKENDS,
                "offline advanced requires acknowledgement",
            )
        return PolicyDecision(
            RequestedMode.OFFLINE_ADVANCED,
            _DESKTOP_BACKENDS | frozenset({Backend.RESHADE_ADDON}),
        )
    if profile.requested_mode is RequestedMode.PUBLISHER_APPROVED_INTEGRATION:
        return PolicyDecision(
            RequestedMode.NON_INJECTING_DESKTOP,
            _DESKTOP_BACKENDS,
            "publisher-approved integration is not implemented",
        )
    return PolicyDecision(RequestedMode.NON_INJECTING_DESKTOP, _DESKTOP_BACKENDS)
```

- [ ] **Step 4: Run the policy tests to verify the expected decisions pass**

Run:

```powershell
& .venv\Scripts\python.exe -B -m pytest tests/test_game_profiles.py -q -p no:cacheprovider
```

Expected: all policy tests pass.

- [ ] **Step 5: Commit the pure policy contract**

```powershell
git add launcher/game_profiles.py tests/test_game_profiles.py
git commit -m "feat: add fail-closed game profile policy"
```

### Task 2: Persist profiles atomically without overwriting unrelated configuration

**Files:**

- Create: `launcher/game_profile_store.py`
- Test: `tests/test_game_profile_store.py`

- [ ] **Step 1: Write failing persistence tests**

```python
import pytest
import yaml

from launcher.game_profile_store import ProfileStoreError, load_profiles, save_profiles
from launcher.game_profiles import GameProfile, PlayContext, RequestedMode


def test_save_profiles_preserves_camera_and_overlay_sections(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("camera:\n  index: 2\noverlay:\n  strength_x: 1.5\n", encoding="utf-8")
    profiles = {
        "arena": GameProfile(
            profile_id="arena",
            display_name="Arena",
            executable_path="C:/Games/Arena/Arena.exe",
        )
    }

    save_profiles(config_path, profiles, active_profile_id="arena")

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["camera"]["index"] == 2
    assert saved["overlay"]["strength_x"] == 1.5
    assert saved["active_game_profile"] == "arena"
    assert saved["game_profiles"]["arena"]["requested_mode"] == "non_injecting_desktop"


def test_load_profiles_treats_invalid_profile_values_as_non_injecting(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "game_profiles:\n  unsafe:\n    display_name: Unsafe\n    executable_path: C:/Unsafe.exe\n"
        "    play_context: anything\n    requested_mode: reshade\n",
        encoding="utf-8",
    )

    profiles, active_profile_id = load_profiles(config_path)

    assert active_profile_id == "unsafe"
    assert profiles["unsafe"].play_context is PlayContext.ONLINE_MULTIPLAYER
    assert profiles["unsafe"].requested_mode is RequestedMode.NON_INJECTING_DESKTOP


def test_load_profiles_uses_in_memory_fallback_when_config_does_not_exist(tmp_path):
    fallback = {
        "game_profiles": {
            "default": {
                "display_name": "Default profile",
                "executable_path": "",
                "play_context": "online_multiplayer",
                "requested_mode": "non_injecting_desktop",
                "advanced_acknowledged": False,
            }
        },
        "active_game_profile": "default",
    }

    profiles, active_profile_id = load_profiles(tmp_path / "missing.yaml", fallback=fallback)

    assert active_profile_id == "default"
    assert profiles["default"].requested_mode is RequestedMode.NON_INJECTING_DESKTOP


def test_save_profiles_refuses_to_replace_malformed_existing_yaml(tmp_path):
    config_path = tmp_path / "config.yaml"
    original = "game_profiles: [unterminated\n"
    config_path.write_text(original, encoding="utf-8")

    with pytest.raises(ProfileStoreError, match="cannot update malformed"):
        save_profiles(config_path, {}, active_profile_id=None)

    assert config_path.read_text(encoding="utf-8") == original
```

- [ ] **Step 2: Run the persistence tests to verify they fail because the store is absent**

Run:

```powershell
& .venv\Scripts\python.exe -B -m pytest tests/test_game_profile_store.py -q -p no:cacheprovider
```

Expected: collection fails with `ModuleNotFoundError: No module named 'launcher.game_profile_store'`.

- [ ] **Step 3: Implement YAML conversion and atomic replacement**

```python
from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from launcher.game_profiles import GameProfile, PlayContext, RequestedMode


class ProfileStoreError(RuntimeError):
    """Raised when profile persistence cannot safely preserve the existing configuration."""


def _enum_or_default(enum_type, raw, default):
    try:
        return enum_type(str(raw))
    except (TypeError, ValueError):
        return default


def _load_root(config_path: Path, fallback: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        loaded = fallback or {}
    except yaml.YAMLError as exc:
        raise ProfileStoreError(f"cannot update malformed configuration: {config_path}") from exc
    except OSError as exc:
        raise ProfileStoreError(f"cannot read configuration: {config_path}") from exc
    return dict(loaded) if isinstance(loaded, dict) else {}


def load_profiles(
    config_path: Path,
    *,
    fallback: Mapping[str, Any] | None = None,
) -> tuple[dict[str, GameProfile], str | None]:
    root = _load_root(config_path, fallback)
    raw_profiles = root.get("game_profiles")
    profiles: dict[str, GameProfile] = {}
    if isinstance(raw_profiles, dict):
        for profile_id, raw in raw_profiles.items():
            if not isinstance(profile_id, str) or not isinstance(raw, dict):
                continue
            profiles[profile_id] = GameProfile(
                profile_id=profile_id,
                display_name=str(raw.get("display_name", profile_id)),
                executable_path=str(raw.get("executable_path", "")),
                play_context=_enum_or_default(
                    PlayContext, raw.get("play_context"), PlayContext.ONLINE_MULTIPLAYER
                ),
                requested_mode=_enum_or_default(
                    RequestedMode, raw.get("requested_mode"), RequestedMode.NON_INJECTING_DESKTOP
                ),
                advanced_acknowledged=raw.get("advanced_acknowledged") is True,
                approval_id=raw.get("approval_id") if isinstance(raw.get("approval_id"), str) else None,
            )
    active = root.get("active_game_profile")
    active_profile_id = active if isinstance(active, str) and active in profiles else next(iter(profiles), None)
    return profiles, active_profile_id


def save_profiles(
    config_path: Path,
    profiles: Mapping[str, GameProfile],
    active_profile_id: str | None,
    *,
    fallback: Mapping[str, Any] | None = None,
) -> None:
    root = _load_root(config_path, fallback)
    root["game_profiles"] = {
        profile_id: {
            "display_name": profile.display_name,
            "executable_path": profile.executable_path,
            "play_context": profile.play_context.value,
            "requested_mode": profile.requested_mode.value,
            "advanced_acknowledged": profile.advanced_acknowledged,
            "approval_id": profile.approval_id,
        }
        for profile_id, profile in profiles.items()
    }
    root["active_game_profile"] = active_profile_id
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=config_path.parent) as temp:
            yaml.safe_dump(root, temp, sort_keys=False)
            temp.flush()
            os.fsync(temp.fileno())
            temp_path = Path(temp.name)
        os.replace(temp_path, config_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
```

- [ ] **Step 4: Run the persistence tests to verify profile round-trips and unrelated config survives**

Run:

```powershell
& .venv\Scripts\python.exe -B -m pytest tests/test_game_profile_store.py -q -p no:cacheprovider
```

Expected: all persistence tests pass.

- [ ] **Step 5: Commit atomic profile persistence**

```powershell
git add launcher/game_profile_store.py tests/test_game_profile_store.py
git commit -m "feat: persist game profiles atomically"
```

### Task 3: Gate all ReShade installation entry points with policy decisions

**Files:**

- Modify: `launcher/reshade_install.py:31-99`
- Modify: `setup.py:126-159`
- Modify: `tests/test_reshade_install.py`
- Create: `tests/test_setup_policy.py`

- [ ] **Step 1: Write failing installer-gate tests**

```python
import pytest

from launcher.game_profiles import GameProfile, PlayContext, RequestedMode, evaluate_profile
from launcher.reshade_install import InstallError, install, install_steps


def test_reshade_install_rejects_online_profile(tmp_path):
    decision = evaluate_profile(
        GameProfile(
            profile_id="online",
            display_name="Online",
            executable_path="C:/Games/Online.exe",
            play_context=PlayContext.ONLINE_MULTIPLAYER,
            requested_mode=RequestedMode.NON_INJECTING_DESKTOP,
        )
    )

    with pytest.raises(InstallError, match="not permitted"):
        list(install_steps(str(tmp_path), policy=decision))

    with pytest.raises(InstallError, match="not permitted"):
        install(str(tmp_path), policy=decision)
```

```python
def test_setup_cli_requires_an_acknowledged_offline_profile(monkeypatch, tmp_path):
    import setup

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "active_game_profile: online\ngame_profiles:\n  online:\n"
        "    display_name: Online\n    executable_path: C:/Games/Online.exe\n"
        "    play_context: online_multiplayer\n    requested_mode: non_injecting_desktop\n"
        "    advanced_acknowledged: false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(setup, "ADDON_PATH", str(tmp_path / "Glassless3D.addon"))
    (tmp_path / "Glassless3D.addon").write_bytes(b"addon")

    with pytest.raises(SystemExit, match="not permitted"):
        setup.main([
            "--game-dir", str(tmp_path / "game"),
            "--profile-config", str(config_path),
        ])
```

- [ ] **Step 2: Run the installer-gate tests to verify the currently permissive paths fail them**

Run:

```powershell
& .venv\Scripts\python.exe -B -m pytest tests/test_reshade_install.py tests/test_setup_policy.py -q -p no:cacheprovider
```

Expected: the new tests fail because `install_steps` lacks a `policy` parameter and `setup.py` lacks `--profile-config`.

- [ ] **Step 3: Require `Backend.RESHADE_ADDON` before touching game files**

```python
# launcher/reshade_install.py
from launcher.game_profiles import Backend, PolicyDecision


def install_steps(
    game_dir: str,
    profile_name: str = "wow",
    *,
    policy: PolicyDecision,
) -> Generator[str, None, None]:
    if not policy.allows(Backend.RESHADE_ADDON):
        raise InstallError(
            "Policy check",
            f"ReShade installation is not permitted: {policy.reason or 'offline advanced is required'}",
        )
    # Existing copy/configuration steps follow unchanged.
```

Give the public convenience wrapper the same mandatory parameter; no wrapper may bypass the capability check:

```python
def install(
    game_dir: str,
    profile_name: str = "wow",
    *,
    policy: PolicyDecision,
) -> None:
    for _ in install_steps(game_dir, profile_name, policy=policy):
        pass
```

```python
# setup.py argument and policy resolution
# Change the entry point to `def main(argv: Sequence[str] | None = None) -> None`
# and call `parser.parse_args(argv)`, so the CLI policy contract is directly testable.
parser.add_argument("--profile-config", type=Path, required=True)
try:
    profiles, active_profile_id = load_profiles(args.profile_config)
except ProfileStoreError as exc:
    sys.exit(f"ERROR: {exc}")
if active_profile_id is None:
    sys.exit("ERROR: No active Glassless3D game profile is configured.")
decision = evaluate_profile(profiles[active_profile_id])
if not decision.allows(Backend.RESHADE_ADDON):
    sys.exit(f"ERROR: ReShade installation is not permitted: {decision.reason}")
```

Resolve this policy immediately after parsing arguments and before checking `ADDON_PATH`, loading the legacy ReShade profile, locating the game directory, or invoking any write path. Give `setup.py`'s local `install(game_dir, profile, dry_run, *, policy)` function the same first-line capability check before it writes/copies anything, including for `--dry-run`; this prevents its legacy direct-copy path from bypassing `launcher.reshade_install`. Import `Path`, `Sequence`, `Backend`, `ProfileStoreError`, `evaluate_profile`, and `load_profiles` in `setup.py`. Update every existing `install_steps` test to pass an acknowledged offline decision created through `evaluate_profile`, so it continues to exercise actual installer behavior rather than bypassing the new gate. Keep `--game` as a display/install profile selector only; it must not grant permission or select a more permissive policy.

- [ ] **Step 4: Run policy and installer tests to verify online profiles cannot mutate game directories**

Run:

```powershell
& .venv\Scripts\python.exe -B -m pytest tests/test_game_profiles.py tests/test_reshade_install.py tests/test_setup_policy.py -q -p no:cacheprovider
```

Expected: all tests pass; online policy tests prove no ReShade asset is copied.

- [ ] **Step 5: Commit installer gating**

```powershell
git add launcher/reshade_install.py setup.py tests/test_reshade_install.py tests/test_setup_policy.py
git commit -m "feat: gate ReShade installation by game profile policy"
```

### Task 4: Add launcher profile controls and enforce the active decision at startup

**Files:**

- Modify: `launcher/mainwindow.py:17-44, 215-267, 536-580, 1129-1153`
- Modify: `tests/test_mainwindow.py`

- [ ] **Step 1: Write failing launcher tests**

```python
def test_mainwindow_defaults_new_profile_to_non_injecting_desktop(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)

    assert win._active_profile.play_context.value == "online_multiplayer"
    assert win._policy_decision.active_mode.value == "non_injecting_desktop"
    assert "Non-injecting desktop" in win._profile_mode_label.text()


def test_mainwindow_online_profile_starts_non_injecting_runtime(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    config = {
        **CONFIG,
        "game_profiles": {
            "online": {
                "display_name": "Online",
                "executable_path": "C:/Games/Online.exe",
                "play_context": "online_multiplayer",
                "requested_mode": "offline_advanced",
                "advanced_acknowledged": True,
            }
        },
        "active_game_profile": "online",
    }
    with patch("launcher.mainwindow.TrackerProcess") as tracker_cls:
        win = MainWindow(config=config, config_path=cfg_path)
        win._start_tracking()

    tracker_cls.assert_called_once_with(config_path=cfg_path)
    assert win._policy_decision.active_mode.value == "non_injecting_desktop"
    assert "Non-injecting desktop" in win._profile_mode_label.text()


def test_mainwindow_surfaces_malformed_profile_config_without_replacing_it(qapp, tmp_path):
    config_path = tmp_path / "config.yaml"
    original = "game_profiles: [unterminated\n"
    config_path.write_text(original, encoding="utf-8")

    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=str(config_path))

    assert config_path.read_text(encoding="utf-8") == original
    assert "Profile configuration error" in win._profile_mode_label.text()
```

- [ ] **Step 2: Run the launcher tests to verify profile fields and controls do not exist yet**

Run:

```powershell
& .venv\Scripts\python.exe -B -m pytest tests/test_mainwindow.py -q -p no:cacheprovider
```

Expected: the new tests fail with missing `_active_profile`, `_policy_decision`, or `_profile_mode_label` attributes.

- [ ] **Step 3: Implement the profile controller in `MainWindow`**

```python
# Imports near existing launcher imports
from launcher.game_profile_store import ProfileStoreError, load_profiles, save_profiles
from launcher.game_profiles import GameProfile, RequestedMode, evaluate_profile

# In __init__, before settings-writer initialization. `fallback=self._config` is
# required because tests and first run may supply a config mapping before it has
# been written to disk.
try:
    self._profiles, active_profile_id = load_profiles(Path(config_path), fallback=self._config)
    self._profile_store_error: str | None = None
except ProfileStoreError as exc:
    self._profiles, active_profile_id = {}, None
    self._profile_store_error = str(exc)
if active_profile_id is None:
    default = GameProfile(
        profile_id="default",
        display_name="Default profile",
        executable_path="",
    )
    self._profiles = {default.profile_id: default}
    active_profile_id = default.profile_id
self._active_profile_id = active_profile_id
self._active_profile = self._profiles[active_profile_id]
self._policy_decision = evaluate_profile(self._active_profile)
if self._profile_store_error is None:
    save_profiles(
        Path(config_path),
        self._profiles,
        self._active_profile_id,
        fallback=self._config,
    )
```

Create a Runtime-tab `QGroupBox("Game profile")` containing a profile `QComboBox`, a play-context `QComboBox`, a requested-mode `QComboBox`, an acknowledgement `QCheckBox`, and a read-only `QLabel` named `_profile_mode_label`. When a control changes, rebuild `GameProfile`, call `evaluate_profile`, update the label with the active mode/reason, and call `save_profiles`. When `_on_save_config` writes overlay/tracking settings, pass its updated mapping as `fallback=` to `save_profiles` instead of opening the YAML file directly; this keeps the in-memory profile state in every normal settings save and uses the same atomic replacement routine. If `_profile_store_error` is set, initialize the label to `Profile configuration error: …`, disable profile-changing controls, and never save over the malformed file.

Before tracker startup, evaluate the active profile again. For a non-injecting decision, continue with the standalone tracker and overlay only: desktop capture is permitted and this is not a refusal to start. For an offline-advanced decision, do not install or launch ReShade automatically; expose the permitted state for the separately gated installer. For a decision with a downgrade reason, show that reason in the profile label and continue only with the permitted non-injecting runtime. Catch `ProfileStoreError` at UI persistence boundaries, retain the prior in-memory profile, and show a visible configuration error rather than overwriting malformed YAML.

- [ ] **Step 4: Run launcher tests to verify the visible mode and fail-closed startup decision**

Run:

```powershell
& .venv\Scripts\python.exe -B -m pytest tests/test_game_profiles.py tests/test_game_profile_store.py tests/test_mainwindow.py -q -p no:cacheprovider
```

Expected: all tests pass and online profiles stay in Non-injecting desktop mode.

- [ ] **Step 5: Commit launcher profile controls**

```powershell
git add launcher/mainwindow.py tests/test_mainwindow.py
git commit -m "feat: add explicit game profile controls"
```

### Task 5: Initialize and report profile state across supported launcher flows

**Files:**

- Modify: `launcher/wizard.py:123-140`
- Modify: `launcher/diagnostics.py:77-98, 120-226, 239-319`
- Modify: `tests/test_wizard.py`
- Modify: `tests/test_diagnostics.py`

- [ ] **Step 1: Write failing wizard and diagnostics tests**

```python
def test_done_page_writes_default_non_injecting_game_profile(tmp_path, qapp):
    config_path = tmp_path / "config.yaml"
    page = DonePage(str(config_path))

    page.initializePage()

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["active_game_profile"] == "default"
    assert config["game_profiles"]["default"]["requested_mode"] == "non_injecting_desktop"


def test_diagnostics_reports_profile_downgrade(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text(
        "active_game_profile: arena\ngame_profiles:\n  arena:\n"
        "    display_name: Arena\n    executable_path: C:/Arena.exe\n"
        "    play_context: online_multiplayer\n    requested_mode: offline_advanced\n"
        "    advanced_acknowledged: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(diagnostics, "_probe_camera", lambda _: diagnostics.CameraProbe(0, True, True))

    report = diagnostics.collect_diagnostics(config)

    assert report.active_profile_id == "arena"
    assert report.requested_profile_mode == "offline_advanced"
    assert report.active_profile_mode == "non_injecting_desktop"
    assert "online profiles permit non-injecting desktop only" in report.profile_reason


def test_diagnostics_reports_malformed_profile_config_without_crashing(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("game_profiles: [unterminated\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "_probe_camera", lambda _: diagnostics.CameraProbe(0, True, True))

    report = diagnostics.collect_diagnostics(config)

    assert report.active_profile_id is None
    assert any("profile configuration" in problem for problem in report.problems)
```

- [ ] **Step 2: Run those tests to verify the current config and report models lack profile state**

Run:

```powershell
& .venv\Scripts\python.exe -B -m pytest tests/test_wizard.py tests/test_diagnostics.py -q -p no:cacheprovider
```

Expected: the new assertions fail because the default profile and diagnostics fields are absent.

- [ ] **Step 3: Add default config and diagnostics fields**

```python
# launcher/wizard.py
_DEFAULT_GAME_PROFILES = {
    "default": {
        "display_name": "Default profile",
        "executable_path": "",
        "play_context": "online_multiplayer",
        "requested_mode": "non_injecting_desktop",
        "advanced_acknowledged": False,
        "approval_id": None,
    }
}

# Persist these alongside existing defaults
config["game_profiles"] = _DEFAULT_GAME_PROFILES
config["active_game_profile"] = "default"
```

```python
# launcher/diagnostics.py
@dataclass(frozen=True)
class DiagnosticsReport:
    # Existing fields remain unchanged.
    active_profile_id: str | None = None
    requested_profile_mode: str | None = None
    active_profile_mode: str | None = None
    profile_reason: str | None = None
```

Load profiles with `load_profiles`, evaluate the active profile, add the values to JSON and human-readable report output, and retain `None` fields for pre-profile configurations. Catch `ProfileStoreError`, append a clear `profile configuration …` problem, and return a report rather than crashing diagnostics.

- [ ] **Step 4: Run wizard and diagnostics tests to verify profile state is observable**

Run:

```powershell
& .venv\Scripts\python.exe -B -m pytest tests/test_wizard.py tests/test_diagnostics.py -q -p no:cacheprovider
```

Expected: all tests pass, including a report that explains the online-profile downgrade.

- [ ] **Step 5: Commit profile initialization and observability**

```powershell
git add launcher/wizard.py launcher/diagnostics.py tests/test_wizard.py tests/test_diagnostics.py
git commit -m "feat: report active game profile policy"
```

### Task 6: Repair the Qt test isolation and verify the complete Phase 1 suite

**Files:**

- Modify: `launcher/mainwindow.py:647-656`
- Modify: `tests/test_mainwindow.py:188-206`
- Test: `tests/test_mainwindow.py`

- [ ] **Step 1: Replace the global QApplication patch with a failing focused shutdown test**

```python
def test_runtime_health_keyboard_interrupt_requests_shutdown(window, monkeypatch):
    calls = []

    class FakeApp:
        def closeAllWindows(self):
            calls.append("close")

        def quit(self):
            calls.append("quit")

    monkeypatch.setattr(window, "_read_overlay_summary", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr(window, "_shutdown_application", lambda: calls.extend(["close", "quit"]))

    window._safe_refresh_runtime_health()

    assert calls == ["close", "quit"]
```

- [ ] **Step 2: Run the focused test to verify the new shutdown seam is absent**

Run:

```powershell
& .venv\Scripts\python.exe -B -m pytest tests/test_mainwindow.py::test_runtime_health_keyboard_interrupt_requests_shutdown -q -p no:cacheprovider
```

Expected: failure because `MainWindow` has no `_shutdown_application` method.

- [ ] **Step 3: Add the shutdown seam and retain production behavior**

```python
def _shutdown_application(self) -> None:
    app = QApplication.instance()
    if app is None:
        return
    close_all = getattr(app, "closeAllWindows", None)
    if callable(close_all):
        close_all()
    app.quit()

def _safe_refresh_runtime_health(self) -> None:
    try:
        self._refresh_runtime_health()
    except KeyboardInterrupt:
        self._shutdown_application()
```

This keeps the real Qt singleton intact during pytest-qt teardown.

- [ ] **Step 4: Run all Phase 1 and full project tests in the project virtual environment**

Run:

```powershell
& .venv\Scripts\python.exe -B -m pytest tests -q -p no:cacheprovider
```

Expected: all collected tests pass with no pytest-qt teardown cascade.

- [ ] **Step 5: Commit test isolation and verification result**

```powershell
git add launcher/mainwindow.py tests/test_mainwindow.py
git commit -m "test: isolate Qt shutdown behavior"
```

## Plan self-review

- Spec coverage: Tasks 1–6 cover profile defaults, manual selection, online fail-closed policy, acknowledgement-gated offline advanced behavior, a deliberately unavailable publisher-approved mode, graceful downgrade visibility, installer gating, canonical persistence, and test isolation.
- Policy consistency: online context is checked before requested mode, so acknowledgement and a requested advanced mode cannot enable injection for multiplayer. Publisher-approved mode also remains non-injecting until a future versioned approval registry and integration are implemented.
- Persistence safety: first-run/in-memory configuration has an explicit fallback path; malformed existing YAML raises `ProfileStoreError`, remains untouched, and is visible through both the launcher and diagnostics.
- Entry-point coverage: both `launcher.reshade_install.install_steps`/`install` and legacy `setup.py` require the same `Backend.RESHADE_ADDON` capability before any game-directory action.
- Test consistency: online profiles still start the permitted standalone tracker/desktop overlay; the test rejects escalation rather than incorrectly treating non-injecting runtime as a start failure. The `setup.main(argv)` refactor makes the CLI decision testable.

## Execution record — 2026-07-10

- Completed inline on the user-authorized `master` branch.
- Added explicit profile creation, executable-path persistence, online disclosure, and online-only advanced-control disablement.
- Tightened the evaluator for runtime-invalid values, bound ReShade CLI installation to the active profile executable, and protected malformed/non-mapping configuration from all Phase 1 launcher write paths.
- Verified with `pytest tests -q -p no:cacheprovider`: **525 passed**. `pyright launcher setup.py` reported **0 errors**.
