from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
import threading

from tracker.async_inference_watchdog import AsyncInferenceWatchdog
from tracker.face_tracker import FaceTracker
from tracker.pose import HeadPosition


def _pose(timestamp_ms: int, *, x_cm: float | None = None) -> HeadPosition:
    return HeadPosition(
        x_cm=float(timestamp_ms if x_cm is None else x_cm),
        y_cm=2.0,
        z_cm=60.0,
        confidence=0.9,
        capture_timestamp_ms=int(timestamp_ms) & 0xFFFF_FFFF,
    )


def _tracker() -> FaceTracker:
    tracker = FaceTracker.__new__(FaceTracker)
    tracker._lock = threading.Lock()
    tracker._closed = False
    tracker._latest_pose = None
    tracker._last_delivered_timestamp_ms = None
    tracker._minimum_result_media_timestamp_ms = None
    tracker._last_submitted_wire_timestamp_ms = None
    tracker._last_submitted_media_timestamp_ms = None
    tracker._stale_result_count = 0
    tracker._last_stale_result_age_ms = None
    tracker._async_result_freshness = MagicMock()
    tracker._async_watchdog = AsyncInferenceWatchdog(
        max_consecutive_errors=3,
        stall_timeout_ms=5000,
    )
    tracker._pose_from_result = MagicMock(
        side_effect=lambda _result, _width, _height, timestamp: _pose(
            timestamp
        )
    )
    return tracker


def _image():
    return SimpleNamespace(width=640, height=480)


def test_older_callback_is_rejected_before_pose_conversion():
    tracker = _tracker()
    image = _image()

    tracker._on_result(object(), image, 300)
    tracker._on_result(object(), image, 200)

    assert tracker._latest_pose is not None
    assert tracker._latest_pose.capture_timestamp_ms == 300
    assert tracker._pose_from_result.call_count == 1
    snapshot = tracker.async_callback_order_snapshot()
    assert snapshot.latest_published_timestamp_ms == 300
    assert snapshot.accepted_publication_count == 1
    assert snapshot.out_of_order_drop_count == 1
    assert snapshot.pre_conversion_drop_count == 1
    assert snapshot.post_conversion_drop_count == 0
    health = tracker._async_watchdog.snapshot()
    assert health.last_callback_ms == 300
    assert health.consecutive_successful_callbacks == 1


def test_duplicate_callback_is_rejected_before_pose_conversion():
    tracker = _tracker()
    image = _image()

    tracker._on_result(object(), image, 300)
    tracker._on_result(object(), image, 300)

    assert tracker._pose_from_result.call_count == 1
    snapshot = tracker.async_callback_order_snapshot()
    assert snapshot.duplicate_drop_count == 1
    assert snapshot.pre_conversion_drop_count == 1


def test_older_callback_that_loses_conversion_race_cannot_overwrite_newer_pose():
    tracker = _tracker()
    image = _image()

    def convert(_result, _width, _height, timestamp):
        if timestamp == 200:
            tracker._on_result(object(), image, 300)
        return _pose(timestamp)

    tracker._pose_from_result.side_effect = convert
    tracker._on_result(object(), image, 200)

    assert tracker._latest_pose is not None
    assert tracker._latest_pose.capture_timestamp_ms == 300
    snapshot = tracker.async_callback_order_snapshot()
    assert snapshot.latest_published_timestamp_ms == 300
    assert snapshot.accepted_publication_count == 1
    assert snapshot.out_of_order_drop_count == 1
    assert snapshot.pre_conversion_drop_count == 0
    assert snapshot.post_conversion_drop_count == 1
    health = tracker._async_watchdog.snapshot()
    assert health.last_callback_ms == 300
    assert health.consecutive_successful_callbacks == 1


def test_newer_no_face_callback_blocks_older_pose_resurrection():
    tracker = _tracker()
    image = _image()

    def convert(_result, _width, _height, timestamp):
        if timestamp == 200:
            tracker._on_result(object(), image, 300)
            return _pose(200)
        return None

    tracker._pose_from_result.side_effect = convert
    tracker._on_result(object(), image, 200)

    assert tracker._latest_pose is None
    snapshot = tracker.async_callback_order_snapshot()
    assert snapshot.latest_published_timestamp_ms == 300
    assert snapshot.post_conversion_drop_count == 1
    tracker._async_result_freshness.record_result_without_pose.assert_called_once_with()


def test_next_newer_pose_publishes_after_race_drop():
    tracker = _tracker()
    image = _image()

    def convert(_result, _width, _height, timestamp):
        if timestamp == 200:
            tracker._on_result(object(), image, 300)
        return _pose(timestamp)

    tracker._pose_from_result.side_effect = convert
    tracker._on_result(object(), image, 200)
    tracker._on_result(object(), image, 400)

    assert tracker._latest_pose is not None
    assert tracker._latest_pose.capture_timestamp_ms == 400
    snapshot = tracker.async_callback_order_snapshot()
    assert snapshot.latest_published_timestamp_ms == 400
    assert snapshot.accepted_publication_count == 2
    assert snapshot.out_of_order_drop_count == 1


def test_obsolete_conversion_error_does_not_poison_watchdog():
    tracker = _tracker()
    image = _image()

    def convert(_result, _width, _height, timestamp):
        if timestamp == 200:
            tracker._on_result(object(), image, 300)
            raise IndexError("old malformed result")
        return _pose(timestamp)

    tracker._pose_from_result.side_effect = convert
    tracker._on_result(object(), image, 200)

    health = tracker._async_watchdog.snapshot()
    assert health.last_callback_ms == 300
    assert health.consecutive_callback_errors == 0
    assert health.consecutive_successful_callbacks == 1


def test_current_conversion_error_still_reaches_watchdog():
    tracker = _tracker()
    tracker._pose_from_result.side_effect = IndexError("malformed result")

    tracker._on_result(object(), _image(), 200)

    health = tracker._async_watchdog.snapshot()
    assert health.last_callback_ms == 200
    assert health.consecutive_callback_errors == 1
    assert health.consecutive_successful_callbacks == 0
    snapshot = tracker.async_callback_order_snapshot()
    assert snapshot.latest_published_timestamp_ms is None
    assert snapshot.accepted_publication_count == 0


def test_session_reset_keeps_global_callback_order_and_result_floor():
    tracker = _tracker()
    image = _image()
    tracker._on_result(object(), image, 300)
    tracker._last_submitted_media_timestamp_ms = 350

    tracker.reset_session()
    tracker._on_result(object(), image, 350)
    tracker._on_result(object(), image, 400)

    assert tracker._latest_pose is not None
    assert tracker._latest_pose.capture_timestamp_ms == 400
    snapshot = tracker.async_callback_order_snapshot()
    assert snapshot.latest_published_timestamp_ms == 400
    assert snapshot.accepted_publication_count == 2
    assert tracker._minimum_result_media_timestamp_ms == 350
    tracker._async_result_freshness.reset.assert_called_once_with()


def test_closed_tracker_and_invalid_timestamps_are_ignored():
    tracker = _tracker()
    tracker._closed = True

    tracker._on_result(object(), _image(), 100)
    tracker._on_result(object(), _image(), -1)
    tracker._on_result(object(), _image(), "bad")

    assert tracker._latest_pose is None
    tracker._pose_from_result.assert_not_called()
    assert tracker.async_callback_order_snapshot().total_drop_count == 0
    assert tracker._async_watchdog.snapshot().last_callback_ms is None
