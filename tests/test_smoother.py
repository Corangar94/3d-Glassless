import pytest
from tracker.smoother import KalmanFilter1D, HeadSmoother


def test_kalman_converges_to_constant():
    """Repeated measurement of the same value should converge to it."""
    kf = KalmanFilter1D(process_noise=0.01, measurement_noise=0.1)
    result = 0.0
    for _ in range(50):
        result = kf.update(10.0)
    assert abs(result - 10.0) < 0.01


def test_kalman_smooths_step_change():
    """A sudden jump should not immediately appear in output."""
    kf = KalmanFilter1D(process_noise=0.01, measurement_noise=0.1)
    for _ in range(20):
        kf.update(0.0)
    after_jump = kf.update(100.0)
    assert after_jump < 50.0  # filter dampens it


def test_head_smoother_three_axes():
    """HeadSmoother wraps three independent Kalman filters."""
    smoother = HeadSmoother(process_noise=0.01, measurement_noise=0.1)
    x, y, z = smoother.update(5.0, -3.0, 60.0)
    # Values should be between 0 and the measurement (filter moves toward it)
    assert 0.0 < x < 5.0
    assert -3.0 < y < 0.0
    assert z == 60.0   # Z seeded at 60.0, measurement is 60.0 → stays at 60.0


def test_head_smoother_axes_independent():
    """Updating one axis should not affect others."""
    smoother = HeadSmoother(process_noise=0.01, measurement_noise=0.1)
    x, y, z = 0.0, 0.0, 60.0
    for _ in range(30):
        x, y, z = smoother.update(10.0, 0.0, 60.0)
    assert abs(x - 10.0) < 0.1
    assert abs(y - 0.0) < 0.1
    assert abs(z - 60.0) < 0.1


def test_kalman_rejects_invalid_noise():
    """Zero or negative noise parameters should raise ValueError."""
    with pytest.raises(ValueError):
        KalmanFilter1D(process_noise=0.01, measurement_noise=0.0)
    with pytest.raises(ValueError):
        KalmanFilter1D(process_noise=-0.01, measurement_noise=0.1)


def test_set_measurement_noise_updates_responsiveness():
    s = HeadSmoother(process_noise=0.01, measurement_noise=0.1)
    s.set_measurement_noise(0.001)  # very responsive
    for _ in range(15):
        s.update(10.0, 0.0, 60.0)
    x, _, _ = s.update(10.0, 0.0, 60.0)
    assert x > 9.5  # after 16 frames with very low r, should be close to 10


def test_set_measurement_noise_rejects_nonpositive():
    s = HeadSmoother()
    with pytest.raises(ValueError):
        s.set_measurement_noise(0.0)
    with pytest.raises(ValueError):
        s.set_measurement_noise(-1.0)
