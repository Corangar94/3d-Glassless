"""Adaptive tracking parameters derived from live head motion."""
from __future__ import annotations

from dataclasses import dataclass


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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
    """

    def __init__(self) -> None:
        self._last_position: tuple[float, float, float] | None = None
        self._last_time: float | None = None
        self._speed_ema = 0.0
        self._distance_ema: float | None = None

    def update(self, x_cm: float, y_cm: float, z_cm: float, timestamp_s: float) -> AutoTuneResult:
        z_cm = _clamp(float(z_cm), 20.0, 200.0)
        if self._distance_ema is None:
            self._distance_ema = z_cm
        else:
            self._distance_ema += 0.12 * (z_cm - self._distance_ema)

        speed = 0.0
        if self._last_position is not None and self._last_time is not None:
            dt = _clamp(timestamp_s - self._last_time, 0.02, 0.5)
            dx = x_cm - self._last_position[0]
            dy = y_cm - self._last_position[1]
            dz = z_cm - self._last_position[2]
            speed = (dx * dx + dy * dy + dz * dz) ** 0.5 / dt
        self._last_position = (x_cm, y_cm, z_cm)
        self._last_time = timestamp_s
        self._speed_ema += 0.25 * (speed - self._speed_ema)

        # Full stability below 1 cm/s; full responsiveness above 20 cm/s.
        motion = _clamp((self._speed_ema - 1.0) / 19.0, 0.0, 1.0)
        smoothing = 0.28 + motion * (0.06 - 0.28)
        still_deadzone = _clamp(0.6 + 0.04 * self._distance_ema, 1.5, 5.0)
        deadzone = still_deadzone + motion * (0.5 - still_deadzone)

        return AutoTuneResult(
            head_dist_cm=self._distance_ema,
            smoothing_alpha=smoothing,
            deadzone_mm=deadzone,
            speed_cm_s=self._speed_ema,
        )
