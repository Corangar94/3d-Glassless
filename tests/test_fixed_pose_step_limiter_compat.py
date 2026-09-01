from __future__ import annotations

import pytest

from tracker.main import TrackingLoop
from tracker.pose import HeadPosition
from tracker.pose_step_limiter import FixedPoseStepLimiter


class Tracker:
    def process_frame(self, _frame, capture_timestamp_ms=None):
        return None


class Smoother:
    def update(self, x, y, z, dt_seconds=None):
        return x, y, z

    def set_measurement_noise(self, _value):
        pass


class Writer:
    def write(self, *, x, y, z):
        pass


def test_direct_tracking_loop_uses_historical_fixed_step_limiter():
    loop = TrackingLoop(
        tracker=Tracker(),
        writer=Writer(),
        smoother=Smoother(),
    )

    assert isinstance(loop._pose_step_limiter, FixedPoseStepLimiter)


def test_fixed_limiter_does_not_gain_a_timestamp_dependency():
    limiter = FixedPoseStepLimiter()
    first = HeadPosition(
        x_cm=1.0,
        y_cm=0.0,
        z_cm=60.0,
        capture_timestamp_ms=1000,
    )
    second = HeadPosition(
        x_cm=1.5,
        y_cm=0.0,
        z_cm=65.0,
        capture_timestamp_ms=1000,
    )

    assert limiter.limit_head_position(first) is first
    assert limiter.limit_head_position(second) is second


def test_fixed_limiter_retains_historical_spike_clamp():
    limiter = FixedPoseStepLimiter()
    limiter.limit_head_position(
        HeadPosition(0.0, 0.0, 60.0, capture_timestamp_ms=1000)
    )

    output = limiter.limit_head_position(
        HeadPosition(30.0, 40.0, 160.0, capture_timestamp_ms=1000)
    )

    assert output.xyz == pytest.approx((6.0, 8.0, 72.0))
    assert output.capture_timestamp_ms == 1000


def test_fixed_limiter_reset_starts_a_new_episode():
    limiter = FixedPoseStepLimiter()
    limiter.limit_head_position(HeadPosition(0.0, 0.0, 60.0))
    limiter.reset()

    pose = HeadPosition(100.0, -100.0, 160.0)

    assert limiter.limit_head_position(pose) is pose


@pytest.mark.parametrize(
    "kwargs",
    [
        {"maximum_xy_step_cm": -1.0},
        {"maximum_z_step_cm": -1.0},
        {"maximum_xy_step_cm": float("nan")},
        {"maximum_z_step_cm": float("inf")},
    ],
)
def test_invalid_fixed_limits_fail_closed(kwargs):
    with pytest.raises(ValueError):
        FixedPoseStepLimiter(**kwargs)
