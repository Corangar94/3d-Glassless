import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tracker'))
from smoother import KalmanFilter1D, HeadSmoother

def test_kalman_converges_to_constant():
    """Repeated measurement of the same value should converge to it."""
    kf = KalmanFilter1D(process_noise=0.01, measurement_noise=0.1)
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
    assert isinstance(x, float)
    assert isinstance(y, float)
    assert isinstance(z, float)

def test_head_smoother_axes_independent():
    """Updating one axis should not affect others."""
    smoother = HeadSmoother(process_noise=0.01, measurement_noise=0.1)
    for _ in range(30):
        x, y, z = smoother.update(10.0, 0.0, 60.0)
    assert abs(x - 10.0) < 0.1
    assert abs(y - 0.0) < 0.1
