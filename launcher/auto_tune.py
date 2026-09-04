"""Adaptive tracking parameters derived from live head motion."""
from __future__ import annotations

from dataclasses import dataclass
import math
import numbers


_NOMINAL_UPDATE_RATE_HZ = 30.0
_SPEED_EMA_ALPHA_AT_NOMINAL_RATE = 0.25
_DISTANCE_EMA_ALPHA_AT_NOMINAL_RATE = 0.12
_DEFAULT_HEAD_DISTANCE_CM = 60.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _elapsed_alpha(base_alpha: float, dt_seconds: float) -> float:
    """Convert a per-30-Hz EMA coefficient to an elapsed-time coefficient."""
    if dt_seconds <= 0.0:
        return 0.0
    equivalent_updates = dt_seconds * _NOMINAL_UPDATE_RATE_HZ
    return 1.0 - math.pow(1.0 - base_alpha, equivalent_updates)


@dataclass(frozen=True)
class AutoTuneResult:
    head_dist_cm: float
    smoothing_alpha: float
    deadzone_mm: float
    speed_cm_s: float


class TrackingAutoTuner:
    """Blend stability and responsiveness from measured head velocity.

    Kalman measurement noise is raised while still (more smoothing) and
    lowered during deliberate motion. The XY dead-zone follows the same rule
    and scales gently with viewing distance.

    Motion and distance EMAs are elapsed-time based, so the tuning response is
    consistent across camera/UI callback rates. A long update gap starts a new
    episode instead of comparing a reacquired viewer with stale pre-gap state.
    """

    def __init__(
        self,
        *,
        reset_after_s: float = 0.5,
        maximum_speed_cm_s: float = 300.0,
    ) -> None:
        reset_after = _finite_number(reset_after_s)
        maximum_speed = _finite_number(maximum_speed_cm_s)
        if reset_after is None or reset_after <= 0.0:
            raise ValueError("reset_after_s must be finite and positive")
        if maximum_speed is None or maximum_speed <= 0.0:
            raise ValueError(
                "maximum_speed_cm_s must be finite and positive"
            )
        self._reset_after_s = reset_after
        self._maximum_speed_cm_s = maximum_speed
        self._last_position: tuple[float, float, float] | None = None
        self._last_time: float | None = None
        self._speed_ema = 0.0
        self._distance_ema: float | None = None
        self._rejected_sample_count = 0
        self._episode_reset_count = 0

    @property
    def reset_after_s(self) -> float:
        return self._reset_after_s

    @property
    def maximum_speed_cm_s(self) -> float:
        return self._maximum_speed_cm_s

    @property
    def rejected_sample_count(self) -> int:
        return self._rejected_sample_count

    @property
    def episode_reset_count(self) -> int:
        return self._episode_reset_count

    def reset(self) -> None:
        """Clear motion/viewing-distance history for a new tracking session."""
        self._last_position = None
        self._last_time = None
        self._speed_ema = 0.0
        self._distance_ema = None

    def _start_episode(
        self,
        x_cm: float,
        y_cm: float,
        z_cm: float,
        timestamp_s: float,
        *,
        count_reset: bool,
    ) -> AutoTuneResult:
        self._last_position = (x_cm, y_cm, z_cm)
        self._last_time = timestamp_s
        self._speed_ema = 0.0
        self._distance_ema = z_cm
        if count_reset:
            self._episode_reset_count += 1
        return self._result()

    def _result(self) -> AutoTuneResult:
        distance = (
            _DEFAULT_HEAD_DISTANCE_CM
            if self._distance_ema is None
            else self._distance_ema
        )
        speed = _clamp(self._speed_ema, 0.0, self._maximum_speed_cm_s)
        # Full stability below 1 cm/s; full responsiveness above 20 cm/s.
        motion = _clamp((speed - 1.0) / 19.0, 0.0, 1.0)
        smoothing = 0.28 + motion * (0.06 - 0.28)
        still_deadzone = _clamp(0.6 + 0.04 * distance, 1.5, 5.0)
        deadzone = still_deadzone + motion * (0.5 - still_deadzone)
        return AutoTuneResult(
            head_dist_cm=distance,
            smoothing_alpha=smoothing,
            deadzone_mm=deadzone,
            speed_cm_s=speed,
        )

    def update(
        self,
        x_cm: float,
        y_cm: float,
        z_cm: float,
        timestamp_s: float,
    ) -> AutoTuneResult:
        x = _finite_number(x_cm)
        y = _finite_number(y_cm)
        z = _finite_number(z_cm)
        timestamp = _finite_number(timestamp_s)
        if (
            x is None
            or y is None
            or z is None
            or timestamp is None
            or timestamp < 0.0
        ):
            self._rejected_sample_count += 1
            return self._result()

        z = _clamp(z, 20.0, 200.0)
        previous_position = self._last_position
        previous_time = self._last_time
        if previous_position is None or previous_time is None:
            return self._start_episode(
                x,
                y,
                z,
                timestamp,
                count_reset=False,
            )

        dt = timestamp - previous_time
        if dt <= 0.0:
            # A duplicate or out-of-order callback cannot represent new motion.
            self._rejected_sample_count += 1
            return self._result()
        if dt >= self._reset_after_s:
            return self._start_episode(
                x,
                y,
                z,
                timestamp,
                count_reset=True,
            )

        distance_alpha = _elapsed_alpha(
            _DISTANCE_EMA_ALPHA_AT_NOMINAL_RATE,
            dt,
        )
        assert self._distance_ema is not None
        self._distance_ema += distance_alpha * (z - self._distance_ema)

        dx = x - previous_position[0]
        dy = y - previous_position[1]
        dz = z - previous_position[2]
        instantaneous_speed = min(
            self._maximum_speed_cm_s,
            math.hypot(dx, dy, dz) / dt,
        )
        speed_alpha = _elapsed_alpha(
            _SPEED_EMA_ALPHA_AT_NOMINAL_RATE,
            dt,
        )
        self._speed_ema += speed_alpha * (
            instantaneous_speed - self._speed_ema
        )
        self._speed_ema = _clamp(
            self._speed_ema,
            0.0,
            self._maximum_speed_cm_s,
        )
        self._last_position = (x, y, z)
        self._last_time = timestamp
        return self._result()
