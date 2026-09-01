from __future__ import annotations

import pytest

from tracker.pose import HeadPosition
from tracker.pose_filter import AdaptivePoseFilter, normalize_angle_degrees


def _circular_error(actual: float, expected: float) -> float:
    return abs(normalize_angle_degrees(actual - expected))


def test_fast_rotation_after_long_gap_unwraps_against_projected_state():
    filter_ = AdaptivePoseFilter(
        process_noise=2.0,
        measurement_noise=0.1,
    )
    filter_.update_pose(
        HeadPosition(
            x_cm=0.0,
            y_cm=0.0,
            z_cm=60.0,
            yaw_deg=170.0,
            capture_timestamp_ms=1000,
        ),
        publish_timestamp_ms=1000,
    )

    # Model a fast established clockwise rotation. At 720 deg/s, the filter's
    # 250 ms prediction clamp places the measurement-time state at 350 degrees,
    # whose canonical representation is -10 degrees.
    filter_._yaw._state.position = 170.0
    filter_._yaw._state.velocity = 720.0
    filter_._yaw._state.timestamp_ms = 1000
    filter_._yaw._state.initialized = True

    output = filter_.update_pose(
        HeadPosition(
            x_cm=0.0,
            y_cm=0.0,
            z_cm=60.0,
            yaw_deg=-10.0,
            capture_timestamp_ms=1250,
        ),
        publish_timestamp_ms=1250,
    )

    assert _circular_error(output.yaw_deg, -10.0) < 1.0
    assert filter_._yaw.position == pytest.approx(350.0, abs=1.0)
    assert filter_._yaw.velocity > 650.0


def test_projected_reference_is_read_only_before_scalar_update():
    filter_ = AdaptivePoseFilter()
    filter_.update_pose(
        HeadPosition(
            x_cm=0.0,
            y_cm=0.0,
            z_cm=60.0,
            roll_deg=175.0,
            capture_timestamp_ms=1000,
        ),
        publish_timestamp_ms=1000,
    )
    filter_._roll._state.velocity = 40.0
    position_before = filter_._roll.position
    timestamp_before = filter_._roll.state_timestamp_ms

    projected = filter_._roll.project(1100)[0]

    assert projected > position_before
    assert filter_._roll.position == position_before
    assert filter_._roll.state_timestamp_ms == timestamp_before
