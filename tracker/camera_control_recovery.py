"""Recover automatic camera controls after sustained locked-state degradation."""
from __future__ import annotations

from dataclasses import dataclass
import math
import numbers
from typing import Iterable, Protocol

import cv2

from tracker.pose import elapsed_u32_ms, normalize_wire_timestamp


_UINT32_HALF_RANGE = 0x8000_0000
_MAX_TIMING_MS = 60_000
_MAX_ATTEMPTS = 20
_EXPOSURE_PROBLEMS = (
    "underexposed",
    "overexposed",
    "exposure is hunting",
)
_FOCUS_PROBLEMS = ("soft or motion-blurred",)


class CameraControlLike(Protocol):
    def set(self, property_id: int, value: float) -> bool:
        ...


@dataclass(frozen=True)
class CameraControlRecoveryPolicy:
    """Timing and retry bounds for one camera capture session."""

    degradation_hold_ms: int = 2_000
    retry_interval_ms: int = 5_000
    max_attempts_per_episode: int = 3

    def __post_init__(self) -> None:
        for name, value, minimum, maximum in (
            (
                "degradation_hold_ms",
                self.degradation_hold_ms,
                0,
                _MAX_TIMING_MS,
            ),
            (
                "retry_interval_ms",
                self.retry_interval_ms,
                0,
                _MAX_TIMING_MS,
            ),
            (
                "max_attempts_per_episode",
                self.max_attempts_per_episode,
                1,
                _MAX_ATTEMPTS,
            ),
        ):
            if isinstance(value, bool) or not isinstance(
                value,
                numbers.Integral,
            ):
                raise ValueError(f"{name} must be an integer")
            if not minimum <= int(value) <= maximum:
                raise ValueError(
                    f"{name} must be between {minimum} and {maximum}"
                )

    def config_values(self) -> dict[str, int]:
        return {
            "degradation_hold_ms": int(self.degradation_hold_ms),
            "retry_interval_ms": int(self.retry_interval_ms),
            "max_attempts_per_episode": int(
                self.max_attempts_per_episode
            ),
        }


@dataclass(frozen=True)
class CameraControlRecoveryRequest:
    autofocus: bool = False
    auto_exposure: bool = False
    reasons: tuple[str, ...] = ()

    @property
    def requested(self) -> bool:
        return self.autofocus or self.auto_exposure


@dataclass(frozen=True)
class CameraControlRecoverySnapshot:
    focus_problem_since_ms: int | None
    exposure_problem_since_ms: int | None
    focus_last_attempt_ms: int | None
    exposure_last_attempt_ms: int | None
    focus_attempts: int
    exposure_attempts: int
    autofocus_recovery_count: int
    auto_exposure_recovery_count: int
    last_request: CameraControlRecoveryRequest


@dataclass
class _ControlEpisode:
    problem_since_ms: int | None = None
    last_attempt_ms: int | None = None
    attempts: int = 0

    def clear(self) -> None:
        self.problem_since_ms = None
        self.last_attempt_ms = None
        self.attempts = 0


class CameraControlRecovery:
    """Observe quality problems and request selective automatic-mode recovery."""

    def __init__(
        self,
        policy: CameraControlRecoveryPolicy = CameraControlRecoveryPolicy(),
    ) -> None:
        self._policy = policy
        self._focus = _ControlEpisode()
        self._exposure = _ControlEpisode()
        self._autofocus_recovery_count = 0
        self._auto_exposure_recovery_count = 0
        self._last_request = CameraControlRecoveryRequest()

    @property
    def policy(self) -> CameraControlRecoveryPolicy:
        return self._policy

    def reset(self) -> None:
        self._focus.clear()
        self._exposure.clear()
        self._autofocus_recovery_count = 0
        self._auto_exposure_recovery_count = 0
        self._last_request = CameraControlRecoveryRequest()

    @staticmethod
    def _matches(problems: tuple[str, ...], prefixes: tuple[str, ...]) -> bool:
        return any(
            any(problem.startswith(prefix) for prefix in prefixes)
            for problem in problems
        )

    @staticmethod
    def _forward_elapsed_ms(now_ms: int, then_ms: int) -> int | None:
        elapsed = elapsed_u32_ms(now_ms, then_ms)
        return None if elapsed >= _UINT32_HALF_RANGE else elapsed

    def _ready(
        self,
        episode: _ControlEpisode,
        timestamp_ms: int,
        *,
        locked: bool,
        degraded: bool,
    ) -> bool:
        if not locked or not degraded:
            episode.clear()
            return False
        if episode.problem_since_ms is None:
            episode.problem_since_ms = timestamp_ms
        sustained_ms = self._forward_elapsed_ms(
            timestamp_ms,
            episode.problem_since_ms,
        )
        if sustained_ms is None:
            episode.clear()
            episode.problem_since_ms = timestamp_ms
            return False
        if sustained_ms < self._policy.degradation_hold_ms:
            return False
        if episode.attempts >= self._policy.max_attempts_per_episode:
            return False
        if episode.last_attempt_ms is None:
            return True
        since_attempt_ms = self._forward_elapsed_ms(
            timestamp_ms,
            episode.last_attempt_ms,
        )
        if since_attempt_ms is None:
            episode.last_attempt_ms = timestamp_ms
            return False
        return since_attempt_ms >= self._policy.retry_interval_ms

    def observe(
        self,
        timestamp_ms: int,
        problems: Iterable[object],
        lock_state: dict[str, object] | None,
    ) -> CameraControlRecoveryRequest:
        timestamp = normalize_wire_timestamp(timestamp_ms)
        normalized_problems = tuple(str(problem) for problem in problems)
        state = lock_state or {}
        focus_ready = self._ready(
            self._focus,
            timestamp,
            locked=bool(state.get("autofocus_locked", False)),
            degraded=self._matches(normalized_problems, _FOCUS_PROBLEMS),
        )
        exposure_ready = self._ready(
            self._exposure,
            timestamp,
            locked=bool(state.get("auto_exposure_locked", False)),
            degraded=self._matches(
                normalized_problems,
                _EXPOSURE_PROBLEMS,
            ),
        )
        reasons = tuple(
            problem
            for problem in normalized_problems
            if (
                focus_ready
                and any(problem.startswith(prefix) for prefix in _FOCUS_PROBLEMS)
            )
            or (
                exposure_ready
                and any(
                    problem.startswith(prefix)
                    for prefix in _EXPOSURE_PROBLEMS
                )
            )
        )
        request = CameraControlRecoveryRequest(
            autofocus=focus_ready,
            auto_exposure=exposure_ready,
            reasons=reasons,
        )
        self._last_request = request
        return request

    def record_result(
        self,
        timestamp_ms: int,
        request: CameraControlRecoveryRequest,
        result: dict[str, object],
    ) -> tuple[str, ...]:
        """Record one hardware attempt and return successfully recovered groups."""
        timestamp = normalize_wire_timestamp(timestamp_ms)
        recovered: list[str] = []
        if request.autofocus:
            self._focus.attempts += 1
            self._focus.last_attempt_ms = timestamp
            if bool(result.get("autofocus_reenabled", False)):
                self._autofocus_recovery_count += 1
                self._focus.clear()
                recovered.append("autofocus")
        if request.auto_exposure:
            self._exposure.attempts += 1
            self._exposure.last_attempt_ms = timestamp
            if bool(result.get("auto_exposure_reenabled", False)):
                self._auto_exposure_recovery_count += 1
                self._exposure.clear()
                recovered.append("auto exposure")
        return tuple(recovered)

    def snapshot(self) -> CameraControlRecoverySnapshot:
        return CameraControlRecoverySnapshot(
            focus_problem_since_ms=self._focus.problem_since_ms,
            exposure_problem_since_ms=self._exposure.problem_since_ms,
            focus_last_attempt_ms=self._focus.last_attempt_ms,
            exposure_last_attempt_ms=self._exposure.last_attempt_ms,
            focus_attempts=self._focus.attempts,
            exposure_attempts=self._exposure.attempts,
            autofocus_recovery_count=self._autofocus_recovery_count,
            auto_exposure_recovery_count=self._auto_exposure_recovery_count,
            last_request=self._last_request,
        )


def _finite_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _unique(values: Iterable[float | None]) -> tuple[float, ...]:
    result: list[float] = []
    for value in values:
        if value is None:
            continue
        parsed = float(value)
        if not any(abs(parsed - current) <= 1e-6 for current in result):
            result.append(parsed)
    return tuple(result)


def _write_first_accepted(
    cap: CameraControlLike,
    property_id: int | None,
    values: Iterable[float | None],
    name: str,
    errors: list[str],
) -> tuple[bool, float | None]:
    if property_id is None:
        errors.append(f"{name} control is unavailable")
        return False, None
    for value in _unique(values):
        try:
            accepted = bool(cap.set(property_id, value))
        except Exception as error:
            errors.append(f"{name} write failed: {type(error).__name__}")
            continue
        if accepted:
            return True, value
    errors.append(f"{name} write was rejected")
    return False, None


def _previous_automatic_value(
    lock_state: dict[str, object],
    key: str,
    manual_values: tuple[float, ...],
) -> float | None:
    value = _finite_float(lock_state.get(key))
    if value is None or value < 0.0:
        return None
    if any(abs(value - manual) <= 1e-3 for manual in manual_values):
        return None
    return value


def try_restore_automatic_camera_controls(
    cap: CameraControlLike,
    request: CameraControlRecoveryRequest,
    lock_state: dict[str, object] | None,
) -> dict[str, object]:
    """Best-effort selective recovery for controls previously locked manually."""
    state = lock_state or {}
    result: dict[str, object] = {
        "autofocus_requested": request.autofocus,
        "auto_exposure_requested": request.auto_exposure,
    }
    errors: list[str] = []

    if request.autofocus:
        previous = _previous_automatic_value(
            state,
            "autofocus_value",
            (0.0,),
        )
        recovered, value = _write_first_accepted(
            cap,
            getattr(cv2, "CAP_PROP_AUTOFOCUS", None),
            (previous, 1.0),
            "autofocus recovery",
            errors,
        )
        result["autofocus_reenabled"] = recovered
        if value is not None:
            result["autofocus_automatic_value"] = value

    if request.auto_exposure:
        previous = _previous_automatic_value(
            state,
            "auto_exposure_value",
            (0.0, 0.25),
        )
        recovered, value = _write_first_accepted(
            cap,
            getattr(cv2, "CAP_PROP_AUTO_EXPOSURE", None),
            (previous, 0.75, 1.0),
            "auto exposure recovery",
            errors,
        )
        result["auto_exposure_reenabled"] = recovered
        if value is not None:
            result["auto_exposure_automatic_value"] = value

    if errors:
        result["errors"] = tuple(errors)
    return result


def apply_camera_control_recovery(
    lock_state: dict[str, object] | None,
    result: dict[str, object],
) -> dict[str, object]:
    """Return lock-state telemetry updated for successfully restored groups."""
    updated = dict(lock_state or {})
    if bool(result.get("autofocus_reenabled", False)):
        updated["autofocus_locked"] = False
        updated["focus_preserved"] = False
        updated["autofocus_recovery_value"] = result.get(
            "autofocus_automatic_value"
        )
    if bool(result.get("auto_exposure_reenabled", False)):
        updated["auto_exposure_locked"] = False
        updated["exposure_preserved"] = False
        updated["auto_exposure_recovery_value"] = result.get(
            "auto_exposure_automatic_value"
        )
    return updated


def parse_camera_control_recovery_policy(
    camera_config: object,
    *,
    logger=print,
) -> CameraControlRecoveryPolicy:
    """Parse ``camera.control_recovery`` atomically or use safe defaults."""
    camera = camera_config if isinstance(camera_config, dict) else {}
    raw = camera.get("control_recovery", {})
    values = raw if isinstance(raw, dict) else None
    try:
        if values is None:
            raise ValueError("camera.control_recovery must be a mapping")
        return CameraControlRecoveryPolicy(
            degradation_hold_ms=int(
                values.get("degradation_hold_ms", 2_000)
            ),
            retry_interval_ms=int(
                values.get("retry_interval_ms", 5_000)
            ),
            max_attempts_per_episode=int(
                values.get("max_attempts_per_episode", 3)
            ),
        )
    except (TypeError, ValueError, OverflowError):
        logger(
            "[G3D] Invalid camera control-recovery settings; "
            "using safe defaults"
        )
        return CameraControlRecoveryPolicy()
