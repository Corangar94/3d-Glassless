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


def _validated_integer(
    value: object,
    field_name: str,
    *,
    minimum: int,
) -> int:
    """Validate a direct policy value without bool or float coercion."""
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise ValueError(f"{field_name} must be an integer")
    parsed = int(value)
    if parsed < minimum:
        if minimum == 0:
            raise ValueError(f"{field_name} cannot be negative")
        raise ValueError(f"{field_name} must be at least {minimum}")
    return parsed


def _parse_integer(value: object, field_name: str) -> int:
    """Parse an explicit base-10 integer without truncation or bool coercion."""
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{field_name} must be an integer")
        try:
            return int(text, 10)
        except ValueError as error:
            raise ValueError(
                f"{field_name} must be an integer"
            ) from error
    raise ValueError(f"{field_name} must be an integer")


def validated_mediapipe_input_width_px(value: object) -> int:
    """Return a bounded MediaPipe width cap; zero explicitly disables it.

    This field retains its established compatibility contract: integral numeric
    values such as ``960.0`` and integer strings remain accepted. The runtime
    timing/count fields use the stricter integer-only policy above because a
    coerced zero or one changes watchdog and admission behavior.
    """
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
    if not MIN_MEDIAPIPE_INPUT_WIDTH_PX <= parsed <= MAX_MEDIAPIPE_INPUT_WIDTH_PX:
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
        for field_name, minimum in (
            ("stall_timeout_ms", 1),
            ("max_consecutive_errors", 1),
            ("max_backlog_ms", 0),
            ("max_result_age_ms", 0),
            ("max_consecutive_stale_results", 0),
            ("stale_result_window_ms", 1),
        ):
            object.__setattr__(
                self,
                field_name,
                _validated_integer(
                    getattr(self, field_name),
                    field_name,
                    minimum=minimum,
                ),
            )
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


def _policy_from_values(
    values: dict[str, object],
    *,
    prefix: str = "",
) -> MediaPipeRuntimePolicy:
    """Build one policy from nested or legacy field names."""
    return MediaPipeRuntimePolicy(
        stall_timeout_ms=_parse_integer(
            values.get(f"{prefix}stall_timeout_ms", 5_000),
            "stall_timeout_ms",
        ),
        max_consecutive_errors=_parse_integer(
            values.get(f"{prefix}max_consecutive_errors", 3),
            "max_consecutive_errors",
        ),
        max_backlog_ms=_parse_integer(
            values.get(f"{prefix}max_backlog_ms", 150),
            "max_backlog_ms",
        ),
        max_result_age_ms=_parse_integer(
            values.get(f"{prefix}max_result_age_ms", 250),
            "max_result_age_ms",
        ),
        max_consecutive_stale_results=_parse_integer(
            values.get(f"{prefix}max_consecutive_stale_results", 3),
            "max_consecutive_stale_results",
        ),
        stale_result_window_ms=_parse_integer(
            values.get(f"{prefix}stale_result_window_ms", 1_000),
            "stale_result_window_ms",
        ),
        max_input_width_px=values.get(
            f"{prefix}max_input_width_px",
            DEFAULT_MEDIAPIPE_INPUT_WIDTH_PX,
        ),
    )


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
            return _policy_from_values(nested)
        return _policy_from_values(tracking, prefix="async_")
    except (TypeError, ValueError, OverflowError):
        logger(
            "[G3D] Invalid MediaPipe runtime settings; "
            "using safe defaults"
        )
        return MediaPipeRuntimePolicy()
