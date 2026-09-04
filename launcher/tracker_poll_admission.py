"""Admit only current-session pose/state snapshots at the launcher boundary."""
from __future__ import annotations

from dataclasses import dataclass
import math
import numbers
import threading


UINT32_MASK = 0xFFFF_FFFF
UINT32_HALF_RANGE = 0x8000_0000
_MAX_POLICY_MS = 60_000
_TRACKING_STATES = frozenset({"tracking", "hold", "paused"})


@dataclass(frozen=True)
class TrackerPollAdmissionPolicy:
    """Freshness and correlation limits for launcher shared-memory polling."""

    maximum_pose_age_ms: int = 800
    maximum_pose_future_skew_ms: int = 25
    maximum_state_lag_ms: int = 100
    legacy_state_grace_ms: int = 500

    def __post_init__(self) -> None:
        for name in (
            "maximum_pose_age_ms",
            "maximum_pose_future_skew_ms",
            "maximum_state_lag_ms",
            "legacy_state_grace_ms",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(
                value,
                numbers.Integral,
            ):
                raise ValueError(f"{name} must be an integer")
            parsed = int(value)
            if not 0 <= parsed <= _MAX_POLICY_MS:
                raise ValueError(
                    f"{name} must be between 0 and {_MAX_POLICY_MS}"
                )
            object.__setattr__(self, name, parsed)


@dataclass(frozen=True)
class PoseAdmissionDecision:
    accepted: bool
    timestamp_ms: int | None
    age_ms: int | None
    future_skew_ms: int | None
    reason: str


@dataclass(frozen=True)
class StateAdmissionDecision:
    """Resolved state plus whether the corresponding pose may be exposed."""

    status: str | None
    publish_pose: bool
    correlated: bool
    legacy_fallback: bool
    reason: str


@dataclass(frozen=True)
class TrackerPollAdmissionSnapshot:
    accepted_pose_count: int
    rejected_pose_count: int
    malformed_pose_count: int
    pre_session_pose_count: int
    stale_pose_count: int
    future_pose_count: int
    correlated_state_count: int
    rejected_state_count: int
    missing_state_count: int
    malformed_state_count: int
    pre_session_state_count: int
    stale_state_count: int
    future_state_count: int
    preserved_state_count: int
    legacy_fallback_count: int
    waiting_state_count: int
    reset_count: int
    session_start_timestamp_ms: int | None
    last_accepted_pose_timestamp_ms: int | None
    last_pose_reason: str
    last_state_reason: str


def _wire_timestamp(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        return None
    parsed = int(value)
    return parsed if 0 <= parsed <= UINT32_MASK else None


def wire_timestamp_ms(monotonic_seconds: object) -> int:
    """Convert one non-negative monotonic time to the shared uint32 clock."""
    if isinstance(monotonic_seconds, bool) or not isinstance(
        monotonic_seconds,
        numbers.Real,
    ):
        raise ValueError("monotonic time must be a finite non-negative number")
    parsed = float(monotonic_seconds)
    if not math.isfinite(parsed) or parsed < 0.0:
        raise ValueError("monotonic time must be a finite non-negative number")
    return int(parsed * 1000.0) & UINT32_MASK


def _forward_delta_ms(newer_ms: int, older_ms: int) -> int | None:
    delta = (newer_ms - older_ms) & UINT32_MASK
    return delta if delta < UINT32_HALF_RANGE else None


def _elapsed_ms(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0.0:
        return None
    return parsed


def _state_snapshot(
    state_data: object,
) -> tuple[str, int] | None:
    if not isinstance(state_data, (tuple, list)) or len(state_data) < 2:
        return None
    raw_state = state_data[0]
    if not isinstance(raw_state, str):
        return None
    state = raw_state.strip().lower()
    timestamp_ms = _wire_timestamp(state_data[1])
    if state not in _TRACKING_STATES or timestamp_ms is None:
        return None
    return state, timestamp_ms


class TrackerPollAdmission:
    """Protect one launcher process from retained or incoherent SHM values."""

    def __init__(
        self,
        policy: TrackerPollAdmissionPolicy = TrackerPollAdmissionPolicy(),
    ) -> None:
        self._policy = policy
        self._lock = threading.RLock()
        self._session_start_timestamp_ms: int | None = None
        self._last_accepted_pose_timestamp_ms: int | None = None
        self._accepted_pose_count = 0
        self._rejected_pose_count = 0
        self._malformed_pose_count = 0
        self._pre_session_pose_count = 0
        self._stale_pose_count = 0
        self._future_pose_count = 0
        self._correlated_state_count = 0
        self._rejected_state_count = 0
        self._missing_state_count = 0
        self._malformed_state_count = 0
        self._pre_session_state_count = 0
        self._stale_state_count = 0
        self._future_state_count = 0
        self._preserved_state_count = 0
        self._legacy_fallback_count = 0
        self._waiting_state_count = 0
        self._reset_count = 0
        self._last_pose_reason = ""
        self._last_state_reason = ""

    @property
    def policy(self) -> TrackerPollAdmissionPolicy:
        return self._policy

    def reset_session(self, session_start_timestamp_ms: object) -> None:
        timestamp_ms = _wire_timestamp(session_start_timestamp_ms)
        if timestamp_ms is None:
            raise ValueError("session start timestamp must be a uint32 integer")
        with self._lock:
            self._session_start_timestamp_ms = timestamp_ms
            self._last_accepted_pose_timestamp_ms = None
            self._last_pose_reason = "poll admission session reset"
            self._last_state_reason = "poll admission session reset"
            self._reset_count += 1

    def _reject_pose(
        self,
        *,
        reason: str,
        timestamp_ms: int | None,
        malformed: bool = False,
        pre_session: bool = False,
        stale: bool = False,
        future: bool = False,
        age_ms: int | None = None,
        future_skew_ms: int | None = None,
    ) -> PoseAdmissionDecision:
        self._rejected_pose_count += 1
        self._malformed_pose_count += int(malformed)
        self._pre_session_pose_count += int(pre_session)
        self._stale_pose_count += int(stale)
        self._future_pose_count += int(future)
        self._last_pose_reason = reason
        return PoseAdmissionDecision(
            accepted=False,
            timestamp_ms=timestamp_ms,
            age_ms=age_ms,
            future_skew_ms=future_skew_ms,
            reason=reason,
        )

    def evaluate_pose(
        self,
        pose_timestamp_ms: object,
        now_timestamp_ms: object,
    ) -> PoseAdmissionDecision:
        """Reject retained, malformed, stale, or implausibly future poses."""
        pose_timestamp = _wire_timestamp(pose_timestamp_ms)
        now_timestamp = _wire_timestamp(now_timestamp_ms)
        with self._lock:
            session_start = self._session_start_timestamp_ms
            if (
                pose_timestamp is None
                or now_timestamp is None
                or session_start is None
            ):
                return self._reject_pose(
                    reason="pose timestamp or admission session is invalid",
                    timestamp_ms=pose_timestamp,
                    malformed=True,
                )

            since_start_ms = _forward_delta_ms(
                pose_timestamp,
                session_start,
            )
            # Equality is deliberately rejected. A retained pre-launch mapping
            # can share the launch millisecond; the current child will publish a
            # later frame, while accepting equality could expose stale state.
            if since_start_ms is None or since_start_ms == 0:
                return self._reject_pose(
                    reason="pose does not belong to the current child session",
                    timestamp_ms=pose_timestamp,
                    pre_session=True,
                )

            age_ms = _forward_delta_ms(now_timestamp, pose_timestamp)
            future_skew_ms: int | None = None
            if age_ms is None:
                future_skew_ms = _forward_delta_ms(
                    pose_timestamp,
                    now_timestamp,
                )
                if (
                    future_skew_ms is None
                    or future_skew_ms
                    > self._policy.maximum_pose_future_skew_ms
                ):
                    return self._reject_pose(
                        reason="pose timestamp is implausibly ahead of launcher time",
                        timestamp_ms=pose_timestamp,
                        future=True,
                        future_skew_ms=future_skew_ms,
                    )
                age_ms = 0
            elif age_ms > self._policy.maximum_pose_age_ms:
                return self._reject_pose(
                    reason="pose is older than the launcher freshness budget",
                    timestamp_ms=pose_timestamp,
                    stale=True,
                    age_ms=age_ms,
                )

            self._accepted_pose_count += 1
            self._last_accepted_pose_timestamp_ms = pose_timestamp
            self._last_pose_reason = "current-session pose accepted"
            return PoseAdmissionDecision(
                accepted=True,
                timestamp_ms=pose_timestamp,
                age_ms=age_ms,
                future_skew_ms=future_skew_ms,
                reason=self._last_pose_reason,
            )

    def _uncorrelated_state(
        self,
        *,
        reason: str,
        current_status: object,
        session_elapsed_ms: object,
    ) -> StateAdmissionDecision:
        self._rejected_state_count += 1
        normalized_current = (
            current_status.strip().lower()
            if isinstance(current_status, str)
            else ""
        )
        if normalized_current in _TRACKING_STATES:
            self._preserved_state_count += 1
            self._last_state_reason = (
                f"{reason}; preserved established launcher state"
            )
            return StateAdmissionDecision(
                status=None,
                publish_pose=True,
                correlated=False,
                legacy_fallback=False,
                reason=self._last_state_reason,
            )

        elapsed_ms = _elapsed_ms(session_elapsed_ms)
        if (
            elapsed_ms is not None
            and elapsed_ms >= self._policy.legacy_state_grace_ms
        ):
            self._legacy_fallback_count += 1
            self._last_state_reason = (
                f"{reason}; legacy state grace expired"
            )
            return StateAdmissionDecision(
                status="tracking",
                publish_pose=True,
                correlated=False,
                legacy_fallback=True,
                reason=self._last_state_reason,
            )

        self._waiting_state_count += 1
        self._last_state_reason = (
            f"{reason}; waiting for current-session state"
        )
        return StateAdmissionDecision(
            status=None,
            publish_pose=False,
            correlated=False,
            legacy_fallback=False,
            reason=self._last_state_reason,
        )

    def resolve_state(
        self,
        pose_timestamp_ms: object,
        state_data: object,
        *,
        current_status: object,
        session_elapsed_ms: object,
    ) -> StateAdmissionDecision:
        """Correlate state to pose or choose a bounded compatibility fallback."""
        pose_timestamp = _wire_timestamp(pose_timestamp_ms)
        parsed_state = _state_snapshot(state_data)
        with self._lock:
            session_start = self._session_start_timestamp_ms
            if parsed_state is None:
                if state_data is None:
                    self._missing_state_count += 1
                    reason = "tracking state mapping is unavailable"
                else:
                    self._malformed_state_count += 1
                    reason = "tracking state snapshot is malformed"
                return self._uncorrelated_state(
                    reason=reason,
                    current_status=current_status,
                    session_elapsed_ms=session_elapsed_ms,
                )
            if pose_timestamp is None or session_start is None:
                self._malformed_state_count += 1
                return self._uncorrelated_state(
                    reason="pose timestamp or admission session is invalid",
                    current_status=current_status,
                    session_elapsed_ms=session_elapsed_ms,
                )

            state, state_timestamp = parsed_state
            since_start_ms = _forward_delta_ms(
                state_timestamp,
                session_start,
            )
            if since_start_ms is None or since_start_ms == 0:
                self._pre_session_state_count += 1
                return self._uncorrelated_state(
                    reason="tracking state does not belong to current session",
                    current_status=current_status,
                    session_elapsed_ms=session_elapsed_ms,
                )

            lag_ms = _forward_delta_ms(
                pose_timestamp,
                state_timestamp,
            )
            if lag_ms is None:
                self._future_state_count += 1
                return self._uncorrelated_state(
                    reason="tracking state is ahead of the pose snapshot",
                    current_status=current_status,
                    session_elapsed_ms=session_elapsed_ms,
                )
            if lag_ms > self._policy.maximum_state_lag_ms:
                self._stale_state_count += 1
                return self._uncorrelated_state(
                    reason="tracking state is too old for the pose snapshot",
                    current_status=current_status,
                    session_elapsed_ms=session_elapsed_ms,
                )

            self._correlated_state_count += 1
            self._last_state_reason = "pose and tracking state correlated"
            return StateAdmissionDecision(
                status=state,
                publish_pose=True,
                correlated=True,
                legacy_fallback=False,
                reason=self._last_state_reason,
            )

    def snapshot(self) -> TrackerPollAdmissionSnapshot:
        with self._lock:
            return TrackerPollAdmissionSnapshot(
                accepted_pose_count=self._accepted_pose_count,
                rejected_pose_count=self._rejected_pose_count,
                malformed_pose_count=self._malformed_pose_count,
                pre_session_pose_count=self._pre_session_pose_count,
                stale_pose_count=self._stale_pose_count,
                future_pose_count=self._future_pose_count,
                correlated_state_count=self._correlated_state_count,
                rejected_state_count=self._rejected_state_count,
                missing_state_count=self._missing_state_count,
                malformed_state_count=self._malformed_state_count,
                pre_session_state_count=self._pre_session_state_count,
                stale_state_count=self._stale_state_count,
                future_state_count=self._future_state_count,
                preserved_state_count=self._preserved_state_count,
                legacy_fallback_count=self._legacy_fallback_count,
                waiting_state_count=self._waiting_state_count,
                reset_count=self._reset_count,
                session_start_timestamp_ms=(
                    self._session_start_timestamp_ms
                ),
                last_accepted_pose_timestamp_ms=(
                    self._last_accepted_pose_timestamp_ms
                ),
                last_pose_reason=self._last_pose_reason,
                last_state_reason=self._last_state_reason,
            )
