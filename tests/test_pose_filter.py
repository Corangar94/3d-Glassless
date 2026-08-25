import math

import pytest

from tracker.pose import HeadPosition
from tracker.pose_filter import (
    AdaptivePoseFilter,
    ConstantVelocityFilter1D,
    _timestamp_delta_seconds,
)
from tracker.pose_shared_memory import POSE_V2_SIZE, POSE_V2_VERSION, PoseFlags


def test_pose_v2_layout_is_fixed_and_versioned():
    assert POSE_V2_SIZE == 64
    assert POSE_V2_VERSION == 2
    assert PoseFlags.VALID & PoseFlags.PREDICTED == 0


def test_timestamp_delta_handles_uint32_wrap():
    assert _timestamp_delta_seconds(5, 0xFFFF_FFFB) == pytest.approx(0.010)
    assert _timestamp_delta_seconds(100, 200) == 0.0


def test_constant_velocity_filter_estimates_motion_and_predicts():
    axis = ConstantVelocityFilter1D(
        process_noise=20.0,
        measurement_noise=0.01,
        max_velocity=100.0,
    )
    axis.update(0.0, 1000)
    for index in range(1, 8):
        axis.update(index * 0.1, 1000 + index * 10)

    position, velocity = axis.predict(1090)

    assert position > 0.70
    assert 2.0 < velocity <= 100.0


def test_low_confidence_measurement_moves_filter_less():
    confident = ConstantVelocityFilter1D(8.0, 0.1)
    uncertain = ConstantVelocityFilter1D(8.0, 0.1)
    confident.update(0.0, 1000)
    uncertain.update(0.0, 1000)

    confident_position, _ = confident.update(10.0, 1033, confidence=1.0)
    uncertain_position, _ = uncertain.update(10.0, 1033, confidence=0.1)

    assert confident_position > uncertain_position


def test_adaptive_pose_filter_publishes_velocity_orientation_and_prediction():
    filter_ = AdaptivePoseFilter(
        process_noise=0.02,
        measurement_noise=0.05,
        prediction_horizon_ms=30.0,
        max_prediction_ms=80.0,
    )
    filter_.update_pose(
        HeadPosition(
            x_cm=0.0,
            y_cm=0.0,
            z_cm=60.0,
            yaw_deg=5.0,
            confidence=0.9,
            capture_timestamp_ms=1000,
        ),
        publish_timestamp_ms=1000,
    )
    output = filter_.update_pose(
        HeadPosition(
            x_cm=1.0,
            y_cm=-0.5,
            z_cm=59.5,
            yaw_deg=8.0,
            pitch_deg=2.0,
            roll_deg=-1.0,
            confidence=0.9,
            capture_timestamp_ms=1033,
        ),
        publish_timestamp_ms=1043,
    )

    assert output.predicted
    assert output.capture_timestamp_ms == 1033
    assert output.publish_timestamp_ms == 1043
    assert output.confidence > 0.5
    assert output.x_cm > 0.0
    assert math.isfinite(output.vx_cm_s)
    assert output.yaw_deg > 5.0


def test_prediction_is_bounded_after_a_long_stall():
    filter_ = AdaptivePoseFilter(
        prediction_horizon_ms=35.0,
        max_prediction_ms=50.0,
    )
    filter_.update_pose(
        HeadPosition(
            x_cm=0.0,
            y_cm=0.0,
            z_cm=60.0,
            capture_timestamp_ms=1000,
        ),
        publish_timestamp_ms=1000,
    )
    filter_.update_pose(
        HeadPosition(
            x_cm=1.0,
            y_cm=0.0,
            z_cm=60.0,
            capture_timestamp_ms=1010,
        ),
        publish_timestamp_ms=1010,
    )

    output = filter_.predict(publish_timestamp_ms=2000)

    assert abs(output.x_cm) < 20.0
    assert output.confidence < 0.2
