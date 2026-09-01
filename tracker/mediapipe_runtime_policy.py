"""Validated runtime policy for MediaPipe asynchronous tracking."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


LogFunction = Callable[[str], None]


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


def parse_mediapipe_runtime_policy(
    tracking_config: object,
    *,
    logger: LogFunction = print,
) -> MediaPipeRuntimePolicy:
    """Parse all async settings atomically or return known-safe defaults."""
    values = tracking_config if isinstance(tracking_config, dict) else {}
    try:
        return MediaPipeRuntimePolicy(
            stall_timeout_ms=int(
                values.get("async_stall_timeout_ms", 5_000)
            ),
            max_consecutive_errors=int(
                values.get("async_max_consecutive_errors", 3)
            ),
            max_backlog_ms=int(
                values.get("async_max_backlog_ms", 150)
            ),
            max_result_age_ms=int(
                values.get("async_max_result_age_ms", 250)
            ),
            max_consecutive_stale_results=int(
                values.get("async_max_consecutive_stale_results", 3)
            ),
            stale_result_window_ms=int(
                values.get("async_stale_result_window_ms", 1_000)
            ),
        )
    except (TypeError, ValueError, OverflowError):
        logger(
            "[G3D] Invalid MediaPipe async runtime settings; "
            "using safe defaults"
        )
        return MediaPipeRuntimePolicy()
