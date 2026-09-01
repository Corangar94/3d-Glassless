from __future__ import annotations

from types import SimpleNamespace
import threading

import pytest

from tracker.face_tracker import FaceTracker
from tracker.pose import HeadPosition


def _tracker(maximum_age_ms: int = 250) -> FaceTracker:
    tracker = FaceTracker.__new__(FaceTracker)
    tracker._lock = threading.Lock()
    tracker._latest_pose = None
    tracker._last_delivered_timestamp_ms = None
    tracker._async_max_result_age_ms = maximum_age_ms
    tracker._stale_result_count = 0
    tracker._last_stale_result_age_ms = None
    tracker._last_submitted_media_timestamp_ms = None
    tracker._minimum_result_media_timestamp_ms = None
    tracker._async_watchdog = None
    return tracker


def _pose(timestamp_ms: int) -> HeadPosition:
    return HeadPosition(
        x_cm=1.0,
        y_cm=2.0,
        z_cm=60.0,
        confidence=0.9,
        capture_timestamp_ms=timestamp_ms,
    )


def test_result_at_age_limit_is_delivered_once():
    tracker = _tracker(maximum_age_ms=250)
    pose = _pose(1000)
    tracker._latest_pose = pose

    assert tracker._poll_latest(1250) is pose
    assert tracker._poll_latest(1251) is None
    assert tracker.stale_result_count == 0
    assert tracker.last_stale_result_age_ms is None


def test_result_beyond_age_limit_is_retired_once():
    tracker = _tracker(maximum_age_ms=250)
    tracker._latest_pose = _pose(1000)

    assert tracker._poll_latest(1251) is None
    assert tracker.stale_result_count == 1
    assert tracker.last_stale_result_age_ms == 251

    # The same late callback is retired rather than reconsidered forever.
    assert tracker._poll_latest(2000) is None
    assert tracker.stale_result_count == 1


def test_freshness_age_is_wrap_safe():
    tracker = _tracker(maximum_age_ms=48)
    pose = _pose(0xFFFF_FFF0)
    tracker._latest_pose = pose

    assert tracker._poll_latest(0x20) is pose

    tracker._latest_pose = _pose(0xFFFF_FFF1)
    tracker._last_delivered_timestamp_ms = None
    tracker._async_max_result_age_ms = 46
    assert tracker._poll_latest(0x20) is None
    assert tracker.last_stale_result_age_ms == 47


def test_small_future_timestamp_is_not_misclassified_as_ancient():
    tracker = _tracker(maximum_age_ms=250)
    pose = _pose(1100)
    tracker._latest_pose = pose

    assert tracker._poll_latest(1000) is pose
    assert tracker.stale_result_count == 0


def test_zero_age_limit_disables_stale_result_gate():
    tracker = _tracker(maximum_age_ms=0)
    pose = _pose(1000)
    tracker._latest_pose = pose

    assert tracker._poll_latest(100_000) is pose
    assert tracker.stale_result_count == 0


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
    assert tracker.last_stale_result_age_ms is None


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


def test_negative_maximum_result_age_fails_before_model_creation():
    with pytest.raises(ValueError, match="async_max_result_age_ms"):
        FaceTracker(
            real_ipd_cm=6.3,
            screen_width_cm=60.0,
            screen_height_cm=34.0,
            async_max_result_age_ms=-1,
        )
