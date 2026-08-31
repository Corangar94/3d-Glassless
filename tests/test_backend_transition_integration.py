from __future__ import annotations

from collections import deque

import pytest

from tracker.async_inference_watchdog import AsyncInferenceFailure
from tracker.backend_failover import (
    AutoFailoverFaceTracker,
    BackendFailoverPolicy,
)
from tracker.backend_transition_state import reset_backend_transition_generation
from tracker.pose import HeadPosition
from tracker.pose_filter import AdaptivePoseFilter


class FakeTracker:
    def __init__(self, *results: object, ready_after: int | None = None) -> None:
        self.results = deque(results)
        self.ready_after = ready_after
        self.calls = 0
        self.reset_count = 0
        self.close_count = 0

    def process_frame(self, _frame, capture_timestamp_ms=None):
        self.calls += 1
        result = self.results.popleft() if self.results else None
        if isinstance(result, BaseException):
            raise result
        return result

    def ready_for_promotion(self) -> bool:
        return self.ready_after is not None and self.calls >= self.ready_after

    def reset_session(self) -> None:
        self.reset_count += 1

    def close(self) -> None:
        self.close_count += 1


def _pose(x: float, timestamp: int, *, yaw: float = 0.0) -> HeadPosition:
    return HeadPosition(
        x_cm=x,
        y_cm=0.0,
        z_cm=60.0,
        yaw_deg=yaw,
        confidence=1.0,
        capture_timestamp_ms=timestamp,
    )


@pytest.fixture(autouse=True)
def _clean_transition_generation():
    reset_backend_transition_generation()
    yield
    reset_backend_transition_generation()


def test_runtime_failover_starts_at_recent_primary_pose_then_converges():
    primary = FakeTracker(
        _pose(10.0, 1000),
        AsyncInferenceFailure("submission failed"),
    )
    fallback = FakeTracker(
        _pose(0.0, 1033),
        _pose(0.0, 1258),
    )
    tracker = AutoFailoverFaceTracker(
        primary_factory=lambda: primary,
        fallback_factory=lambda: fallback,
        policy=BackendFailoverPolicy(max_primary_retries=0),
        logger=lambda _message: None,
    )

    first = tracker.process_frame(object(), 1000)
    switched = tracker.process_frame(object(), 1033)
    halfway = tracker.process_frame(object(), 1258)

    assert first.x_cm == pytest.approx(10.0)
    assert switched.x_cm == pytest.approx(10.0)
    assert halfway.x_cm == pytest.approx(5.0)
    assert tracker.backend_transition_id == 1
    assert tracker.snapshot(1258).pose_transition_active


def test_stale_primary_pose_is_not_blended_after_long_watchdog_stall():
    primary = FakeTracker(
        _pose(10.0, 1000),
        AsyncInferenceFailure("callback stalled"),
    )
    fallback = FakeTracker(_pose(0.0, 6000))
    tracker = AutoFailoverFaceTracker(
        primary_factory=lambda: primary,
        fallback_factory=lambda: fallback,
        policy=BackendFailoverPolicy(max_primary_retries=0),
        logger=lambda _message: None,
    )

    tracker.process_frame(object(), 1000)
    result = tracker.process_frame(object(), 6000)

    assert result.x_cm == pytest.approx(0.0)
    assert not tracker.snapshot(6000).pose_transition_active


def test_backend_switch_resets_filter_velocity_before_bridged_pose_update():
    primary = FakeTracker(
        _pose(0.0, 1000),
        _pose(3.0, 1033),
        AsyncInferenceFailure("submission failed"),
    )
    fallback = FakeTracker(_pose(0.0, 1066))
    tracker = AutoFailoverFaceTracker(
        primary_factory=lambda: primary,
        fallback_factory=lambda: fallback,
        policy=BackendFailoverPolicy(max_primary_retries=0),
        logger=lambda _message: None,
    )
    filter_ = AdaptivePoseFilter(
        process_noise=2.0,
        measurement_noise=0.1,
    )

    for timestamp in (1000, 1033):
        pose = tracker.process_frame(object(), timestamp)
        output = filter_.update_pose(
            pose,
            publish_timestamp_ms=timestamp,
        )
    assert output.vx_cm_s > 0.0

    switched_pose = tracker.process_frame(object(), 1066)
    switched_output = filter_.update_pose(
        switched_pose,
        publish_timestamp_ms=1066,
    )

    assert switched_pose.x_cm == pytest.approx(3.0)
    assert switched_output.x_cm == pytest.approx(3.0, abs=0.1)
    assert switched_output.vx_cm_s == pytest.approx(0.0, abs=0.05)


def test_promotion_marks_second_transition_and_aligns_candidate_pose():
    failed = FakeTracker(AsyncInferenceFailure("failed"))
    candidate = FakeTracker(_pose(20.0, 1001), ready_after=1)
    factories = iter((failed, candidate))
    fallback = FakeTracker(_pose(1.0, 1000), _pose(1.0, 1001))
    tracker = AutoFailoverFaceTracker(
        primary_factory=lambda: next(factories),
        fallback_factory=lambda: fallback,
        policy=BackendFailoverPolicy(
            retry_primary_after_ms=0,
            max_primary_retries=1,
        ),
        logger=lambda _message: None,
    )

    first = tracker.process_frame(object(), 1000)
    promoted = tracker.process_frame(object(), 1001)

    assert first.x_cm == pytest.approx(1.0)
    assert promoted.x_cm == pytest.approx(1.0)
    assert tracker.active_backend == "mediapipe"
    snapshot = tracker.snapshot(1001)
    assert snapshot.backend_transition_id == 2
    assert snapshot.pose_transition_active


def test_camera_session_reset_drops_old_bridge_source():
    primary = FakeTracker(
        _pose(10.0, 1000),
        AsyncInferenceFailure("failed"),
    )
    fallback = FakeTracker(_pose(0.0, 1033))
    tracker = AutoFailoverFaceTracker(
        primary_factory=lambda: primary,
        fallback_factory=lambda: fallback,
        policy=BackendFailoverPolicy(max_primary_retries=0),
        logger=lambda _message: None,
    )

    tracker.process_frame(object(), 1000)
    tracker.reset_session()
    switched = tracker.process_frame(object(), 1033)

    assert switched.x_cm == pytest.approx(0.0)
    assert primary.reset_count == 1


def test_startup_fallback_has_no_artificial_backend_transition():
    fallback = FakeTracker(_pose(4.0, 1000))
    tracker = AutoFailoverFaceTracker(
        primary_factory=lambda: (_ for _ in ()).throw(
            ImportError("mediapipe unavailable")
        ),
        fallback_factory=lambda: fallback,
        policy=BackendFailoverPolicy(max_primary_retries=0),
        logger=lambda _message: None,
    )

    result = tracker.process_frame(object(), 1000)

    assert result.x_cm == pytest.approx(4.0)
    assert tracker.backend_transition_id == 0
