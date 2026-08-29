"""Bounded retry policy for optional webcam focus/exposure locking."""
from __future__ import annotations

from dataclasses import dataclass

from tracker.pose import elapsed_u32_ms, normalize_wire_timestamp


_CONTROL_RESULT_KEYS = ("autofocus_locked", "auto_exposure_locked")


def camera_controls_locked(result: dict[str, object]) -> bool:
    """Return whether every control exposed by the backend was locked.

    Missing result keys mean the corresponding OpenCV property is unavailable,
    so there is nothing to retry. A present false key means the backend exposed
    that operation but did not accept manual mode during this attempt.
    """
    attempted = [bool(result[key]) for key in _CONTROL_RESULT_KEYS if key in result]
    return not attempted or all(attempted)


@dataclass
class CameraControlLockRetry:
    """Wrap-safe, bounded retry state for one camera capture session."""

    max_attempts: int = 3
    retry_interval_ms: int = 5000
    attempts: int = 0
    last_attempt_timestamp_ms: int | None = None
    complete: bool = False

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.retry_interval_ms < 0:
            raise ValueError("retry_interval_ms cannot be negative")

    @property
    def exhausted(self) -> bool:
        return not self.complete and self.attempts >= self.max_attempts

    @property
    def remaining_attempts(self) -> int:
        return max(0, self.max_attempts - self.attempts)

    def reset(self) -> None:
        """Start a fresh policy window for a replacement camera handle."""
        self.attempts = 0
        self.last_attempt_timestamp_ms = None
        self.complete = False

    def should_attempt(self, timestamp_ms: int, *, stable_for_lock: bool) -> bool:
        if not stable_for_lock or self.complete or self.exhausted:
            return False
        if self.last_attempt_timestamp_ms is None:
            return True
        return (
            elapsed_u32_ms(
                normalize_wire_timestamp(timestamp_ms),
                self.last_attempt_timestamp_ms,
            )
            >= self.retry_interval_ms
        )

    def record_result(
        self,
        timestamp_ms: int,
        result: dict[str, object],
    ) -> bool:
        """Record one attempted lock and return whether retrying is complete."""
        if self.complete:
            return True
        if self.exhausted:
            return False
        self.attempts += 1
        self.last_attempt_timestamp_ms = normalize_wire_timestamp(timestamp_ms)
        self.complete = camera_controls_locked(result)
        return self.complete
