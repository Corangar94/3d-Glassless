"""Timestamp-aware constant-velocity head-pose filtering and prediction."""
from __future__ import annotations

from dataclasses import dataclass
import math

from tracker.pose import FilteredPose, HeadPosition, monotonic_ms

_UINT32 = 0xFFFF_FFFF


def _timestamp_delta_seconds(newer_ms: int, older_ms: int) -> float:
    delta = (int(newer_ms) - int(older_ms)) & _UINT32
    return 0.0 if delta > 0x7FFF_FFFF else delta / 1000.0


@dataclass
class _AxisState:
    position: float
    velocity: float
    p00: float
    p01: float
    p10: float
    p11: float
    timestamp_ms: int
    initialized: bool = False


class ConstantVelocityFilter1D:
    """Two-state Kalman filter with position and velocity."""

    def __init__(self, process_noise: float = 24.0, measurement_noise: float = 0.10, *, initial_value: float = 0.0, max_velocity: float = 250.0, max_acceleration: float = 1500.0) -> None:
        if process_noise < 0.0 or measurement_noise <= 0.0:
            raise ValueError("process_noise must be non-negative and measurement_noise positive")
        self._process_noise = float(process_noise)
        self._measurement_noise = float(measurement_noise)
        self._initial_value = float(initial_value)
        self._max_velocity = max(0.01, float(max_velocity))
        self._max_acceleration = max(0.01, float(max_acceleration))
        self._state = _AxisState(self._initial_value, 0.0, 4.0, 0.0, 0.0, 25.0, 0, False)

    def reset(self, value: float | None = None, timestamp_ms: int = 0) -> None:
        self._state = _AxisState(self._initial_value if value is None else float(value), 0.0, 4.0, 0.0, 0.0, 25.0, int(timestamp_ms) & _UINT32, False)

    def set_measurement_noise(self, value: float) -> None:
        if value <= 0.0 or not math.isfinite(value):
            raise ValueError(f"measurement_noise must be finite and positive, got {value}")
        self._measurement_noise = float(value)

    def _predict_in_place(self, timestamp_ms: int) -> None:
        state = self._state
        if not state.initialized:
            state.timestamp_ms = int(timestamp_ms) & _UINT32
            return
        dt = _timestamp_delta_seconds(timestamp_ms, state.timestamp_ms)
        if dt <= 0.0:
            return
        dt = min(dt, 0.25)
        q = min(self._process_noise, self._max_acceleration**2)
        dt2, dt3, dt4 = dt * dt, dt * dt * dt, dt * dt * dt * dt
        q00, q01, q11 = 0.25 * dt4 * q, 0.5 * dt3 * q, dt2 * q
        state.position += state.velocity * dt
        p00 = state.p00 + dt * (state.p10 + state.p01) + dt2 * state.p11 + q00
        p01 = state.p01 + dt * state.p11 + q01
        p10 = state.p10 + dt * state.p11 + q01
        p11 = state.p11 + q11
        state.p00, state.p01, state.p10, state.p11 = p00, p01, p10, p11
        state.timestamp_ms = int(timestamp_ms) & _UINT32

    def update(self, measurement: float, timestamp_ms: int, *, confidence: float = 1.0) -> tuple[float, float]:
        if not math.isfinite(measurement):
            return self.predict(timestamp_ms)
        confidence = min(1.0, max(0.05, float(confidence)))
        state = self._state
        if not state.initialized:
            state.position, state.velocity = float(measurement), 0.0
            state.timestamp_ms, state.initialized = int(timestamp_ms) & _UINT32, True
            return state.position, state.velocity
        previous_velocity, previous_timestamp = state.velocity, state.timestamp_ms
        self._predict_in_place(timestamp_ms)
        dt = max(1e-3, _timestamp_delta_seconds(timestamp_ms, previous_timestamp))
        r = self._measurement_noise / (confidence * confidence)
        innovation = float(measurement) - state.position
        innovation_covariance = state.p00 + r
        if innovation_covariance <= 1e-9:
            return state.position, state.velocity
        k0, k1 = state.p00 / innovation_covariance, state.p10 / innovation_covariance
        state.position += k0 * innovation
        state.velocity += k1 * innovation
        state.velocity = max(-self._max_velocity, min(self._max_velocity, state.velocity))
        max_dv = self._max_acceleration * dt
        state.velocity = max(previous_velocity - max_dv, min(previous_velocity + max_dv, state.velocity))
        p00, p01, p10, p11 = state.p00, state.p01, state.p10, state.p11
        state.p00, state.p01 = (1.0 - k0) * p00, (1.0 - k0) * p01
        state.p10, state.p11 = p10 - k1 * p00, p11 - k1 * p01
        return state.position, state.velocity

    def predict(self, timestamp_ms: int) -> tuple[float, float]:
        self._predict_in_place(timestamp_ms)
        return self._state.position, self._state.velocity

    @property
    def initialized(self) -> bool:
        return self._state.initialized


class AdaptivePoseFilter:
    """Filter translation/orientation once and predict to display time."""

    def __init__(self, process_noise: float = 0.01, measurement_noise: float = 0.1, *, prediction_horizon_ms: float = 35.0, max_prediction_ms: float = 80.0) -> None:
        acceleration_variance = max(1.0, float(process_noise) * 2400.0)
        self._x = ConstantVelocityFilter1D(acceleration_variance, measurement_noise, max_velocity=180.0)
        self._y = ConstantVelocityFilter1D(acceleration_variance, measurement_noise, max_velocity=180.0)
        self._z = ConstantVelocityFilter1D(acceleration_variance * 1.5, measurement_noise * 1.5, initial_value=60.0, max_velocity=240.0)
        orientation_q, orientation_r = max(4.0, acceleration_variance * 0.5), max(0.2, measurement_noise * 4.0)
        self._yaw = ConstantVelocityFilter1D(orientation_q, orientation_r, max_velocity=720.0)
        self._pitch = ConstantVelocityFilter1D(orientation_q, orientation_r, max_velocity=720.0)
        self._roll = ConstantVelocityFilter1D(orientation_q, orientation_r, max_velocity=720.0)
        self._prediction_horizon_ms = max(0.0, float(prediction_horizon_ms))
        self._max_prediction_ms = max(self._prediction_horizon_ms, float(max_prediction_ms))
        self._last_confidence = 0.0
        self._last_capture_timestamp_ms = 0
        self._synthetic_timestamp_ms = monotonic_ms()

    def set_measurement_noise(self, value: float) -> None:
        value = max(1e-6, float(value))
        self._x.set_measurement_noise(value)
        self._y.set_measurement_noise(value)
        self._z.set_measurement_noise(value * 1.5)
        for axis in (self._yaw, self._pitch, self._roll):
            axis.set_measurement_noise(max(0.2, value * 4.0))

    def reset(self) -> None:
        for axis in (self._x, self._y, self._yaw, self._pitch, self._roll):
            axis.reset(0.0)
        self._z.reset(60.0)
        self._last_confidence = 0.0
        self._last_capture_timestamp_ms = 0

    def _prediction_timestamp(self, capture_ms: int, publish_ms: int) -> int:
        age_ms = _timestamp_delta_seconds(publish_ms, capture_ms) * 1000.0
        ahead_ms = min(self._max_prediction_ms, max(0.0, age_ms) + self._prediction_horizon_ms)
        return (int(capture_ms) + int(round(ahead_ms))) & _UINT32

    def update_pose(self, pose: HeadPosition, *, publish_timestamp_ms: int | None = None) -> FilteredPose:
        capture_ms = int(pose.capture_timestamp_ms) & _UINT32 if pose.capture_timestamp_ms else monotonic_ms()
        publish_ms = monotonic_ms() if publish_timestamp_ms is None else int(publish_timestamp_ms)
        confidence = min(1.0, max(0.05, float(pose.confidence)))
        self._x.update(pose.x_cm, capture_ms, confidence=confidence)
        self._y.update(pose.y_cm, capture_ms, confidence=confidence)
        self._z.update(pose.z_cm, capture_ms, confidence=confidence)
        self._yaw.update(pose.yaw_deg, capture_ms, confidence=confidence)
        self._pitch.update(pose.pitch_deg, capture_ms, confidence=confidence)
        self._roll.update(pose.roll_deg, capture_ms, confidence=confidence)
        self._last_confidence, self._last_capture_timestamp_ms = confidence, capture_ms
        return self.predict(publish_timestamp_ms=publish_ms)

    def predict(self, *, publish_timestamp_ms: int | None = None) -> FilteredPose:
        publish_ms = monotonic_ms() if publish_timestamp_ms is None else int(publish_timestamp_ms)
        if not self._x.initialized:
            return FilteredPose(x_cm=0.0, y_cm=0.0, z_cm=60.0, publish_timestamp_ms=publish_ms & _UINT32)
        target_ms = self._prediction_timestamp(self._last_capture_timestamp_ms, publish_ms)
        x, vx = self._x.predict(target_ms)
        y, vy = self._y.predict(target_ms)
        z, vz = self._z.predict(target_ms)
        yaw, _ = self._yaw.predict(target_ms)
        pitch, _ = self._pitch.predict(target_ms)
        roll, _ = self._roll.predict(target_ms)
        age_ms = _timestamp_delta_seconds(publish_ms, self._last_capture_timestamp_ms) * 1000.0
        confidence = self._last_confidence * math.exp(-max(0.0, age_ms - 50.0) / 350.0)
        return FilteredPose(x_cm=x, y_cm=y, z_cm=max(1.0, z), vx_cm_s=vx, vy_cm_s=vy, vz_cm_s=vz, yaw_deg=yaw, pitch_deg=pitch, roll_deg=roll, confidence=min(1.0, max(0.0, confidence)), capture_timestamp_ms=self._last_capture_timestamp_ms, publish_timestamp_ms=publish_ms & _UINT32, predicted=target_ms != self._last_capture_timestamp_ms)

    def update(self, x: float, y: float, z: float, dt_seconds: float | None = None) -> tuple[float, float, float]:
        self._synthetic_timestamp_ms = monotonic_ms() if dt_seconds is None else (self._synthetic_timestamp_ms + max(1, int(dt_seconds * 1000.0))) & _UINT32
        return self.update_pose(HeadPosition(x_cm=x, y_cm=y, z_cm=z, capture_timestamp_ms=self._synthetic_timestamp_ms), publish_timestamp_ms=self._synthetic_timestamp_ms).xyz
