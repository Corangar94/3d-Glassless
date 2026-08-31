from __future__ import annotations

import pytest

from tracker.backend_transition_state import (
    current_backend_transition_generation,
    mark_backend_transition,
    reset_backend_transition_generation,
)
from tracker.pose import HeadPosition
from tracker.pose_filter import AdaptivePoseFilter


@pytest.fixture(autouse=True)
def _clean_transition_generation():
    reset_backend_transition_generation()
    yield
    reset_backend_transition_generation()


def _pose(x: float, timestamp: int) -> HeadPosition:
    return HeadPosition(
        x_cm=x,
        y_cm=0.0,
        z_cm=60.0,
        confidence=1.0,
        capture_timestamp_ms=timestamp,
    )


def _moving_filter() -> AdaptivePoseFilter:
    filter_ = AdaptivePoseFilter(
        process_noise=2.0,
        measurement_noise=0.1,
    )
    filter_.update_pose(_pose(0.0, 1000), publish_timestamp_ms=1000)
    output = filter_.update_pose(
        _pose(3.0, 1033),
        publish_timestamp_ms=1033,
    )
    assert output.vx_cm_s > 0.0
    return filter_


def test_transition_resets_velocity_before_next_measurement():
    filter_ = _moving_filter()
    generation = mark_backend_transition()

    output = filter_.update_pose(
        _pose(3.0, 1066),
        publish_timestamp_ms=1066,
    )

    assert output.x_cm == pytest.approx(3.0)
    assert output.vx_cm_s == pytest.approx(0.0)
    assert filter_._backend_transition_generation == generation


def test_transition_resets_velocity_before_hold_prediction():
    filter_ = _moving_filter()
    mark_backend_transition()

    output = filter_.predict(publish_timestamp_ms=1066)

    assert output.xyz == pytest.approx((0.0, 0.0, 60.0))
    assert output.vx_cm_s == pytest.approx(0.0)
    assert output.confidence == pytest.approx(0.0)


def test_no_transition_preserves_estimated_velocity():
    filter_ = _moving_filter()

    output = filter_.predict(publish_timestamp_ms=1066)

    assert output.vx_cm_s > 0.0
    assert output.x_cm > 3.0


def test_manual_reset_acknowledges_current_transition_generation():
    filter_ = _moving_filter()
    generation = mark_backend_transition()

    filter_.reset()
    output = filter_.predict(publish_timestamp_ms=1066)

    assert filter_._backend_transition_generation == generation
    assert output.xyz == pytest.approx((0.0, 0.0, 60.0))


def test_filter_created_after_transition_starts_in_current_generation():
    generation = mark_backend_transition()

    filter_ = AdaptivePoseFilter()
    output = filter_.update_pose(
        _pose(5.0, 1000),
        publish_timestamp_ms=1000,
    )

    assert filter_._backend_transition_generation == generation
    assert output.x_cm == pytest.approx(5.0)
    assert output.vx_cm_s == pytest.approx(0.0)


def test_generation_is_thread_local_state_with_monotonic_ids():
    assert current_backend_transition_generation() == 0
    assert mark_backend_transition() == 1
    assert mark_backend_transition() == 2
    assert current_backend_transition_generation() == 2
