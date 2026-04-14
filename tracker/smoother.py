"""Kalman filter for smoothing noisy head position measurements."""


class KalmanFilter1D:
    """Single-axis Kalman filter for smoothing noisy measurements."""

    def __init__(self, process_noise: float = 0.01, measurement_noise: float = 0.1):
        self._q = process_noise      # process noise covariance
        self._r = measurement_noise  # measurement noise covariance
        self._x = 0.0                # state estimate
        self._p = 1.0                # error covariance

    def update(self, measurement: float) -> float:
        """Update the filter with a new measurement and return the smoothed estimate.

        Args:
            measurement: The raw measurement value for this axis.

        Returns:
            The smoothed state estimate.
        """
        # Prediction step
        self._p += self._q
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


class HeadSmoother:
    """Three independent Kalman filters for X, Y, Z head position axes."""

    def __init__(self, process_noise: float = 0.01, measurement_noise: float = 0.1):
        self._kf_x = KalmanFilter1D(process_noise, measurement_noise)
        self._kf_y = KalmanFilter1D(process_noise, measurement_noise)
        self._kf_z = KalmanFilter1D(process_noise, measurement_noise)

    def update(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        """Update all three axes with new measurements.

        Args:
            x: Raw X-axis measurement (cm).
            y: Raw Y-axis measurement (cm).
            z: Raw Z-axis measurement (cm).

        Returns:
            A tuple of (smoothed_x, smoothed_y, smoothed_z).
        """
        return (
            self._kf_x.update(x),
            self._kf_y.update(y),
            self._kf_z.update(z),
        )

    def reset(self) -> None:
        """Reset all filters. Z defaults to 60.0 cm (nominal head distance)."""
        self._kf_x.reset()
        self._kf_y.reset()
        self._kf_z.reset(60.0)
