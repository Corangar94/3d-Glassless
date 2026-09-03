"""Parse whether stable-camera focus and exposure should be locked."""
from __future__ import annotations

from typing import Callable


LogFunction = Callable[[str], None]
DEFAULT_LOCK_CONTROLS_AFTER_WARMUP = True
_CONFIG_KEY = "lock_controls_after_warmup"


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0"}:
            return False
    raise ValueError(f"{_CONFIG_KEY} must be a boolean")


def parse_camera_control_lock_enabled(
    camera_config: object,
    *,
    logger: LogFunction = print,
) -> bool:
    """Return the packaged default, respecting an explicit safe opt-out.

    The transactional lock and recovery boundaries are enabled when the key is
    absent so existing configuration files gain the stabilized-camera path.
    Invalid explicit values fail closed and disable hardware changes.
    """
    camera = camera_config if isinstance(camera_config, dict) else {}
    if _CONFIG_KEY not in camera:
        return DEFAULT_LOCK_CONTROLS_AFTER_WARMUP
    try:
        return _parse_bool(camera[_CONFIG_KEY])
    except (TypeError, ValueError, OverflowError):
        logger(
            "[G3D] Invalid camera lock-controls setting; "
            "leaving automatic controls enabled"
        )
        return False
