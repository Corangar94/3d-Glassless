"""Bounded, deterministic recovery policy for tracker and overlay failures.

The GUI owns process lifetimes, but recovery decisions live here so crash-loop
protection can be tested without Qt, a camera, or a GPU. The first failure may
retry immediately; repeated failures use exponential backoff and eventually
open a cooldown circuit until the user retries or the cooldown expires.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import time
from typing import Callable


Clock = Callable[[], float]


@dataclass(frozen=True)
class RecoveryPolicy:
    """Recovery timings in seconds."""

    immediate_retries: int = 1
    base_delay_s: float = 1.0
    max_delay_s: float = 20.0
    max_failures: int = 5
    failure_window_s: float = 90.0
    cooldown_s: float = 60.0
    stable_reset_s: float = 30.0

    def __post_init__(self) -> None:
        numeric = (
            self.base_delay_s,
            self.max_delay_s,
            self.failure_window_s,
            self.cooldown_s,
            self.stable_reset_s,
        )
        if not all(math.isfinite(value) and value >= 0.0 for value in numeric):
            raise ValueError("recovery timings must be finite and non-negative")
        if self.immediate_retries < 0:
            raise ValueError("immediate_retries cannot be negative")
        if self.max_failures < 1:
            raise ValueError("max_failures must be at least one")
        if self.max_delay_s < self.base_delay_s:
            raise ValueError("max_delay_s cannot be smaller than base_delay_s")


@dataclass(frozen=True)
class RecoveryDecision:
    component: str
    allowed: bool
    delay_s: float
    circuit_open: bool
    failure_count: int
    retry_at_s: float | None
    reason: str


@dataclass(frozen=True)
class RecoverySnapshot:
    component: str
    failure_count: int
    consecutive_failures: int
    circuit_open: bool
    retry_after_s: float
    last_reason: str
    stable_for_s: float


@dataclass
class _ComponentState:
    failures: deque[float] = field(default_factory=deque)
    consecutive_failures: int = 0
    open_until_s: float = 0.0
    last_reason: str = ""
    healthy_since_s: float | None = None


class RuntimeRecoveryController:
    """Component-scoped exponential backoff and circuit breaking."""

    def __init__(
        self,
        policy: RecoveryPolicy = RecoveryPolicy(),
        *,
        clock: Clock = time.monotonic,
    ) -> None:
        self._policy = policy
        self._clock = clock
        self._states: dict[str, _ComponentState] = {}

    @property
    def policy(self) -> RecoveryPolicy:
        return self._policy

    def _now(self, value: float | None) -> float:
        now = self._clock() if value is None else float(value)
        if not math.isfinite(now):
            raise ValueError("recovery timestamp must be finite")
        return now

    def _state(self, component: str) -> _ComponentState:
        key = str(component).strip().lower()
        if not key:
            raise ValueError("recovery component cannot be empty")
        return self._states.setdefault(key, _ComponentState())

    def _prune(self, state: _ComponentState, now_s: float) -> None:
        cutoff = now_s - self._policy.failure_window_s
        while state.failures and state.failures[0] < cutoff:
            state.failures.popleft()
        if state.open_until_s and now_s >= state.open_until_s:
            state.open_until_s = 0.0
            state.consecutive_failures = 0
            state.failures.clear()
            state.healthy_since_s = None

    def record_failure(
        self,
        component: str,
        reason: str,
        *,
        now_s: float | None = None,
    ) -> RecoveryDecision:
        now = self._now(now_s)
        key = str(component).strip().lower()
        state = self._state(key)
        self._prune(state, now)
        state.healthy_since_s = None
        state.last_reason = str(reason).strip() or "runtime failure"

        if state.open_until_s > now:
            return RecoveryDecision(
                component=key,
                allowed=False,
                delay_s=max(0.0, state.open_until_s - now),
                circuit_open=True,
                failure_count=len(state.failures),
                retry_at_s=state.open_until_s,
                reason=state.last_reason,
            )

        state.failures.append(now)
        state.consecutive_failures += 1
        failure_count = len(state.failures)
        if failure_count >= self._policy.max_failures:
            state.open_until_s = now + self._policy.cooldown_s
            return RecoveryDecision(
                component=key,
                allowed=False,
                delay_s=self._policy.cooldown_s,
                circuit_open=True,
                failure_count=failure_count,
                retry_at_s=state.open_until_s,
                reason=state.last_reason,
            )

        delayed_attempt_index = max(
            0,
            state.consecutive_failures - self._policy.immediate_retries - 1,
        )
        if state.consecutive_failures <= self._policy.immediate_retries:
            delay = 0.0
        else:
            delay = min(
                self._policy.max_delay_s,
                self._policy.base_delay_s * (2.0**delayed_attempt_index),
            )
        return RecoveryDecision(
            component=key,
            allowed=True,
            delay_s=delay,
            circuit_open=False,
            failure_count=failure_count,
            retry_at_s=now + delay,
            reason=state.last_reason,
        )

    def mark_healthy(
        self,
        component: str,
        *,
        now_s: float | None = None,
    ) -> bool:
        """Return True when a stable interval reset the failure history."""
        now = self._now(now_s)
        state = self._state(component)
        self._prune(state, now)
        if state.open_until_s > now:
            return False
        if state.healthy_since_s is None:
            state.healthy_since_s = now
            return False
        if now - state.healthy_since_s < self._policy.stable_reset_s:
            return False
        state.failures.clear()
        state.consecutive_failures = 0
        state.open_until_s = 0.0
        state.last_reason = ""
        state.healthy_since_s = now
        return True

    def reset(self, component: str | None = None) -> None:
        """Clear one circuit, or every circuit after an explicit user retry."""
        if component is None:
            self._states.clear()
            return
        key = str(component).strip().lower()
        if key:
            self._states.pop(key, None)

    def snapshot(
        self,
        component: str,
        *,
        now_s: float | None = None,
    ) -> RecoverySnapshot:
        now = self._now(now_s)
        key = str(component).strip().lower()
        state = self._state(key)
        self._prune(state, now)
        stable_for = (
            max(0.0, now - state.healthy_since_s)
            if state.healthy_since_s is not None
            else 0.0
        )
        return RecoverySnapshot(
            component=key,
            failure_count=len(state.failures),
            consecutive_failures=state.consecutive_failures,
            circuit_open=state.open_until_s > now,
            retry_after_s=max(0.0, state.open_until_s - now),
            last_reason=state.last_reason,
            stable_for_s=stable_for,
        )
