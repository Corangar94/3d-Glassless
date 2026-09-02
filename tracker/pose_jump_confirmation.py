"""Confirm extreme pose changes before they steer the virtual camera."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable

from tracker.backend_transition_state import (
    current_backend_transition_generation,
)
from tracker.pose import elapsed_u32_ms, normalize_wire_timestamp


LogFunction = Callable[[str], None]
_UINT32_HALF_RANGE = 0x8000_0000


@dataclass(frozen=True)
class PoseJumpConfirmationPolicy:
    """Time-aware thresholds for a likely landmark error or viewer switch.

    The trigger is intentionally above the normal raw-pose speed limiter. A
    physically plausible fast movement therefore keeps the existing low-latency
    path, while a much larger discontinuity must appear consistently twice.
    """

    enabled: bool = True
    minimum_xy_jump_cm: float = 20.0
    minimum_z_jump_cm: float = 25.0
    minimum_angle_jump_deg: float = 35.0
    trigger_xy_speed_cm_s: float = 600.0
    trigger_z_speed_cm_s: float = 720.0
    trigger_angular_speed_deg_s: float = 1080.0
    confirmation_samples: int = 2
    candidate_xy_tolerance_cm: float = 12.0
    candidate_z_tolerance_cm: float = 15.0
    candidate_angle_tolerance_deg: float = 20.0
    candidate_timeout_ms: int = 250
    reset_after_ms: int = 750
    minimum_candidate_confidence: float = 0.45

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")
        for name, value in (
            ("minimum_xy_jump_cm", self.minimum_xy_jump_cm),
            ("minimum_z_jump_cm", self.minimum_z_jump_cm),
            ("minimum_angle_jump_deg", self.minimum_angle_jump_deg),
            ("trigger_xy_speed_cm_s", self.trigger_xy_speed_cm_s),
            ("trigger_z_speed_cm_s", self.trigger_z_speed_cm_s),
            (
                "trigger_angular_speed_deg_s",
                self.trigger_angular_speed_deg_s,
            ),
            ("candidate_xy_tolerance_cm", self.candidate_xy_tolerance_cm),
            ("candidate_z_tolerance_cm", self.candidate_z_tolerance_cm),
            (
                "candidate_angle_tolerance_deg",
                self.candidate_angle_tolerance_deg,
            ),
        ):
            if not math.isfinite(float(value)) or float(value) < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.confirmation_samples < 2:
            raise ValueError("confirmation_samples must be at least two")
        if self.candidate_timeout_ms < 1:
            raise ValueError("candidate_timeout_ms must be at least one")
        if self.reset_after_ms < self.candidate_timeout_ms:
            raise ValueError(
                "reset_after_ms must be at least candidate_timeout_ms"
            )
        confidence = float(self.minimum_candidate_confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError(
                "minimum_candidate_confidence must be in [0, 1]"
            )

    def config_values(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "minimum_xy_jump_cm": self.minimum_xy_jump_cm,
            "minimum_z_jump_cm": self.minimum_z_jump_cm,
            "minimum_angle_jump_deg": self.minimum_angle_jump_deg,
            "trigger_xy_speed_cm_s": self.trigger_xy_speed_cm_s,
            "trigger_z_speed_cm_s": self.trigger_z_speed_cm_s,
            "trigger_angular_speed_deg_s": (
                self.trigger_angular_speed_deg_s
            ),
            "confirmation_samples": self.confirmation_samples,
            "candidate_xy_tolerance_cm": self.candidate_xy_tolerance_cm,
            "candidate_z_tolerance_cm": self.candidate_z_tolerance_cm,
            "candidate_angle_tolerance_deg": (
                self.candidate_angle_tolerance_deg
            ),
            "candidate_timeout_ms": self.candidate_timeout_ms,
            "reset_after_ms": self.reset_after_ms,
            "minimum_candidate_confidence": (
                self.minimum_candidate_confidence
            ),
        }


@dataclass(frozen=True)
class PoseJumpConfirmationSnapshot:
    accepted_count: int
    suspected_jump_count: int
    confirmed_jump_count: int
    rejected_candidate_count: int
    low_confidence_jump_count: int
    candidate_sample_count: int
    anchor_timestamp_ms: int | None
    candidate_timestamp_ms: int | None
    last_rejection_reason: str
    backend_transition_generation: int


@dataclass(frozen=True)
class _PoseSample:
    x_cm: float
    y_cm: float
    z_cm: float
    yaw_deg: float
    pitch_deg: float
    roll_deg: float
    confidence: float
    timestamp_ms: int


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0"}:
            return False
    raise ValueError("enabled must be a boolean")


def parse_pose_jump_confirmation_policy(
    tracking_config: object,
    *,
    logger: LogFunction = print,
) -> PoseJumpConfirmationPolicy:
    """Parse ``tracking.pose_jump_confirmation`` atomically."""
    tracking = tracking_config if isinstance(tracking_config, dict) else {}
    raw = tracking.get("pose_jump_confirmation", {})
    values = raw if isinstance(raw, dict) else None
    try:
        if values is None:
            raise ValueError("pose_jump_confirmation must be a mapping")
        defaults = PoseJumpConfirmationPolicy()
        return PoseJumpConfirmationPolicy(
            enabled=_parse_bool(values.get("enabled", defaults.enabled)),
            minimum_xy_jump_cm=float(
                values.get(
                    "minimum_xy_jump_cm",
                    defaults.minimum_xy_jump_cm,
                )
            ),
            minimum_z_jump_cm=float(
                values.get("minimum_z_jump_cm", defaults.minimum_z_jump_cm)
            ),
            minimum_angle_jump_deg=float(
                values.get(
                    "minimum_angle_jump_deg",
                    defaults.minimum_angle_jump_deg,
                )
            ),
            trigger_xy_speed_cm_s=float(
                values.get(
                    "trigger_xy_speed_cm_s",
                    defaults.trigger_xy_speed_cm_s,
                )
            ),
            trigger_z_speed_cm_s=float(
                values.get(
                    "trigger_z_speed_cm_s",
                    defaults.trigger_z_speed_cm_s,
                )
            ),
            trigger_angular_speed_deg_s=float(
                values.get(
                    "trigger_angular_speed_deg_s",
                    defaults.trigger_angular_speed_deg_s,
                )
            ),
            confirmation_samples=int(
                values.get(
                    "confirmation_samples",
                    defaults.confirmation_samples,
                )
            ),
            candidate_xy_tolerance_cm=float(
                values.get(
                    "candidate_xy_tolerance_cm",
                    defaults.candidate_xy_tolerance_cm,
                )
            ),
            candidate_z_tolerance_cm=float(
                values.get(
                    "candidate_z_tolerance_cm",
                    defaults.candidate_z_tolerance_cm,
                )
            ),
            candidate_angle_tolerance_deg=float(
                values.get(
                    "candidate_angle_tolerance_deg",
                    defaults.candidate_angle_tolerance_deg,
                )
            ),
            candidate_timeout_ms=int(
                values.get(
                    "candidate_timeout_ms",
                    defaults.candidate_timeout_ms,
                )
            ),
            reset_after_ms=int(
                values.get("reset_after_ms", defaults.reset_after_ms)
            ),
            minimum_candidate_confidence=float(
                values.get(
                    "minimum_candidate_confidence",
                    defaults.minimum_candidate_confidence,
                )
            ),
        )
    except (TypeError, ValueError, OverflowError):
        logger(
            "[G3D] Invalid pose-jump confirmation settings; "
            "using safe defaults"
        )
        return PoseJumpConfirmationPolicy()


def _angle_distance(first_deg: float, second_deg: float) -> float:
    return abs((float(first_deg) - float(second_deg) + 180.0) % 360.0 - 180.0)


def _forward_delta_ms(newer_ms: int, older_ms: int) -> int | None:
    delta = elapsed_u32_ms(newer_ms, older_ms)
    return None if delta >= _UINT32_HALF_RANGE else delta


class PoseJumpConfirmationGate:
    """Reject one-frame extreme pose discontinuities and confirm persistence."""

    def __init__(
        self,
        policy: PoseJumpConfirmationPolicy = PoseJumpConfirmationPolicy(),
    ) -> None:
        self._policy = policy
        self._anchor: _PoseSample | None = None
        self._candidate: _PoseSample | None = None
        self._candidate_sample_count = 0
        self._accepted_count = 0
        self._suspected_jump_count = 0
        self._confirmed_jump_count = 0
        self._rejected_candidate_count = 0
        self._low_confidence_jump_count = 0
        self._last_rejection_reason = ""
        self._backend_transition_generation = (
            current_backend_transition_generation()
        )

    @property
    def policy(self) -> PoseJumpConfirmationPolicy:
        return self._policy

    def reset(self) -> None:
        """Start a new viewer episode while retaining lifetime counters."""
        self._anchor = None
        self._candidate = None
        self._candidate_sample_count = 0
        self._last_rejection_reason = ""
        self._backend_transition_generation = (
            current_backend_transition_generation()
        )

    def _synchronize_backend_transition(self) -> None:
        generation = current_backend_transition_generation()
        if generation == self._backend_transition_generation:
            return
        self._anchor = None
        self._candidate = None
        self._candidate_sample_count = 0
        self._last_rejection_reason = ""
        self._backend_transition_generation = generation

    @staticmethod
    def _sample(value: object) -> _PoseSample | None:
        names = (
            "x_cm",
            "y_cm",
            "z_cm",
            "yaw_deg",
            "pitch_deg",
            "roll_deg",
            "confidence",
            "capture_timestamp_ms",
        )
        try:
            raw = [getattr(value, name) for name in names]
        except (AttributeError, TypeError, ValueError):
            return None
        try:
            numbers = [float(component) for component in raw[:7]]
            timestamp = int(raw[7])
        except (TypeError, ValueError, OverflowError):
            return None
        if timestamp <= 0 or not all(math.isfinite(item) for item in numbers):
            return None
        return _PoseSample(
            x_cm=numbers[0],
            y_cm=numbers[1],
            z_cm=numbers[2],
            yaw_deg=numbers[3],
            pitch_deg=numbers[4],
            roll_deg=numbers[5],
            confidence=numbers[6],
            timestamp_ms=normalize_wire_timestamp(timestamp),
        )

    @staticmethod
    def _differences(
        first: _PoseSample,
        second: _PoseSample,
    ) -> tuple[float, float, float]:
        xy_cm = math.hypot(
            first.x_cm - second.x_cm,
            first.y_cm - second.y_cm,
        )
        z_cm = abs(first.z_cm - second.z_cm)
        angle_deg = max(
            _angle_distance(first.yaw_deg, second.yaw_deg),
            _angle_distance(first.pitch_deg, second.pitch_deg),
            _angle_distance(first.roll_deg, second.roll_deg),
        )
        return xy_cm, z_cm, angle_deg

    def _is_extreme_jump(
        self,
        sample: _PoseSample,
        anchor: _PoseSample,
        delta_ms: int,
    ) -> bool:
        dt_seconds = max(0.001, delta_ms / 1000.0)
        xy_limit = max(
            self._policy.minimum_xy_jump_cm,
            self._policy.trigger_xy_speed_cm_s * dt_seconds,
        )
        z_limit = max(
            self._policy.minimum_z_jump_cm,
            self._policy.trigger_z_speed_cm_s * dt_seconds,
        )
        angle_limit = max(
            self._policy.minimum_angle_jump_deg,
            self._policy.trigger_angular_speed_deg_s * dt_seconds,
        )
        xy_cm, z_cm, angle_deg = self._differences(sample, anchor)
        return (
            xy_cm > xy_limit
            or z_cm > z_limit
            or angle_deg > angle_limit
        )

    def _candidate_matches(self, sample: _PoseSample) -> bool:
        candidate = self._candidate
        if candidate is None:
            return False
        delta_ms = _forward_delta_ms(
            sample.timestamp_ms,
            candidate.timestamp_ms,
        )
        if delta_ms is None or delta_ms > self._policy.candidate_timeout_ms:
            return False
        xy_cm, z_cm, angle_deg = self._differences(sample, candidate)
        return (
            xy_cm <= self._policy.candidate_xy_tolerance_cm
            and z_cm <= self._policy.candidate_z_tolerance_cm
            and angle_deg <= self._policy.candidate_angle_tolerance_deg
        )

    def _accept(self, value: Any, sample: _PoseSample) -> Any:
        self._anchor = sample
        self._candidate = None
        self._candidate_sample_count = 0
        self._accepted_count += 1
        self._last_rejection_reason = ""
        return value

    def filter(self, value: Any) -> Any:
        """Return an accepted pose-like value, or ``None`` while confirming."""
        self._synchronize_backend_transition()
        if value is None or not self._policy.enabled:
            return value
        sample = self._sample(value)
        if sample is None:
            # Opaque and timestamp-less direct integrations retain their
            # historical behavior. Earlier validation remains authoritative for
            # malformed real pose packets.
            return value
        anchor = self._anchor
        if anchor is None:
            return self._accept(value, sample)

        delta_ms = _forward_delta_ms(
            sample.timestamp_ms,
            anchor.timestamp_ms,
        )
        if delta_ms is None or delta_ms >= self._policy.reset_after_ms:
            return self._accept(value, sample)
        if not self._is_extreme_jump(sample, anchor, delta_ms):
            return self._accept(value, sample)

        self._suspected_jump_count += 1
        if sample.confidence < self._policy.minimum_candidate_confidence:
            self._candidate = None
            self._candidate_sample_count = 0
            self._low_confidence_jump_count += 1
            self._rejected_candidate_count += 1
            self._last_rejection_reason = (
                "extreme pose jump below confirmation confidence"
            )
            return None

        if self._candidate_matches(sample):
            self._candidate = sample
            self._candidate_sample_count += 1
        else:
            self._candidate = sample
            self._candidate_sample_count = 1

        if self._candidate_sample_count >= self._policy.confirmation_samples:
            self._confirmed_jump_count += 1
            return self._accept(value, sample)

        self._rejected_candidate_count += 1
        self._last_rejection_reason = "extreme pose jump awaiting confirmation"
        return None

    def snapshot(self) -> PoseJumpConfirmationSnapshot:
        return PoseJumpConfirmationSnapshot(
            accepted_count=self._accepted_count,
            suspected_jump_count=self._suspected_jump_count,
            confirmed_jump_count=self._confirmed_jump_count,
            rejected_candidate_count=self._rejected_candidate_count,
            low_confidence_jump_count=self._low_confidence_jump_count,
            candidate_sample_count=self._candidate_sample_count,
            anchor_timestamp_ms=(
                None if self._anchor is None else self._anchor.timestamp_ms
            ),
            candidate_timestamp_ms=(
                None
                if self._candidate is None
                else self._candidate.timestamp_ms
            ),
            last_rejection_reason=self._last_rejection_reason,
            backend_transition_generation=(
                self._backend_transition_generation
            ),
        )
