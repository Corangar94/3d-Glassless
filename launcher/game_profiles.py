"""Fail-closed capability policy for per-game Glassless3D profiles."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PlayContext(str, Enum):
    """How the selected game profile will be used."""

    ONLINE_MULTIPLAYER = "online_multiplayer"
    OFFLINE_SINGLEPLAYER = "offline_singleplayer"


class RequestedMode(str, Enum):
    """The integration level requested by the user for a profile."""

    NON_INJECTING_DESKTOP = "non_injecting_desktop"
    OFFLINE_ADVANCED = "offline_advanced"
    PUBLISHER_APPROVED_INTEGRATION = "publisher_approved_integration"


class Backend(str, Enum):
    """Runtime and installation backends controlled by profile policy."""

    DESKTOP_OVERLAY = "desktop_overlay"
    WINDOWS_GRAPHICS_CAPTURE = "windows_graphics_capture"
    RESHADE_ADDON = "reshade_addon"


@dataclass(frozen=True)
class GameProfile:
    """User-selected game and safety context."""

    profile_id: str
    display_name: str
    executable_path: str
    play_context: PlayContext = PlayContext.ONLINE_MULTIPLAYER
    requested_mode: RequestedMode = RequestedMode.NON_INJECTING_DESKTOP
    advanced_acknowledged: bool = False
    approval_id: str | None = None


@dataclass(frozen=True)
class PolicyDecision:
    """Resolved capability set for a game profile."""

    active_mode: RequestedMode
    allowed_backends: frozenset[Backend]
    reason: str | None = None

    def allows(self, backend: Backend) -> bool:
        return backend in self.allowed_backends


_DESKTOP_BACKENDS = frozenset({Backend.DESKTOP_OVERLAY, Backend.WINDOWS_GRAPHICS_CAPTURE})


def evaluate_profile(profile: GameProfile) -> PolicyDecision:
    """Resolve the capability set without ever escalating an unsafe profile."""
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
