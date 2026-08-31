"""Timestamp-aware constant-velocity head-pose filtering and prediction."""
from __future__ import annotations

from dataclasses import dataclass
import math

from tracker.backend_transition_state import (
    current_backend_transition_generation,
)
from tracker.pose import (
    FilteredPose,
    HeadPosition,
    monotonic_ms,
    normalize_wire_timestamp,
)

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

    def __init__(
        self,
        process_noise: float = 4800.0,
        measurement_noise: float = 0.10,
        *,
        initial_value: float = 0.0,
        max_velocity: float = 250.0,
        max_acceleration: float = 1500.0,
    ) -> None:
        if process_noise < 0.0 or measurement_noise <= 0.0:
            raise ValueError(
                "process_noise must be non-negative and measurement_noise positive"
            )
        self._process_noise = float(process_noise)
        self._measurement_noise = float(measurement_noise)
        self._initial_value = float(initial_value)
        self._max_velocity = max(0.01, float(max_velocity))
        self._max_acceleration = max(0.01, float(max_acceleration))
        self._state = _AxisState(
            self._initial_value,
            0.0,
            4.0,
            0.0,
            0.0,
            25.0,
            0,
            False,
        )

    def reset(self, value: float | None = None, timestamp_ms: int = 0) -> None:
        self._state = _AxisState(
            self._initial_value if value is None else float(value),
            0.0,
            4.0,
            0.0,
            0.0,
            25.0,
            int(timestamp_ms) & _UINT32,
            False,
        )

    def set_measurement_noise(self, value: float) -> None:
        if value <= 0.0 or not math.isfinite(value):
            raise ValueError(
                f"measurement_noise must be finite and positive, got {value}"
            )
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
        dt2 = dt * dt
        dt3 = dt2 * dt
        dt4 = dt2 * dt2
        q00 = 0.25 * dt4 * q
        q01 = 0.5 * dt3 * q
        q11 = dt2 * q
        state.position += state.velocity * dt
        p00 = state.p00 + dt * (state.p10 + state.p01) + dt2 * state.p11 + q00
        p01 = state.p01 + dt * state.p11 + q01
        p10 = state.p10 + dt * state.p11 + q01
        p11 = state.p11 + q11
        state.p00, state.p01, state.p10, state.p11 = p00, p01, p10, p11
        state.timestamp_ms = int(timestamp_ms) & _UINT32

    def update(
        self,
        measurement: float,
        timestamp_ms: int,
        *,
        confidence: float = 1.0,
    ) -> tuple[float, float]:
        if not math.isfinite(measurement):
            return self.project(timestamp_ms)
        confidence = min(1.0, max(0.05, float(confidence)))
        state = self._state
        if not state.initialized:
            state.position = float(measurement)
            state.velocity = 0.0
            state.timestamp_ms = int(timestamp_ms) & _UINT32
            state.initialized = True
            return state.position, state.velocity

        previous_velocity = state.velocity
        previous_timestamp = state.timestamp_ms
        self._predict_in_place(timestamp_ms)
        dt = max(1e-3, _timestamp_delta_seconds(timestamp_ms, previous_timestamp))
        measurement_variance = self._measurement_noise / (confidence * confidence)
        innovation = float(measurement) - state.position
        innovation_covariance = state.p00 + measurement_variance
        if innovation_covariance <= 1e-9:
            return state.position, state.velocity
        k0 = state.p00 / innovation_covariance
        k1 = state.p10 / innovation_covariance
        state.position += k0 * innovation
        state.velocity += k1 * innovation
        state.velocity = max(
            -self._max_velocity,
            min(self._max_velocity, state.velocity),
        )
        max_velocity_change = self._max_acceleration * dt
        state.velocity = max(
            previous_velocity - max_velocity_change,
            min(previous_velocity + max_velocity_change, state.velocity),
        )
        p00, p01, p10, p11 = state.p00, state.p01, state.p10, state.p11
        state.p00 = (1.0 - k0) * p00
        state.p01 = (1.0 - k0) * p01
        state.p10 = p10 - k1 * p00
        state.p11 = p11 - k1 * p01
        return state.position, state.velocity

    def project(self, timestamp_ms: int) -> tuple[float, float]:
        """Project without moving the camera-time Kalman state forward.

        Display prediction can run ahead of the next camera capture. Mutating
        the estimator to that display timestamp makes the following measurement
        appear out-of-order and causes overshoot. Projection therefore remains
        a read-only view of the latest measurement-time state.
        """
        state = self._state
        if not state.initialized:
            return state.position, state.velocity
        dt = min(0.25, _timestamp_delta_seconds(timestamp_ms, state.timestamp_ms))
        return state.position + state.velocity * dt, state.velocity

    def predict(self, timestamp_ms: int) -> tuple[float, float]:
        """Advance the estimator itself; retained for explicit state propagation."""
        self._predict_in_place(timestamp_ms)
        return self._state.position, self._state.velocity

    @property
    def state_timestamp_ms(self) -> int:
        return self._state.timestamp_ms

    @property
    def initialized(self) -> bool:
        return self._state.initialized


class AdaptivePoseFilter:
    """Filter translation/orientation once and predict to display time."""

    def __init__(
        self,
        process_noise: float = 2.0,
        measurement_noise: float = 0.1,
        *,
        prediction_horizon_ms: float = 0.0,
        max_prediction_ms: float = 80.0,
    ) -> None:
        acceleration_variance = max(1.0, float(process_noise) * 2400.0)
        self._x = ConstantVelocityFilter1D(
            acceleration_variance,
            measurement_noise,
            max_velocity=180.0,
        )
        self._y = ConstantVelocityFilter1D(
            acceleration_variance,
            measurement_noise,
            max_velocity=180.0,
        )
        self._z = ConstantVelocityFilter1D(
            acceleration_variance * 1.5,
            measurement_noise * 1.5,
            initial_value=60.0,
            max_velocity=240.0,
        )
        orientation_q = max(4.0, acceleration_variance * 0.5)
        orientation_r = max(0.2, measurement_noise * 4.0)
        self._yaw = ConstantVelocityFilter1D(
            orientation_q,
            orientation_r,
            max_velocity=720.0,
        )
        self._pitch = ConstantVelocityFilter1D(
            orientation_q,
            orientation_r,
            max_velocity=720.0,
        )
        self._roll = ConstantVelocityFilter1D(
            orientation_q,
            orientation_r,
            max_velocity=720.0,
        )
        self._prediction_horizon_ms = max(0.0, float(prediction_horizon_ms))
        self._max_prediction_ms = max(
            self._prediction_horizon_ms,
            float(max_prediction_ms),
        )
        self._last_confidence = 0.0
        self._last_capture_timestamp_ms = 0
        self._synthetic_timestamp_ms = monotonic_ms()
        self._backend_transition_generation = (
            current_backend_transition_generation()
        )

    def set_measurement_noise(self, value: float) -> None:
        value = max(1e-6, float(value))
        self._x.set_measurement_noise(value)
        self._y.set_measurement_noise(value)
        self._z.set_measurement_noise(value * 1.5)
        for axis in (self._yaw, self._pitch, self._roll):
            axis.set_measurement_noise(max(0.2, value * 4.0))

    def _reset_state(self) -> None:
        for axis in (
            self._x,
            self._y,
            self._yaw,
            self._pitch,
            self._roll,
        ):
            axis.reset(0.0)
        self._z.reset(60.0)
        self._last_confidence = 0.0
        self._last_capture_timestamp_ms = 0

    def reset(self) -> None:
        self._reset_state()
        self._backend_transition_generation = (
            current_backend_transition_generation()
        )

    def _synchronize_backend_transition(self) -> None:
        generation = current_backend_transition_generation()
        if generation == self._backend_transition_generation:
            return
        # Backend-specific velocity and covariance are not transferable. Reset
        # before accepting or predicting the next pose from the new source.
        self._reset_state()
        self._backend_transition_generation = generation

    def _prediction_timestamp(self, capture_ms: int, publish_ms: int) -> int:
        measurement_age_ms = (
            _timestamp_delta_seconds(publish_ms, capture_ms) * 1000.0
        )
        ahead_ms = min(
            self._max_prediction_ms,
            max(0.0, measurement_age_ms) + self._prediction_horizon_ms,
        )
        return normalize_wire_timestamp(
            int(capture_ms) + int(round(ahead_ms))
        )

    def update_pose(
        self,
        pose: HeadPosition,
        *,
        publish_timestamp_ms: int | None = None,
    ) -> FilteredPose:
        self._synchronize_backend_transition()
        capture_ms = (
            normalize_wire_timestamp(pose.capture_timestamp_ms)
            if pose.capture_timestamp_ms
            else monotonic_ms()
        )
        publish_ms = (
            monotonic_ms()
            if publish_timestamp_ms is None
            else normalize_wire_timestamp(publish_timestamp_ms)
        )
        confidence = min(1.0, max(0.05, float(pose.confidence)))
        self._x.update(pose.x_cm, capture_ms, confidence=confidence)
        self._y.update(pose.y_cm, capture_ms, confidence=confidence)
        self._z.update(pose.z_cm, capture_ms, confidence=confidence)
        self._yaw.update(pose.yaw_deg, capture_ms, confidence=confidence)
        self._pitch.update(pose.pitch_deg, capture_ms, confidence=confidence)
        self._roll.update(pose.roll_deg, capture_ms, confidence=confidence)
        self._last_confidence = confidence
        self._last_capture_timestamp_ms = capture_ms
        return self.predict(publish_timestamp_ms=publish_ms)

    def predict(
        self,
        *,
        publish_timestamp_ms: int | None = None,
    ) -> FilteredPose:
        self._synchronize_backend_transition()
        publish_ms = (
            monotonic_ms()
            if publish_timestamp_ms is None
            else normalize_wire_timestamp(publish_timestamp_ms)
        )
        if not self._x.initialized:
            return FilteredPose(
                x_cm=0.0,
                y_cm=0.0,
                z_cm=60.0,
                publish_timestamp_ms=publish_ms,
                prediction_target_timestamp_ms=publish_ms,
            )
        target_ms = self._prediction_timestamp(
            self._last_capture_timestamp_ms,
            publish_ms,
        )
        x, vx = self._x.project(target_ms)
        y, vy = self._y.project(target_ms)
        z, vz = self._z.project(target_ms)
        yaw, _ = self._yaw.project(target_ms)
        pitch, _ = self._pitch.project(target_ms)
        roll, _ = self._roll.project(target_ms)
        measurement_age_ms = (
            _timestamp_delta_seconds(
                publish_ms,
                self._last_capture_timestamp_ms,
            )
            * 1000.0
        )
        confidence = self._last_confidence * math.exp(
            -max(0.0, measurement_age_ms - 50.0) / 350.0
        )
        return FilteredPose(
            x_cm=x,
            y_cm=y,
            z_cm=max(1.0, z),
            vx_cm_s=vx,
            vy_cm_s=vy,
            vz_cm_s=vz,
            yaw_deg=yaw,
            pitch_deg=pitch,
            roll_deg=roll,
            confidence=min(1.0, max(0.0, confidence)),
            capture_timestamp_ms=self._last_capture_timestamp_ms,
            publish_timestamp_ms=publish_ms,
            prediction_target_timestamp_ms=target_ms,
            predicted=target_ms != self._last_capture_timestamp_ms,
        )

    def update(
        self,
        x: float,
        y: float,
        z: float,
        dt_seconds: float | None = None,
    ) -> tuple[float, float, float]:
        self._synthetic_timestamp_ms = (
            monotonic_ms()
            if dt_seconds is None
            else normalize_wire_timestamp(
                self._synthetic_timestamp_ms
                + max(1, int(dt_seconds * 1000.0))
            )
        )
        return self.update_pose(
            HeadPosition(
                x_cm=x,
                y_cm=y,
                z_cm=z,
                capture_timestamp_ms=self._synthetic_timestamp_ms,
            ),
            publish_timestamp_ms=self._synthetic_timestamp_ms,
        ).xyz
