"""Short continuity bridge for MediaPipe/OpenCV pose-source transitions."""
from __future__ import annotations

from dataclasses import dataclass
import math

from tracker.pose import HeadPosition, elapsed_u32_ms, normalize_wire_timestamp


@dataclass(frozen=True)
class PoseContinuityPolicy:
    """Bounds for transition alignment and convergence."""

    blend_ms: int = 450
    max_source_age_ms: int = 750
    max_xy_offset_cm: float = 20.0
    max_z_offset_cm: float = 20.0
    max_angle_offset_deg: float = 30.0

    def __post_init__(self) -> None:
        if self.blend_ms < 0:
            raise ValueError("blend_ms cannot be negative")
        if self.max_source_age_ms < 0:
            raise ValueError("max_source_age_ms cannot be negative")
        for name, value in (
            ("max_xy_offset_cm", self.max_xy_offset_cm),
            ("max_z_offset_cm", self.max_z_offset_cm),
            ("max_angle_offset_deg", self.max_angle_offset_deg),
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True)
class _PoseOffset:
    x_cm: float
    y_cm: float
    z_cm: float
    yaw_deg: float
    pitch_deg: float
    roll_deg: float


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def _angle_delta(source_deg: float, target_deg: float) -> float:
    """Return the shortest signed source-minus-target angle."""
    return (float(source_deg) - float(target_deg) + 180.0) % 360.0 - 180.0


def _normalized_angle(value: float) -> float:
    return (float(value) + 180.0) % 360.0 - 180.0


class BackendPoseContinuityBridge:
    """Align a fresh backend briefly to the latest recent published pose.

    The bridge stores only returned measurements, never predictions. A switch
    begins with the last returned pose as its optional alignment source. If that
    source is older than the configured hold-scale age, alignment is skipped;
    the display was already stale/paused and preserving it would be misleading.
    """

    def __init__(
        self,
        policy: PoseContinuityPolicy = PoseContinuityPolicy(),
    ) -> None:
        self._policy = policy
        self._last_output: HeadPosition | None = None
        self._transition_started_ms: int | None = None
        self._transition_source: HeadPosition | None = None
        self._offset: _PoseOffset | None = None
        self._transition_preserves_position = False

    @property
    def policy(self) -> PoseContinuityPolicy:
        return self._policy

    @property
    def transition_active(self) -> bool:
        return self._transition_started_ms is not None

    @property
    def transition_preserves_position(self) -> bool:
        return self._transition_preserves_position

    @property
    def last_output(self) -> HeadPosition | None:
        return self._last_output

    def reset(self) -> None:
        self._last_output = None
        self._transition_started_ms = None
        self._transition_source = None
        self._offset = None
        self._transition_preserves_position = False

    def begin_transition(self, timestamp_ms: int) -> bool:
        started = normalize_wire_timestamp(timestamp_ms)
        self._transition_started_ms = started
        self._transition_source = self._last_output
        self._offset = None
        self._transition_preserves_position = self._source_is_recent(started)
        if not self._transition_preserves_position:
            self._transition_source = None
        return self._transition_preserves_position

    def _source_is_recent(self, transition_timestamp_ms: int) -> bool:
        source = self._transition_source
        if source is None or not source.capture_timestamp_ms:
            return False
        return (
            elapsed_u32_ms(
                transition_timestamp_ms,
                source.capture_timestamp_ms,
            )
            <= self._policy.max_source_age_ms
        )

    def _make_offset(
        self,
        source: HeadPosition,
        target: HeadPosition,
    ) -> _PoseOffset | None:
        values = (
            source.x_cm,
            source.y_cm,
            source.z_cm,
            source.yaw_deg,
            source.pitch_deg,
            source.roll_deg,
            target.x_cm,
            target.y_cm,
            target.z_cm,
            target.yaw_deg,
            target.pitch_deg,
            target.roll_deg,
        )
        if not all(math.isfinite(float(value)) for value in values):
            return None
        return _PoseOffset(
            x_cm=_clamp(
                source.x_cm - target.x_cm,
                self._policy.max_xy_offset_cm,
            ),
            y_cm=_clamp(
                source.y_cm - target.y_cm,
                self._policy.max_xy_offset_cm,
            ),
            z_cm=_clamp(
                source.z_cm - target.z_cm,
                self._policy.max_z_offset_cm,
            ),
            yaw_deg=_clamp(
                _angle_delta(source.yaw_deg, target.yaw_deg),
                self._policy.max_angle_offset_deg,
            ),
            pitch_deg=_clamp(
                _angle_delta(source.pitch_deg, target.pitch_deg),
                self._policy.max_angle_offset_deg,
            ),
            roll_deg=_clamp(
                _angle_delta(source.roll_deg, target.roll_deg),
                self._policy.max_angle_offset_deg,
            ),
        )

    def _finish_transition(self) -> None:
        self._transition_started_ms = None
        self._transition_source = None
        self._offset = None
        self._transition_preserves_position = False

    def apply(
        self,
        pose: HeadPosition | None,
        timestamp_ms: int,
    ) -> HeadPosition | None:
        if pose is None:
            return None

        timestamp = normalize_wire_timestamp(timestamp_ms)
        started = self._transition_started_ms
        if started is None:
            self._last_output = pose
            return pose

        if self._policy.blend_ms <= 0:
            self._finish_transition()
            self._last_output = pose
            return pose

        if self._offset is None:
            source = self._transition_source
            if source is None or not self._transition_preserves_position:
                self._finish_transition()
                self._last_output = pose
                return pose
            self._offset = self._make_offset(source, pose)
            if self._offset is None:
                self._finish_transition()
                self._last_output = pose
                return pose

        elapsed = elapsed_u32_ms(timestamp, started)
        if elapsed >= self._policy.blend_ms:
            self._finish_transition()
            self._last_output = pose
            return pose

        weight = 1.0 - elapsed / float(self._policy.blend_ms)
        offset = self._offset
        assert offset is not None
        adjusted = HeadPosition(
            x_cm=pose.x_cm + offset.x_cm * weight,
            y_cm=pose.y_cm + offset.y_cm * weight,
            z_cm=max(1.0, pose.z_cm + offset.z_cm * weight),
            yaw_deg=_normalized_angle(
                pose.yaw_deg + offset.yaw_deg * weight
            ),
            pitch_deg=_normalized_angle(
                pose.pitch_deg + offset.pitch_deg * weight
            ),
            roll_deg=_normalized_angle(
                pose.roll_deg + offset.roll_deg * weight
            ),
            confidence=pose.confidence,
            capture_timestamp_ms=pose.capture_timestamp_ms,
        )
        self._last_output = adjusted
        return adjusted
