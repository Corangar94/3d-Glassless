from __future__ import annotations

from types import SimpleNamespace
import threading

import pytest

from tracker.async_inference_watchdog import AsyncInferenceFailure
from tracker.async_result_freshness import (
    AsyncResultFreshnessGate,
    AsyncResultFreshnessPolicy,
)
from tracker.face_tracker import FaceTracker
from tracker.pose import HeadPosition


def _tracker(
    maximum_age_ms: int = 250,
    *,
    maximum_consecutive_stale: int = 3,
    stale_window_ms: int = 1000,
) -> FaceTracker:
    tracker = FaceTracker.__new__(FaceTracker)
    tracker._lock = threading.Lock()
    tracker._latest_pose = None
    tracker._last_delivered_timestamp_ms = None
    tracker._async_max_result_age_ms = maximum_age_ms
    tracker._stale_result_count = 0
    tracker._last_stale_result_age_ms = None
    tracker._async_result_freshness = AsyncResultFreshnessGate(
        AsyncResultFreshnessPolicy(
            max_result_age_ms=maximum_age_ms,
            max_consecutive_stale_results=maximum_consecutive_stale,
            stale_result_window_ms=stale_window_ms,
        )
    )
    tracker._last_submitted_media_timestamp_ms = None
    tracker._minimum_result_media_timestamp_ms = None
    tracker._async_watchdog = None
    tracker._closed = False
    return tracker


def _pose(timestamp_ms: int) -> HeadPosition:
    return HeadPosition(
        x_cm=1.0,
        y_cm=2.0,
        z_cm=60.0,
        confidence=0.9,
        capture_timestamp_ms=timestamp_ms,
    )


def _publish_pose(tracker: FaceTracker, timestamp_ms: int) -> None:
    tracker._latest_pose = _pose(timestamp_ms)


def test_result_at_age_limit_is_delivered_once():
    tracker = _tracker(maximum_age_ms=250)
    pose = _pose(1000)
    tracker._latest_pose = pose

    assert tracker._poll_latest(1250) is pose
    assert tracker._poll_latest(1251) is None
    assert tracker.stale_result_count == 0
    assert tracker.consecutive_stale_result_count == 0
    assert tracker.last_stale_result_age_ms is None


def test_result_beyond_age_limit_is_retired_once():
    tracker = _tracker(maximum_age_ms=250)
    _publish_pose(tracker, 1000)

    assert tracker._poll_latest(1251) is None
    assert tracker.stale_result_count == 1
    assert tracker.consecutive_stale_result_count == 1
    assert tracker.last_stale_result_age_ms == 251

    # The same late callback is retired rather than reconsidered forever.
    assert tracker._poll_latest(2000) is None
    assert tracker.stale_result_count == 1


def test_third_stale_pose_inside_window_escalates_to_backend_failover():
    tracker = _tracker(
        maximum_age_ms=250,
        maximum_consecutive_stale=3,
        stale_window_ms=1000,
    )

    _publish_pose(tracker, 1000)
    assert tracker._poll_latest(1300) is None
    _publish_pose(tracker, 1033)
    assert tracker._poll_latest(1333) is None
    _publish_pose(tracker, 1066)
    with pytest.raises(
        AsyncInferenceFailure,
        match="3 stale pose results",
    ):
        tracker._poll_latest(1366)

    assert tracker.stale_result_count == 3
    assert tracker.consecutive_stale_result_count == 3
    assert tracker.last_stale_result_age_ms == 300
    # The result that triggered failover is retired as well.
    assert tracker._poll_latest(1400) is None


def test_fresh_pose_resets_stale_escalation_episode():
    tracker = _tracker()
    for pose_timestamp, current_timestamp in (
        (1000, 1300),
        (1033, 1333),
    ):
        _publish_pose(tracker, pose_timestamp)
        assert tracker._poll_latest(current_timestamp) is None

    fresh = _pose(1370)
    tracker._latest_pose = fresh
    assert tracker._poll_latest(1400) is fresh
    assert tracker.consecutive_stale_result_count == 0

    _publish_pose(tracker, 1400)
    assert tracker._poll_latest(1700) is None
    assert tracker.consecutive_stale_result_count == 1


def test_no_face_callback_resets_stale_escalation_episode():
    tracker = _tracker()
    for pose_timestamp, current_timestamp in (
        (1000, 1300),
        (1033, 1333),
    ):
        _publish_pose(tracker, pose_timestamp)
        assert tracker._poll_latest(current_timestamp) is None
    assert tracker.consecutive_stale_result_count == 2

    tracker._pose_from_result = lambda *_args: None
    tracker._on_result(
        object(),
        SimpleNamespace(width=640, height=480),
        1366,
    )

    assert tracker._latest_pose is None
    assert tracker.consecutive_stale_result_count == 0
    _publish_pose(tracker, 1066)
    assert tracker._poll_latest(1366) is None
    assert tracker.consecutive_stale_result_count == 1


def test_freshness_age_is_wrap_safe():
    tracker = _tracker(maximum_age_ms=48)
    pose = _pose(0xFFFF_FFF0)
    tracker._latest_pose = pose

    assert tracker._poll_latest(0x20) is pose

    tracker = _tracker(maximum_age_ms=46)
    tracker._latest_pose = _pose(0xFFFF_FFF1)
    assert tracker._poll_latest(0x20) is None
    assert tracker.last_stale_result_age_ms == 47


def test_small_future_timestamp_is_not_misclassified_as_ancient():
    tracker = _tracker(maximum_age_ms=250)
    pose = _pose(1100)
    tracker._latest_pose = pose

    assert tracker._poll_latest(1000) is pose
    assert tracker.stale_result_count == 0
    assert tracker.consecutive_stale_result_count == 0


def test_zero_age_limit_disables_stale_result_gate():
    tracker = _tracker(maximum_age_ms=0)
    pose = _pose(1000)
    tracker._latest_pose = pose

    assert tracker._poll_latest(100_000) is pose
    assert tracker.stale_result_count == 0


def test_zero_stale_threshold_drops_forever_without_escalating():
    tracker = _tracker(maximum_consecutive_stale=0)

    for index in range(10):
        _publish_pose(tracker, 1000 + index)
        assert tracker._poll_latest(1300 + index) is None

    assert tracker.stale_result_count == 10
    assert tracker.consecutive_stale_result_count == 10


def test_missing_pose_timestamp_preserves_legacy_delivery():
    tracker = _tracker(maximum_age_ms=1)
    pose = _pose(0)
    tracker._latest_pose = pose

    assert tracker._poll_latest(100_000) is pose


def test_session_reset_clears_stale_delivery_episode():
    tracker = _tracker(maximum_age_ms=100)
    tracker._latest_pose = _pose(1000)
    assert tracker._poll_latest(1200) is None
    assert tracker.stale_result_count == 1

    tracker.reset_session()

    assert tracker._latest_pose is None
    assert tracker._last_delivered_timestamp_ms is None
    assert tracker.stale_result_count == 0
    assert tracker.consecutive_stale_result_count == 0
    assert tracker.last_stale_result_age_ms is None


def test_bare_legacy_tracker_without_gate_keeps_previous_drop_behavior():
    tracker = _tracker(maximum_age_ms=250)
    tracker._async_result_freshness = None
    tracker._latest_pose = _pose(1000)

    assert tracker._poll_latest(1251) is None
    assert tracker.stale_result_count == 1
    assert tracker.last_stale_result_age_ms == 251


def test_rollover_callback_timestamp_uses_nonzero_wire_contract():
    tracker = _tracker()
    tracker._real_ipd_cm = 6.3
    tracker._camera_fov_deg = 90.0
    tracker._camera_geometry = None
    landmarks = [SimpleNamespace(x=0.5, y=0.5) for _ in range(474)]
    landmarks[468] = SimpleNamespace(x=0.45, y=0.5)
    landmarks[473] = SimpleNamespace(x=0.55, y=0.5)
    result = SimpleNamespace(
        face_landmarks=[landmarks],
        facial_transformation_matrixes=[],
    )

    pose = tracker._pose_from_result(result, 640, 480, 0)

    assert pose is not None
    assert pose.capture_timestamp_ms == 0xFFFF_FFFF


@pytest.mark.parametrize(
    ("argument", "value", "message"),
    [
        ("async_max_result_age_ms", -1, "max_result_age_ms"),
        (
            "async_max_consecutive_stale_results",
            -1,
            "max_consecutive_stale_results",
        ),
        (
            "async_stale_result_window_ms",
            0,
            "stale_result_window_ms",
        ),
    ],
)
def test_invalid_freshness_configuration_fails_before_model_creation(
    argument,
    value,
    message,
):
    kwargs = {argument: value}
    with pytest.raises(ValueError, match=message):
        FaceTracker(
            real_ipd_cm=6.3,
            screen_width_cm=60.0,
            screen_height_cm=34.0,
            **kwargs,
        )
