from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tracker.backend_transition_state import (
    mark_backend_transition,
    reset_backend_transition_generation,
)
from tracker.main import TrackingLoop
from tracker.pose import FilteredPose


class LegacySmoother:
    def __init__(self) -> None:
        self.reset_count = 0

    def update(self, x, y, z, dt_seconds=None):
        return x, y, z

    def reset(self) -> None:
        self.reset_count += 1

    def set_measurement_noise(self, _value: float) -> None:
        pass


class PoseFilterStub(LegacySmoother):
    def update_pose(self, pose):
        return pose

    def predict(self):
        return FilteredPose(x_cm=0.0, y_cm=0.0, z_cm=60.0)


class Tracker:
    def process_frame(self, _frame, capture_timestamp_ms=None):
        return None


@pytest.fixture(autouse=True)
def _clean_transition_generation():
    reset_backend_transition_generation()
    yield
    reset_backend_transition_generation()


def _loop(smoother) -> TrackingLoop:
    return TrackingLoop(
        tracker=Tracker(),
        writer=MagicMock(),
        smoother=smoother,
    )


def test_recent_transition_restarts_raw_limiter_and_legacy_smoother():
    smoother = LegacySmoother()
    loop = _loop(smoother)
    old_output = FilteredPose(x_cm=8.0, y_cm=2.0, z_cm=65.0)
    loop._last_raw_pos = (8.0, 2.0, 65.0)
    loop._last_measurement_s = 10.0
    loop._last_face_ms = 20.0
    loop._last_output_pose = old_output

    generation = mark_backend_transition(preserve_position=True)
    loop._synchronize_tracker_backend_transition()

    assert loop._last_raw_pos is None
    assert loop._last_measurement_s is None
    assert loop._last_face_ms == 20.0
    assert loop._last_output_pose is old_output
    assert smoother.reset_count == 1
    assert loop._backend_transition_generation == generation


def test_adaptive_style_filter_handles_its_own_transition_reset():
    smoother = PoseFilterStub()
    loop = _loop(smoother)
    loop._last_raw_pos = (1.0, 2.0, 60.0)

    mark_backend_transition(preserve_position=True)
    loop._synchronize_tracker_backend_transition()

    assert loop._last_raw_pos is None
    assert smoother.reset_count == 0


def test_stale_transition_clears_hold_source_and_last_output():
    smoother = PoseFilterStub()
    loop = _loop(smoother)
    loop._last_face_ms = 123.0
    loop._last_raw_pos = (20.0, 0.0, 80.0)
    loop._last_output_pose = FilteredPose(
        x_cm=20.0,
        y_cm=0.0,
        z_cm=80.0,
    )
    loop._last_smoothed = (20.0, 0.0, 80.0)

    mark_backend_transition(preserve_position=False)
    loop._synchronize_tracker_backend_transition()

    assert loop._last_face_ms is None
    assert loop._last_raw_pos is None
    assert loop._last_output_pose.xyz == pytest.approx((0.0, 0.0, 60.0))
    assert loop._last_smoothed == pytest.approx((0.0, 0.0, 60.0))


def test_repeated_sync_for_same_generation_is_a_noop():
    smoother = LegacySmoother()
    loop = _loop(smoother)
    mark_backend_transition(preserve_position=True)

    loop._synchronize_tracker_backend_transition()
    loop._last_raw_pos = (1.0, 2.0, 3.0)
    loop._synchronize_tracker_backend_transition()

    assert smoother.reset_count == 1
    assert loop._last_raw_pos == (1.0, 2.0, 3.0)


def test_tracking_loop_checks_transition_after_backend_processing():
    source = open("tracker/main.py", encoding="utf-8").read()
    measured_block = source.split(
        "measured = _validated_pose(",
        1,
    )[1].split("if measured is not None:", 1)[0]

    assert "self._process_frame(frame, capture_timestamp_ms)" in measured_block
    assert "self._synchronize_tracker_backend_transition()" in measured_block
