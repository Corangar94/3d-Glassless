"""Freshness gating for completed asynchronous pose results."""
from __future__ import annotations

from dataclasses import dataclass
import threading

from tracker.async_inference_watchdog import AsyncInferenceFailure
from tracker.pose import elapsed_u32_ms, normalize_wire_timestamp


_UINT32_MASK = 0xFFFF_FFFF
_UINT32_HALF_RANGE = 0x8000_0000


@dataclass(frozen=True)
class AsyncResultFreshnessPolicy:
    """Bound completed-but-obsolete pose results before publication."""

    max_result_age_ms: int = 250
    max_consecutive_stale_results: int = 3
    stale_result_window_ms: int = 1_000

    def __post_init__(self) -> None:
        if self.max_result_age_ms < 0:
            raise ValueError("max_result_age_ms cannot be negative")
        if self.max_consecutive_stale_results < 0:
            raise ValueError(
                "max_consecutive_stale_results cannot be negative"
            )
        if self.stale_result_window_ms < 1:
            raise ValueError("stale_result_window_ms must be at least one")


@dataclass(frozen=True)
class AsyncResultFreshnessSnapshot:
    total_stale_results: int
    consecutive_stale_results: int
    stale_burst_started_ms: int | None
    last_stale_observed_ms: int | None
    last_stale_result_timestamp_ms: int | None
    last_stale_age_ms: int | None


class AsyncResultFreshnessGate:
    """Drop isolated stale poses and escalate a sustained stale-result burst.

    MediaPipe callback progress can remain healthy even when every completed pose
    is too old for head-coupled rendering. Isolated late poses are retired, while
    repeated stale poses inside one bounded observation window surface through
    ``AsyncInferenceFailure`` so automatic backend failover can recover.
    """

    def __init__(
        self,
        policy: AsyncResultFreshnessPolicy = AsyncResultFreshnessPolicy(),
    ) -> None:
        self._policy = policy
        self._lock = threading.Lock()
        self._total_stale_results = 0
        self._consecutive_stale_results = 0
        self._stale_burst_started_ms: int | None = None
        self._last_stale_observed_ms: int | None = None
        self._last_stale_result_timestamp_ms: int | None = None
        self._last_stale_age_ms: int | None = None

    @property
    def policy(self) -> AsyncResultFreshnessPolicy:
        return self._policy

    def reset(self) -> None:
        """Clear both lifetime/session counters and the current stale burst."""
        with self._lock:
            self._total_stale_results = 0
            self._consecutive_stale_results = 0
            self._stale_burst_started_ms = None
            self._last_stale_observed_ms = None
            self._last_stale_result_timestamp_ms = None
            self._last_stale_age_ms = None

    def _reset_burst_locked(self) -> None:
        self._consecutive_stale_results = 0
        self._stale_burst_started_ms = None

    def record_fresh_result(self) -> None:
        """End a stale burst after one usable current pose."""
        with self._lock:
            self._reset_burst_locked()

    def record_result_without_pose(self) -> None:
        """A healthy no-face callback interrupts stale-pose consecutiveness."""
        with self._lock:
            self._reset_burst_locked()

    def accept_result(
        self,
        result_timestamp_ms: int,
        current_timestamp_ms: int,
    ) -> bool:
        """Return whether a completed pose is fresh enough to publish.

        A result timestamp of zero retains the legacy "timestamp unavailable"
        contract. An upper-half uint32 delta means the result is slightly ahead
        of the caller or otherwise out of order, not billions of milliseconds
        old, so it remains eligible and resets the stale burst.
        """
        raw_result_timestamp = int(result_timestamp_ms) & _UINT32_MASK
        current = normalize_wire_timestamp(current_timestamp_ms)
        if raw_result_timestamp == 0 or self._policy.max_result_age_ms == 0:
            self.record_fresh_result()
            return True

        result_timestamp = normalize_wire_timestamp(raw_result_timestamp)
        age_ms = elapsed_u32_ms(current, result_timestamp)
        if (
            age_ms >= _UINT32_HALF_RANGE
            or age_ms <= self._policy.max_result_age_ms
        ):
            self.record_fresh_result()
            return True

        with self._lock:
            burst_start = self._stale_burst_started_ms
            if burst_start is None:
                self._stale_burst_started_ms = current
                self._consecutive_stale_results = 1
            else:
                burst_age_ms = elapsed_u32_ms(current, burst_start)
                if (
                    burst_age_ms >= _UINT32_HALF_RANGE
                    or burst_age_ms > self._policy.stale_result_window_ms
                ):
                    self._stale_burst_started_ms = current
                    self._consecutive_stale_results = 1
                else:
                    self._consecutive_stale_results += 1

            self._total_stale_results += 1
            self._last_stale_observed_ms = current
            self._last_stale_result_timestamp_ms = result_timestamp
            self._last_stale_age_ms = age_ms
            consecutive = self._consecutive_stale_results

        threshold = self._policy.max_consecutive_stale_results
        if threshold > 0 and consecutive >= threshold:
            raise AsyncInferenceFailure(
                "MediaPipe produced "
                f"{consecutive} stale pose results within "
                f"{self._policy.stale_result_window_ms} ms "
                f"(latest age {age_ms} ms, limit "
                f"{self._policy.max_result_age_ms} ms)"
            )
        return False

    def snapshot(self) -> AsyncResultFreshnessSnapshot:
        with self._lock:
            return AsyncResultFreshnessSnapshot(
                total_stale_results=self._total_stale_results,
                consecutive_stale_results=self._consecutive_stale_results,
                stale_burst_started_ms=self._stale_burst_started_ms,
                last_stale_observed_ms=self._last_stale_observed_ms,
                last_stale_result_timestamp_ms=(
                    self._last_stale_result_timestamp_ms
                ),
                last_stale_age_ms=self._last_stale_age_ms,
            )
