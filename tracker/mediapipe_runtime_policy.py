"""Validated runtime policy for MediaPipe asynchronous tracking."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


LogFunction = Callable[[str], None]
_RUNTIME_CONFIG_KEY = "mediapipe_runtime"


@dataclass(frozen=True)
class MediaPipeRuntimePolicy:
    """All bounded asynchronous MediaPipe health and latency settings."""

    stall_timeout_ms: int = 5_000
    max_consecutive_errors: int = 3
    max_backlog_ms: int = 150
    max_result_age_ms: int = 250
    max_consecutive_stale_results: int = 3
    stale_result_window_ms: int = 1_000

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
    """Parse all asynchronous limits atomically or use known-safe defaults.

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
        )
    except (TypeError, ValueError, OverflowError):
        logger(
            "[G3D] Invalid MediaPipe async runtime settings; "
            "using safe defaults"
        )
        return MediaPipeRuntimePolicy()
