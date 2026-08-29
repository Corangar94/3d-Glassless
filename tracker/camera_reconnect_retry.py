"""Bounded local recovery for temporarily unavailable webcam devices."""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Callable


Clock = Callable[[], float]


@dataclass(frozen=True)
class CameraReconnectPolicy:
    """Local camera recovery limits before the tracker process escalates."""

    immediate_retries: int = 1
    max_failures: int = 8
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0
    max_outage_s: float = 45.0
    heartbeat_s: float = 1.0

    def __post_init__(self) -> None:
        values = (
            self.base_delay_s,
            self.max_delay_s,
            self.max_outage_s,
            self.heartbeat_s,
        )
        if not all(math.isfinite(value) and value >= 0.0 for value in values):
            raise ValueError("camera reconnect timings must be finite and non-negative")
        if self.immediate_retries < 0:
            raise ValueError("immediate_retries cannot be negative")
        if self.max_failures < 1:
            raise ValueError("max_failures must be at least one")
        if self.max_delay_s < self.base_delay_s:
            raise ValueError("max_delay_s cannot be smaller than base_delay_s")
        if self.heartbeat_s <= 0.0:
            raise ValueError("heartbeat_s must be positive")


@dataclass(frozen=True)
class CameraReconnectDecision:
    allowed: bool
    delay_s: float
    failure_count: int
    outage_elapsed_s: float
    reason: str


@dataclass(frozen=True)
class CameraReconnectSnapshot:
    failure_count: int
    outage_elapsed_s: float
    last_reason: str


class CameraReconnectBudget:
    """Exponential-backoff budget shared by stalls and failed backend opens."""

    def __init__(
        self,
        policy: CameraReconnectPolicy = CameraReconnectPolicy(),
        *,
        clock: Clock = time.monotonic,
    ) -> None:
        self._policy = policy
        self._clock = clock
        self._failure_count = 0
        self._outage_started_s: float | None = None
        self._last_reason = ""

    @property
    def policy(self) -> CameraReconnectPolicy:
        return self._policy

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def _now(self, value: float | None) -> float:
        now = self._clock() if value is None else float(value)
        if not math.isfinite(now):
            raise ValueError("camera reconnect timestamp must be finite")
        return now

    def record_failure(
        self,
        reason: str,
        *,
        now_s: float | None = None,
    ) -> CameraReconnectDecision:
        now = self._now(now_s)
        if self._outage_started_s is None:
            self._outage_started_s = now
        self._failure_count += 1
        self._last_reason = str(reason).strip() or "camera unavailable"
        elapsed = max(0.0, now - self._outage_started_s)

        if (
            self._failure_count >= self._policy.max_failures
            or elapsed >= self._policy.max_outage_s
        ):
            return CameraReconnectDecision(
                allowed=False,
                delay_s=0.0,
                failure_count=self._failure_count,
                outage_elapsed_s=elapsed,
                reason=self._last_reason,
            )

        if self._failure_count <= self._policy.immediate_retries:
            delay = 0.0
        else:
            exponent = (
                self._failure_count
                - self._policy.immediate_retries
                - 1
            )
            delay = min(
                self._policy.max_delay_s,
                self._policy.base_delay_s * (2.0**exponent),
            )
            remaining = max(0.0, self._policy.max_outage_s - elapsed)
            delay = min(delay, remaining)

        return CameraReconnectDecision(
            allowed=True,
            delay_s=delay,
            failure_count=self._failure_count,
            outage_elapsed_s=elapsed,
            reason=self._last_reason,
        )

    def snapshot(
        self,
        *,
        now_s: float | None = None,
    ) -> CameraReconnectSnapshot:
        now = self._now(now_s)
        elapsed = (
            0.0
            if self._outage_started_s is None
            else max(0.0, now - self._outage_started_s)
        )
        return CameraReconnectSnapshot(
            failure_count=self._failure_count,
            outage_elapsed_s=elapsed,
            last_reason=self._last_reason,
        )

    def reset(self) -> None:
        self._failure_count = 0
        self._outage_started_s = None
        self._last_reason = ""
