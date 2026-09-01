from __future__ import annotations

import math

import pytest

from tracker.backend_transition_state import (
    mark_backend_transition,
    reset_backend_transition_generation,
)
from tracker.pose import HeadPosition
from tracker.pose_filter import AdaptivePoseFilter, _timestamp_delta_seconds


@pytest.fixture(autouse=True)
def _clean_backend_transition_generation():
    reset_backend_transition_generation()
    yield
    reset_backend_transition_generation()


def _pose(
    timestamp_ms: int,
    *,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 60.0,
    yaw: float = 0.0,
    pitch: float = 0.0,
    roll: float = 0.0,
    confidence: float = 1.0,
) -> HeadPosition:
    return HeadPosition(
        x_cm=x,
        y_cm=y,
        z_cm=z,
        yaw_deg=yaw,
        pitch_deg=pitch,
        roll_deg=roll,
        confidence=confidence,
        capture_timestamp_ms=timestamp_ms,
    )


def _moving_filter(*, reset_ms: float = 500.0) -> AdaptivePoseFilter:
    filter_ = AdaptivePoseFilter(
        process_noise=2.0,
        measurement_noise=0.1,
        prediction_horizon_ms=0.0,
        measurement_gap_reset_ms=reset_ms,
    )
    filter_.update_pose(
        _pose(1000, x=0.0, yaw=170.0),
        publish_timestamp_ms=1000,
    )
    moving = filter_.update_pose(
        _pose(1033, x=3.0, yaw=175.0),
        publish_timestamp_ms=1033,
    )
    assert moving.vx_cm_s > 0.0
    assert filter_._yaw.velocity > 0.0
    return filter_


def test_first_pose_after_long_gap_initializes_exactly_with_zero_velocity():
    filter_ = _moving_filter()

    reacquired = filter_.update_pose(
        _pose(
            1600,
            x=50.0,
            y=-7.0,
            z=90.0,
            yaw=-10.0,
            pitch=5.0,
            roll=2.0,
            confidence=0.8,
        ),
        publish_timestamp_ms=1600,
    )

    assert reacquired.xyz == pytest.approx((50.0, -7.0, 90.0))
    assert reacquired.vx_cm_s == pytest.approx(0.0)
    assert reacquired.vy_cm_s == pytest.approx(0.0)
    assert reacquired.vz_cm_s == pytest.approx(0.0)
    assert reacquired.yaw_deg == pytest.approx(-10.0)
    assert reacquired.pitch_deg == pytest.approx(5.0)
    assert reacquired.roll_deg == pytest.approx(2.0)
    assert reacquired.confidence == pytest.approx(0.8)
    assert filter_._yaw.velocity == pytest.approx(0.0)
    assert filter_.measurement_gap_reset_count == 1
    assert filter_.last_measurement_gap_ms == pytest.approx(567.0)


def test_exact_reset_threshold_starts_a_new_episode():
    filter_ = AdaptivePoseFilter(measurement_gap_reset_ms=500.0)
    filter_.update_pose(
        _pose(1000, x=1.0),
        publish_timestamp_ms=1000,
    )

    reacquired = filter_.update_pose(
        _pose(1500, x=20.0),
        publish_timestamp_ms=1500,
    )

    assert reacquired.x_cm == pytest.approx(20.0)
    assert reacquired.vx_cm_s == pytest.approx(0.0)
    assert filter_.measurement_gap_reset_count == 1
    assert filter_.last_measurement_gap_ms == pytest.approx(500.0)


def test_subthreshold_gap_keeps_continuous_filter_state():
    filter_ = _moving_filter()

    output = filter_.update_pose(
        _pose(1532, x=20.0, yaw=-10.0),
        publish_timestamp_ms=1532,
    )

    assert filter_.measurement_gap_reset_count == 0
    assert filter_.last_measurement_gap_ms is None
    assert output.x_cm != pytest.approx(20.0)
    assert output.yaw_deg != pytest.approx(-10.0)


def test_predicting_through_hold_does_not_reset_until_a_measurement_arrives():
    filter_ = _moving_filter()

    held = filter_.predict(publish_timestamp_ms=1600)

    assert held.x_cm > 0.0
    assert held.confidence < 1.0
    assert filter_.measurement_gap_reset_count == 0
    assert filter_.last_measurement_gap_ms is None

    reacquired = filter_.update_pose(
        _pose(1600, x=40.0),
        publish_timestamp_ms=1600,
    )
    assert reacquired.x_cm == pytest.approx(40.0)
    assert filter_.measurement_gap_reset_count == 1


def test_zero_threshold_disables_gap_reset_for_direct_callers():
    filter_ = _moving_filter(reset_ms=0.0)

    output = filter_.update_pose(
        _pose(5000, x=50.0),
        publish_timestamp_ms=5000,
    )

    assert filter_.measurement_gap_reset_count == 0
    assert filter_.last_measurement_gap_ms is None
    assert output.x_cm != pytest.approx(50.0)


def test_gap_detection_is_wrap_safe_at_windows_uptime_rollover():
    filter_ = AdaptivePoseFilter(measurement_gap_reset_ms=500.0)
    filter_.update_pose(
        _pose(0xFFFF_FF00, x=1.0),
        publish_timestamp_ms=0xFFFF_FF00,
    )

    # 0xFFFFFF00 -> 0x00000158 is exactly 600 ms.
    output = filter_.update_pose(
        _pose(0x0000_0158, x=25.0),
        publish_timestamp_ms=0x0000_0158,
    )

    assert output.x_cm == pytest.approx(25.0)
    assert output.vx_cm_s == pytest.approx(0.0)
    assert filter_.last_measurement_gap_ms == pytest.approx(600.0)


def test_half_range_ambiguous_timestamp_is_not_treated_as_a_long_gap():
    assert _timestamp_delta_seconds(0x8000_0001, 1) == 0.0


def test_long_gap_after_recent_backend_transition_uses_bridged_pose_as_fresh_state():
    filter_ = _moving_filter()
    generation = mark_backend_transition(preserve_position=True)

    bridged = filter_.update_pose(
        _pose(1600, x=3.0, yaw=175.0),
        publish_timestamp_ms=1600,
    )

    assert bridged.x_cm == pytest.approx(3.0)
    assert bridged.yaw_deg == pytest.approx(175.0)
    assert bridged.vx_cm_s == pytest.approx(0.0)
    assert filter_._yaw.velocity == pytest.approx(0.0)
    assert filter_.measurement_gap_reset_count == 1
    assert filter_._backend_transition_generation == generation


def test_stale_backend_transition_resets_before_the_next_measurement():
    filter_ = _moving_filter()
    generation = mark_backend_transition(preserve_position=False)

    output = filter_.update_pose(
        _pose(1600, x=-12.0, yaw=-30.0),
        publish_timestamp_ms=1600,
    )

    assert output.x_cm == pytest.approx(-12.0)
    assert output.yaw_deg == pytest.approx(-30.0)
    assert output.vx_cm_s == pytest.approx(0.0)
    # The backend reset already removed the old measurement anchor, so this is
    # not double-counted as an independent measurement-gap reset.
    assert filter_.measurement_gap_reset_count == 0
    assert filter_._backend_transition_generation == generation


def test_reset_clears_gap_state_and_diagnostics():
    filter_ = _moving_filter()
    filter_.update_pose(
        _pose(1600, x=20.0),
        publish_timestamp_ms=1600,
    )
    assert filter_.measurement_gap_reset_count == 1

    filter_.reset()

    snapshot = filter_.gap_snapshot()
    assert snapshot.measurement_gap_reset_ms == pytest.approx(500.0)
    assert snapshot.measurement_gap_reset_count == 0
    assert snapshot.last_measurement_gap_ms is None
    neutral = filter_.predict(publish_timestamp_ms=2000)
    assert neutral.xyz == pytest.approx((0.0, 0.0, 60.0))
    assert neutral.confidence == pytest.approx(0.0)


def test_runtime_gap_threshold_can_be_changed_or_disabled():
    filter_ = _moving_filter()

    filter_.set_measurement_gap_reset_ms(1000.0)
    filter_.update_pose(
        _pose(1600, x=20.0),
        publish_timestamp_ms=1600,
    )
    assert filter_.measurement_gap_reset_count == 0

    filter_.set_measurement_gap_reset_ms(0.0)
    filter_.update_pose(
        _pose(5000, x=30.0),
        publish_timestamp_ms=5000,
    )
    assert filter_.measurement_gap_reset_count == 0
    assert filter_.measurement_gap_reset_ms == pytest.approx(0.0)


@pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf")])
def test_invalid_gap_threshold_fails_closed(value):
    with pytest.raises(ValueError):
        AdaptivePoseFilter(measurement_gap_reset_ms=value)

    filter_ = AdaptivePoseFilter()
    with pytest.raises(ValueError):
        filter_.set_measurement_gap_reset_ms(value)


def test_default_threshold_matches_the_default_tracking_hold_window():
    filter_ = AdaptivePoseFilter()

    assert filter_.measurement_gap_reset_ms == pytest.approx(500.0)
    assert math.isfinite(filter_.measurement_gap_reset_ms)
