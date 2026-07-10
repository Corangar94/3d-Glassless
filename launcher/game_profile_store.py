"""Safe YAML persistence for game-profile policy configuration."""
from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeVar

import yaml

from launcher.game_profiles import GameProfile, PlayContext, RequestedMode


class ProfileStoreError(RuntimeError):
    """Raised when profile persistence cannot preserve the configuration safely."""


_EnumT = TypeVar("_EnumT", PlayContext, RequestedMode)


def _enum_or_default(enum_type: type[_EnumT], raw: object, default: _EnumT) -> _EnumT:
    try:
        return enum_type(str(raw))
    except (TypeError, ValueError):
        return default


def _load_root(
    config_path: Path,
    fallback: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        loaded = fallback or {}
    except yaml.YAMLError as exc:
        raise ProfileStoreError(f"cannot update malformed configuration: {config_path}") from exc
    except OSError as exc:
        raise ProfileStoreError(f"cannot read configuration: {config_path}") from exc

    if loaded is None:
        return {}
    if not isinstance(loaded, Mapping):
        raise ProfileStoreError("configuration root must be a mapping")
    return dict(loaded)


def load_profiles(
    config_path: Path,
    *,
    fallback: Mapping[str, Any] | None = None,
) -> tuple[dict[str, GameProfile], str | None]:
    """Load tolerant profile values while keeping unknown enum values safe."""
    root = _load_root(config_path, fallback)
    raw_profiles = root.get("game_profiles")
    profiles: dict[str, GameProfile] = {}
    if isinstance(raw_profiles, Mapping):
        for profile_id, raw_profile in raw_profiles.items():
            if not isinstance(profile_id, str) or not isinstance(raw_profile, Mapping):
                continue
            approval_id = raw_profile.get("approval_id")
            profiles[profile_id] = GameProfile(
                profile_id=profile_id,
                display_name=str(raw_profile.get("display_name", profile_id)),
                executable_path=str(raw_profile.get("executable_path", "")),
                play_context=_enum_or_default(
                    PlayContext,
                    raw_profile.get("play_context"),
                    PlayContext.ONLINE_MULTIPLAYER,
                ),
                requested_mode=_enum_or_default(
                    RequestedMode,
                    raw_profile.get("requested_mode"),
                    RequestedMode.NON_INJECTING_DESKTOP,
                ),
                advanced_acknowledged=raw_profile.get("advanced_acknowledged") is True,
                approval_id=approval_id if isinstance(approval_id, str) else None,
            )

    active = root.get("active_game_profile")
    active_profile_id = (
        active if isinstance(active, str) and active in profiles else next(iter(profiles), None)
    )
    return profiles, active_profile_id


def save_profiles(
    config_path: Path,
    profiles: Mapping[str, GameProfile],
    active_profile_id: str | None,
    *,
    fallback: Mapping[str, Any] | None = None,
) -> None:
    """Atomically store profiles without modifying unrelated configuration keys."""
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
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            dir=config_path.parent,
        ) as temp_file:
            yaml.safe_dump(root, temp_file, sort_keys=False)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)
        os.replace(temp_path, config_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
