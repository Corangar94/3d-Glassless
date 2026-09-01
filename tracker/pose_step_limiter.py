"""Translation spike limits before Kalman pose filtering."""
from __future__ import annotations

from dataclasses import dataclass
import math

from tracker.pose import HeadPosition, elapsed_u32_ms, normalize_wire_timestamp


_UINT32_HALF_RANGE = 0x8000_0000


@dataclass(frozen=True)
class PoseStepLimiterPolicy:
    """Physical-speed bounds for raw tracker translation.

    A zero speed disables that axis bound. ``reset_after_ms`` starts a fresh
    episode after a long measurement gap, preventing a newly reacquired viewer
    from being dragged toward a pose that was already stale or paused.
    """

    max_xy_speed_cm_s: float = 300.0
    max_z_speed_cm_s: float = 360.0
    reset_after_ms: int = 500

    def __post_init__(self) -> None:
        for name, value in (
            ("max_xy_speed_cm_s", self.max_xy_speed_cm_s),
            ("max_z_speed_cm_s", self.max_z_speed_cm_s),
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.reset_after_ms < 1:
            raise ValueError("reset_after_ms must be at least one")


@dataclass(frozen=True)
class PoseStepLimiterSnapshot:
    last_position: tuple[float, float, float] | None
    last_timestamp_ms: int | None
    limited_sample_count: int
    duplicate_or_out_of_order_count: int
    episode_reset_count: int
    last_interval_ms: int | None
    last_xy_limit_cm: float | None
    last_z_limit_cm: float | None


def _validated_position(
    values: tuple[float, float, float],
) -> tuple[float, float, float]:
    parsed = tuple(float(value) for value in values)
    if len(parsed) != 3 or not all(math.isfinite(value) for value in parsed):
        raise ValueError("raw pose translation must contain three finite values")
    if parsed[2] <= 0.0:
        raise ValueError("raw pose depth must be positive")
    return parsed  # type: ignore[return-value]


def _limited_head_position(
    pose: HeadPosition,
    limited: tuple[float, float, float],
) -> HeadPosition:
    if limited == pose.xyz:
        return pose
    return HeadPosition(
        x_cm=limited[0],
        y_cm=limited[1],
        z_cm=limited[2],
        yaw_deg=pose.yaw_deg,
        pitch_deg=pose.pitch_deg,
        roll_deg=pose.roll_deg,
        confidence=pose.confidence,
        capture_timestamp_ms=pose.capture_timestamp_ms,
    )


def limit_pose_step(
    raw: tuple[float, float, float],
    previous: tuple[float, float, float] | None,
    *,
    maximum_xy_step_cm: float,
    maximum_z_step_cm: float,
) -> tuple[float, float, float]:
    """Clamp one translation step while preserving horizontal direction."""
    if previous is None:
        return raw
    dx = raw[0] - previous[0]
    dy = raw[1] - previous[1]
    distance = math.hypot(dx, dy)
    if maximum_xy_step_cm > 0.0 and distance > maximum_xy_step_cm:
        scale = maximum_xy_step_cm / distance
        x = previous[0] + dx * scale
        y = previous[1] + dy * scale
    else:
        x, y = raw[0], raw[1]
    dz = raw[2] - previous[2]
    if maximum_z_step_cm > 0.0:
        dz = max(-maximum_z_step_cm, min(maximum_z_step_cm, dz))
    return x, y, previous[2] + dz


class FixedPoseStepLimiter:
    """Historical fixed-per-measurement limiter for direct loop callers.

    The packaged runtime explicitly injects ``PoseStepLimiter``. Keeping this
    compatibility implementation as the ``TrackingLoop`` default prevents
    timestamp-less test doubles and third-party direct callers from acquiring a
    new frame-rate dependency merely by upgrading the library.
    """

    def __init__(
        self,
        *,
        maximum_xy_step_cm: float = 10.0,
        maximum_z_step_cm: float = 12.0,
    ) -> None:
        for name, value in (
            ("maximum_xy_step_cm", maximum_xy_step_cm),
            ("maximum_z_step_cm", maximum_z_step_cm),
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        self._maximum_xy_step_cm = float(maximum_xy_step_cm)
        self._maximum_z_step_cm = float(maximum_z_step_cm)
        self._last_position: tuple[float, float, float] | None = None

    def reset(self) -> None:
        self._last_position = None

    def limit_head_position(self, pose: HeadPosition) -> HeadPosition:
        position = _validated_position(pose.xyz)
        limited = limit_pose_step(
            position,
            self._last_position,
            maximum_xy_step_cm=self._maximum_xy_step_cm,
            maximum_z_step_cm=self._maximum_z_step_cm,
        )
        self._last_position = limited
        return _limited_head_position(pose, limited)


class PoseStepLimiter:
    """Convert per-measurement spike rejection into a physical-speed bound."""

    def __init__(
        self,
        policy: PoseStepLimiterPolicy = PoseStepLimiterPolicy(),
    ) -> None:
        self._policy = policy
        self._last_position: tuple[float, float, float] | None = None
        self._last_timestamp_ms: int | None = None
        self._limited_sample_count = 0
        self._duplicate_or_out_of_order_count = 0
        self._episode_reset_count = 0
        self._last_interval_ms: int | None = None
        self._last_xy_limit_cm: float | None = None
        self._last_z_limit_cm: float | None = None

    @property
    def policy(self) -> PoseStepLimiterPolicy:
        return self._policy

    def reset(self) -> None:
        self._last_position = None
        self._last_timestamp_ms = None
        self._last_interval_ms = None
        self._last_xy_limit_cm = None
        self._last_z_limit_cm = None

    def _accept_new_episode(
        self,
        position: tuple[float, float, float],
        timestamp_ms: int,
        *,
        count_reset: bool,
    ) -> tuple[float, float, float]:
        self._last_position = position
        self._last_timestamp_ms = timestamp_ms
        self._last_interval_ms = None
        self._last_xy_limit_cm = None
        self._last_z_limit_cm = None
        if count_reset:
            self._episode_reset_count += 1
        return position

    def limit(
        self,
        raw: tuple[float, float, float],
        capture_timestamp_ms: int,
    ) -> tuple[float, float, float]:
        position = _validated_position(raw)
        timestamp = normalize_wire_timestamp(capture_timestamp_ms)
        previous = self._last_position
        previous_timestamp = self._last_timestamp_ms
        if previous is None or previous_timestamp is None:
            return self._accept_new_episode(
                position,
                timestamp,
                count_reset=False,
            )

        interval_ms = elapsed_u32_ms(timestamp, previous_timestamp)
        if interval_ms == 0 or interval_ms >= _UINT32_HALF_RANGE:
            # A duplicate or older measurement cannot claim additional physical
            # travel time. Keep the last accepted translation and timestamp.
            self._duplicate_or_out_of_order_count += 1
            self._last_interval_ms = interval_ms
            self._last_xy_limit_cm = 0.0
            self._last_z_limit_cm = 0.0
            if position != previous:
                self._limited_sample_count += 1
            return previous

        if interval_ms >= self._policy.reset_after_ms:
            return self._accept_new_episode(
                position,
                timestamp,
                count_reset=True,
            )

        interval_s = interval_ms / 1000.0
        maximum_xy_step_cm = (
            self._policy.max_xy_speed_cm_s * interval_s
            if self._policy.max_xy_speed_cm_s > 0.0
            else 0.0
        )
        maximum_z_step_cm = (
            self._policy.max_z_speed_cm_s * interval_s
            if self._policy.max_z_speed_cm_s > 0.0
            else 0.0
        )
        limited = limit_pose_step(
            position,
            previous,
            maximum_xy_step_cm=maximum_xy_step_cm,
            maximum_z_step_cm=maximum_z_step_cm,
        )
        self._last_position = limited
        self._last_timestamp_ms = timestamp
        self._last_interval_ms = interval_ms
        self._last_xy_limit_cm = maximum_xy_step_cm
        self._last_z_limit_cm = maximum_z_step_cm
        if limited != position:
            self._limited_sample_count += 1
        return limited

    def limit_head_position(self, pose: HeadPosition) -> HeadPosition:
        limited = self.limit(pose.xyz, pose.capture_timestamp_ms)
        return _limited_head_position(pose, limited)

    def snapshot(self) -> PoseStepLimiterSnapshot:
        return PoseStepLimiterSnapshot(
            last_position=self._last_position,
            last_timestamp_ms=self._last_timestamp_ms,
            limited_sample_count=self._limited_sample_count,
            duplicate_or_out_of_order_count=(
                self._duplicate_or_out_of_order_count
            ),
            episode_reset_count=self._episode_reset_count,
            last_interval_ms=self._last_interval_ms,
            last_xy_limit_cm=self._last_xy_limit_cm,
            last_z_limit_cm=self._last_z_limit_cm,
        )
