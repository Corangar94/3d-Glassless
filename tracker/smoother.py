"""Kalman filter for smoothing noisy head position measurements."""


class KalmanFilter1D:
    """Single-axis Kalman filter for smoothing noisy measurements."""

    def __init__(self, process_noise: float = 0.01, measurement_noise: float = 0.1):
        if process_noise < 0 or measurement_noise <= 0:
            raise ValueError(
                f"Noise parameters must be non-negative (q) and positive (r); "
                f"got q={process_noise}, r={measurement_noise}"
            )
        self._q = process_noise      # process noise covariance
        self._r = measurement_noise  # measurement noise covariance
        self._x = 0.0                # state estimate
        self._p = 1.0                # error covariance

    def update(self, measurement: float, dt_seconds: float | None = None) -> float:
        """Update the filter with a new measurement and return the smoothed estimate.

        Args:
            measurement: The raw measurement value for this axis.

        Returns:
            The smoothed state estimate.
        """
        # Scale process uncertainty by elapsed time so smoothing does not change
        # when the camera switches between (for example) 15 and 60 FPS. Calls
        # without dt retain the historical one-sample behaviour.
        dt_scale = 1.0 if dt_seconds is None else max(0.05, min(10.0, dt_seconds * 30.0))
        self._p += self._q * dt_scale
        # Update step
        k = self._p / (self._p + self._r)   # Kalman gain
        self._x += k * (measurement - self._x)
        self._p *= (1.0 - k)
        return self._x

    def reset(self, value: float = 0.0) -> None:
        """Reset the filter to a known state.

        Args:
            value: The initial state value (default 0.0).
        """
        self._x = value
        self._p = 1.0

    def set_measurement_noise(self, r: float) -> None:
        """Update measurement noise covariance in-place."""
        if r <= 0:
            raise ValueError(f"measurement_noise must be positive, got {r}")
        self._r = r


class HeadSmoother:
    """Three independent Kalman filters for X, Y, Z head position axes."""

    def __init__(self, process_noise: float = 0.01, measurement_noise: float = 0.1):
        self._kf_x = KalmanFilter1D(process_noise, measurement_noise)
        self._kf_y = KalmanFilter1D(process_noise, measurement_noise)
        self._kf_z = KalmanFilter1D(process_noise, measurement_noise)
        self._kf_z.reset(60.0)  # seed Z at nominal head distance

    def update(
        self,
        x: float,
        y: float,
        z: float,
        dt_seconds: float | None = None,
    ) -> tuple[float, float, float]:
        """Update all three axes with new measurements.

        Args:
            x: Raw X-axis measurement (cm).
            y: Raw Y-axis measurement (cm).
            z: Raw Z-axis measurement (cm).

        Returns:
            A tuple of (smoothed_x, smoothed_y, smoothed_z).
        """
        return (
            self._kf_x.update(x, dt_seconds),
            self._kf_y.update(y, dt_seconds),
            self._kf_z.update(z, dt_seconds),
        )

    def reset(self) -> None:
        """Reset all filters. Z defaults to 60.0 cm (nominal head distance)."""
        self._kf_x.reset()
        self._kf_y.reset()
        self._kf_z.reset(60.0)

    def set_measurement_noise(self, r: float) -> None:
        """Update measurement noise on all three axes (higher r = more smoothing)."""
        self._kf_x.set_measurement_noise(r)
        self._kf_y.set_measurement_noise(r)
        self._kf_z.set_measurement_noise(r)
