"""Validated runtime policy for MediaPipe tracking."""
from __future__ import annotations

from dataclasses import dataclass
import math
import numbers
from typing import Callable


LogFunction = Callable[[str], None]
_RUNTIME_CONFIG_KEY = "mediapipe_runtime"
DEFAULT_MEDIAPIPE_INPUT_WIDTH_PX = 960
MIN_MEDIAPIPE_INPUT_WIDTH_PX = 320
MAX_MEDIAPIPE_INPUT_WIDTH_PX = 8192


def validated_mediapipe_input_width_px(value: object) -> int:
    """Return a bounded MediaPipe width cap; zero explicitly disables it."""
    if isinstance(value, bool):
        raise ValueError("max_input_width_px must be an integer")
    if isinstance(value, numbers.Real) and not isinstance(
        value,
        numbers.Integral,
    ):
        parsed_real = float(value)
        if not math.isfinite(parsed_real) or not parsed_real.is_integer():
            raise ValueError("max_input_width_px must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("max_input_width_px must be an integer") from error
    if parsed == 0:
        return 0
    if not MIN_MEDIAPIIPE_INPUT_WIDTH_PX <= parsed <= MAX_MEDIAPIPE_INPUT_WIDTH_PX:
        raise ValueError(
            "max_input_width_px must be 0 or between "
            f"{MIN_MEDIAPIPE_INPUT_WIDTH_PX} and "
            f"{MAX_MEDIAPIPE_INPUT_WIDTH_PX}"
        )
    return parsed


@dataclass(frozen=True)
class MediaPipeRuntimePolicy:
    """Bounded MediaPipe preprocessing, health, and latency settings."""

    stall_timeout_ms: int = 5_000
    max_consecutive_errors: int = 3
    max_backlog_ms: int = 150
    max_result_age_ms: int = 250
    max_consecutive_stale_results: int = 3
    stale_result_window_ms: int = 1_000
    max_input_width_px: int = DEFAULT_MEDIAPIPE_INPUT_WIDTH_PX

    def __post_init__(self) -> None:
        if self.stall_timeout_ms < 1:
            raise ValueError("stall_timeout_ms must be at least one")
        if self.max_consecutive_errors < 1:
            raise ValueError("max_consecutive_errors must be at least one")
        if self.max_backlog_ms < 0:
            raise ValueError("max_backlog_ms cannot be negative")
        if self.max_result_age_ms < 0:
            raise ValueError("max_result_age_ms cannot be negative")
        if self.max_consecutive_stale_results < 0:
            raise ValueError(
                "max_consecutive_stale_results cannot be negative"
            )
        if self.stale_result_window_ms < 1:
            raise ValueError("stale_result_window_ms must be at least one")
        object.__setattr__(
            self,
            "max_input_width_px",
            validated_mediapipe_input_width_px(self.max_input_width_px),
        )

    def tracker_kwargs(self) -> dict[str, int]:
        """Return the exact keyword contract accepted by ``FaceTracker``."""
        return {
            "async_stall_timeout_ms": self.stall_timeout_ms,
            "async_max_consecutive_errors": self.max_consecutive_errors,
            "async_max_backlog_ms": self.max_backlog_ms,
            "async_max_result_age_ms": self.max_result_age_ms,
            "async_max_consecutive_stale_results": (
                self.max_consecutive_stale_results
            ),
            "async_stale_result_window_ms": self.stale_result_window_ms,
            "max_input_width_px": self.max_input_width_px,
        }

    def config_values(self) -> dict[str, int]:
        """Return values for the nested ``tracking.mediapipe_runtime`` block."""
        return {
            "stall_timeout_ms": self.stall_timeout_ms,
            "max_consecutive_errors": self.max_consecutive_errors,
            "max_backlog_ms": self.max_backlog_ms,
            "max_result_age_ms": self.max_result_age_ms,
            "max_consecutive_stale_results": (
                self.max_consecutive_stale_results
            ),
            "stale_result_window_ms": self.stale_result_window_ms,
            "max_input_width_px": self.max_input_width_px,
        }


def _nested_values(tracking: dict[str, object]) -> dict[str, object] | None:
    if _RUNTIME_CONFIG_KEY not in tracking:
        return None
    nested = tracking[_RUNTIME_CONFIG_KEY]
    if not isinstance(nested, dict):
        raise ValueError(
            f"tracking.{_RUNTIME_CONFIG_KEY} must be a mapping"
        )
    return nested


def parse_mediapipe_runtime_policy(
    tracking_config: object,
    *,
    logger: LogFunction = print,
) -> MediaPipeRuntimePolicy:
    """Parse all MediaPipe limits atomically or use known-safe defaults.

    New configurations use the nested ``tracking.mediapipe_runtime`` mapping so
    the complete group reaches the backend factory before any individual value
    is consumed. Valid legacy top-level ``async_*`` keys remain supported when
    the nested mapping is absent.
    """
    tracking = tracking_config if isinstance(tracking_config, dict) else {}
    try:
        nested = _nested_values(tracking)
        if nested is not None:
            return MediaPipeRuntimePolicy(
                stall_timeout_ms=int(
                    nested.get("stall_timeout_ms", 5_000)
                ),
                max_consecutive_errors=int(
                    nested.get("max_consecutive_errors", 3)
                ),
                max_backlog_ms=int(
                    nested.get("max_backlog_ms", 150)
                ),
                max_result_age_ms=int(
                    nested.get("max_result_age_ms", 250)
                ),
                max_consecutive_stale_results=int(
                    nested.get("max_consecutive_stale_results", 3)
                ),
                stale_result_window_ms=int(
                    nested.get("stale_result_window_ms", 1_000)
                ),
                max_input_width_px=nested.get(
                    "max_input_width_px",
                    DEFAULT_MEDIAPIPE_INPUT_WIDTH_PX,
                ),
            )

        return MediaPipeRuntimePolicy(
            stall_timeout_ms=int(
                tracking.get("async_stall_timeout_ms", 5_000)
            ),
            max_consecutive_errors=int(
                tracking.get("async_max_consecutive_errors", 3)
            ),
            max_backlog_ms=int(
                tracking.get("async_max_backlog_ms", 150)
            ),
            max_result_age_ms=int(
                tracking.get("async_max_result_age_ms", 250)
            ),
            max_consecutive_stale_results=int(
                tracking.get("async_max_consecutive_stale_results", 3)
            ),
            stale_result_window_ms=int(
                tracking.get("async_stale_result_window_ms", 1_000)
            ),
            max_input_width_px=tracking.get(
                "async_max_input_width_px",
                DEFAULT_MEDIAPIPE_INPUT_WIDTH_PX,
            ),
        )
    except (TypeError, ValueError, OverflowError):
        logger(
            "[G3D] Invalid MediaPipe runtime settings; "
            "using safe defaults"
        )
        return MediaPipeRuntimePolicy()
