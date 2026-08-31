from __future__ import annotations

import math

import pytest

from tracker.backend_transition_state import (
    mark_backend_transition,
    reset_backend_transition_generation,
)
from tracker.pose import HeadPosition
from tracker.pose_filter import (
    AdaptivePoseFilter,
    normalize_angle_degrees,
    unwrap_angle_near,
)


def _circular_delta(newer: float, older: float) -> float:
    return normalize_angle_degrees(newer - older)


def _pose(
    timestamp_ms: int,
    *,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 60.0,
    yaw: float = 0.0,
    pitch: float = 0.0,
    roll: float = 0.0,
) -> HeadPosition:
    return HeadPosition(
        x_cm=x,
        y_cm=y,
        z_cm=z,
        yaw_deg=yaw,
        pitch_deg=pitch,
        roll_deg=roll,
        confidence=1.0,
        capture_timestamp_ms=timestamp_ms,
    )


@pytest.fixture(autouse=True)
def _clean_backend_transition_state():
    reset_backend_transition_generation()
    yield
    reset_backend_transition_generation()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, 0.0),
        (180.0, -180.0),
        (-180.0, -180.0),
        (181.0, -179.0),
        (-181.0, 179.0),
        (360.0, 0.0),
        (-360.0, 0.0),
        (725.0, 5.0),
    ],
)
def test_angle_normalization_uses_canonical_interval(value, expected):
    assert normalize_angle_degrees(value) == pytest.approx(expected)


def test_nonfinite_angle_normalization_remains_fail_safe():
    assert math.isnan(normalize_angle_degrees(float("nan")))
    assert normalize_angle_degrees(float("inf")) == float("inf")


def test_wrapped_measurement_is_lifted_to_nearest_turn():
    assert unwrap_angle_near(-179.0, 179.0) == pytest.approx(181.0)
    assert unwrap_angle_near(179.0, -179.0) == pytest.approx(-181.0)
    assert unwrap_angle_near(10.0, 730.0) == pytest.approx(730.0)


def test_yaw_crosses_positive_wrap_by_two_degrees_not_358():
    filter_ = AdaptivePoseFilter(
        process_noise=2.0,
        measurement_noise=0.1,
        prediction_horizon_ms=0.0,
    )
    first = filter_.update_pose(
        _pose(1000, yaw=179.0),
        publish_timestamp_ms=1000,
    )
    second = filter_.update_pose(
        _pose(1033, yaw=-179.0),
        publish_timestamp_ms=1033,
    )

    assert abs(_circular_delta(second.yaw_deg, first.yaw_deg)) < 5.0
    assert filter_._yaw.position > 180.0
    assert 0.0 < filter_._yaw.velocity < 100.0


def test_yaw_crosses_negative_wrap_in_the_reverse_direction():
    filter_ = AdaptivePoseFilter(
        process_noise=2.0,
        measurement_noise=0.1,
    )
    first = filter_.update_pose(
        _pose(1000, yaw=-179.0),
        publish_timestamp_ms=1000,
    )
    second = filter_.update_pose(
        _pose(1033, yaw=179.0),
        publish_timestamp_ms=1033,
    )

    assert abs(_circular_delta(second.yaw_deg, first.yaw_deg)) < 5.0
    assert filter_._yaw.position < -180.0
    assert -100.0 < filter_._yaw.velocity < 0.0


def test_all_orientation_axes_take_the_shortest_path():
    filter_ = AdaptivePoseFilter()
    first = filter_.update_pose(
        _pose(1000, yaw=179.0, pitch=178.0, roll=177.0),
        publish_timestamp_ms=1000,
    )
    second = filter_.update_pose(
        _pose(1033, yaw=-179.0, pitch=-178.0, roll=-177.0),
        publish_timestamp_ms=1033,
    )

    assert abs(_circular_delta(second.yaw_deg, first.yaw_deg)) < 5.0
    assert abs(_circular_delta(second.pitch_deg, first.pitch_deg)) < 7.0
    assert abs(_circular_delta(second.roll_deg, first.roll_deg)) < 9.0
    assert filter_._yaw.position > 180.0
    assert filter_._pitch.position > 180.0
    assert filter_._roll.position > 180.0


def test_smooth_rotation_sequence_has_no_wrap_spike():
    filter_ = AdaptivePoseFilter(
        process_noise=2.0,
        measurement_noise=0.1,
        prediction_horizon_ms=20.0,
        max_prediction_ms=80.0,
    )
    measurements = [170.0, 175.0, 179.0, -179.0, -175.0, -170.0]
    outputs = []
    timestamp = 1000
    for measurement in measurements:
        outputs.append(
            filter_.update_pose(
                _pose(timestamp, yaw=measurement),
                publish_timestamp_ms=timestamp,
            ).yaw_deg
        )
        timestamp += 33

    deltas = [
        _circular_delta(current, previous)
        for previous, current in zip(outputs, outputs[1:])
    ]
    assert all(0.0 <= delta < 15.0 for delta in deltas)
    assert filter_._yaw.position > 185.0
    assert abs(filter_._yaw.velocity) < 200.0


def test_prediction_stays_canonical_and_continuous_across_wrap():
    filter_ = AdaptivePoseFilter(
        process_noise=2.0,
        measurement_noise=0.1,
        prediction_horizon_ms=40.0,
        max_prediction_ms=100.0,
    )
    filter_.update_pose(
        _pose(1000, yaw=175.0),
        publish_timestamp_ms=1000,
    )
    measured = filter_.update_pose(
        _pose(1033, yaw=179.0),
        publish_timestamp_ms=1033,
    )
    predicted = filter_.predict(publish_timestamp_ms=1099)

    assert -180.0 <= predicted.yaw_deg < 180.0
    assert abs(_circular_delta(predicted.yaw_deg, measured.yaw_deg)) < 20.0
    assert predicted.predicted


def test_wrap_filtering_does_not_change_translation_results():
    wrapped = AdaptivePoseFilter()
    neutral = AdaptivePoseFilter()
    timestamps = (1000, 1033, 1066, 1099)
    yaws = (179.0, -179.0, -176.0, -172.0)

    wrapped_outputs = []
    neutral_outputs = []
    for index, timestamp in enumerate(timestamps):
        values = dict(
            x=float(index * 2),
            y=float(-index),
            z=60.0 + index,
        )
        wrapped_outputs.append(
            wrapped.update_pose(
                _pose(timestamp, yaw=yaws[index], **values),
                publish_timestamp_ms=timestamp,
            )
        )
        neutral_outputs.append(
            neutral.update_pose(
                _pose(timestamp, yaw=0.0, **values),
                publish_timestamp_ms=timestamp,
            )
        )

    for wrapped_output, neutral_output in zip(
        wrapped_outputs,
        neutral_outputs,
    ):
        assert wrapped_output.xyz == pytest.approx(neutral_output.xyz)
        assert wrapped_output.vx_cm_s == pytest.approx(
            neutral_output.vx_cm_s
        )
        assert wrapped_output.vy_cm_s == pytest.approx(
            neutral_output.vy_cm_s
        )
        assert wrapped_output.vz_cm_s == pytest.approx(
            neutral_output.vz_cm_s
        )


def test_angle_and_uint32_timestamp_rollovers_work_together():
    filter_ = AdaptivePoseFilter()
    first_timestamp = 0xFFFF_FFF0
    second_timestamp = 0x0000_0011  # 33 ms later across uint32 rollover
    first = filter_.update_pose(
        _pose(first_timestamp, yaw=179.0),
        publish_timestamp_ms=first_timestamp,
    )
    second = filter_.update_pose(
        _pose(second_timestamp, yaw=-179.0),
        publish_timestamp_ms=second_timestamp,
    )

    assert abs(_circular_delta(second.yaw_deg, first.yaw_deg)) < 5.0
    assert filter_._yaw.position > 180.0
    assert filter_._yaw.state_timestamp_ms == second_timestamp


def test_recent_backend_transition_preserves_orientation_but_quenches_velocity():
    filter_ = AdaptivePoseFilter()
    filter_.update_pose(
        _pose(1000, yaw=170.0),
        publish_timestamp_ms=1000,
    )
    before = filter_.update_pose(
        _pose(1033, yaw=175.0),
        publish_timestamp_ms=1033,
    )
    assert filter_._yaw.velocity > 0.0
    mark_backend_transition(preserve_position=True)

    after = filter_.update_pose(
        _pose(1066, yaw=179.0),
        publish_timestamp_ms=1066,
    )

    assert abs(_circular_delta(after.yaw_deg, before.yaw_deg)) < 10.0
    assert abs(filter_._yaw.velocity) < 1.0


def test_stale_backend_transition_does_not_carry_old_orientation_turn():
    filter_ = AdaptivePoseFilter()
    filter_.update_pose(
        _pose(1000, yaw=179.0),
        publish_timestamp_ms=1000,
    )
    filter_.update_pose(
        _pose(1033, yaw=-179.0),
        publish_timestamp_ms=1033,
    )
    assert filter_._yaw.position > 180.0
    mark_backend_transition(preserve_position=False)

    output = filter_.update_pose(
        _pose(2000, yaw=-10.0),
        publish_timestamp_ms=2000,
    )

    assert output.yaw_deg == pytest.approx(-10.0)
    assert filter_._yaw.position == pytest.approx(-10.0)
    assert filter_._yaw.velocity == pytest.approx(0.0)
