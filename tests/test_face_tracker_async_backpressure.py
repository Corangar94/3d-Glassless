from __future__ import annotations

from unittest.mock import MagicMock
import threading

import numpy as np
import pytest

from tracker.async_inference_watchdog import (
    AsyncInferenceFailure,
    AsyncInferenceWatchdog,
)
from tracker.face_tracker import FaceTracker
from tracker.pose import HeadPosition


def _tracker(
    *,
    max_backlog_ms: int = 150,
    stall_timeout_ms: int = 5000,
) -> FaceTracker:
    tracker = FaceTracker.__new__(FaceTracker)
    tracker._real_ipd_cm = 6.3
    tracker._screen_width_cm = 60.0
    tracker._screen_height_cm = 34.0
    tracker._camera_fov_deg = 90.0
    tracker._camera_geometry = None
    tracker._async_mode = True
    tracker._async_max_backlog_ms = max_backlog_ms
    tracker._lock = threading.Lock()
    tracker._latest_pose = None
    tracker._last_delivered_timestamp_ms = None
    tracker._last_submitted_wire_timestamp_ms = None
    tracker._last_submitted_media_timestamp_ms = None
    tracker._minimum_result_media_timestamp_ms = None
    tracker._async_watchdog = AsyncInferenceWatchdog(
        max_consecutive_errors=3,
        stall_timeout_ms=stall_timeout_ms,
    )
    tracker._closed = False
    tracker._landmarker = MagicMock()
    tracker._mediapipe_image = MagicMock(
        side_effect=lambda frame: ("image", id(frame))
    )
    return tracker


def _frame() -> np.ndarray:
    return np.zeros((8, 12, 3), dtype=np.uint8)


def test_backlog_skips_conversion_allocation_and_submission():
    tracker = _tracker(max_backlog_ms=150)
    frame = _frame()

    for timestamp in (1000, 1033, 1066, 1099, 1132):
        assert tracker.process_frame(frame, timestamp) is None
    conversion_calls = tracker._mediapipe_image.call_count
    submission_calls = tracker._landmarker.detect_async.call_count

    assert tracker.process_frame(frame, 1150) is None

    assert tracker._mediapipe_image.call_count == conversion_calls
    assert tracker._landmarker.detect_async.call_count == submission_calls
    snapshot = tracker._async_watchdog.snapshot(1150)
    assert snapshot.throttled_submission_count == 1
    assert snapshot.consecutive_submission_errors == 0


def test_throttled_frame_does_not_advance_submission_timeline():
    tracker = _tracker(max_backlog_ms=100)
    frame = _frame()

    tracker.process_frame(frame, 1000)
    tracker.process_frame(frame, 1033)
    tracker.process_frame(frame, 1066)
    assert tracker.process_frame(frame, 1100) is None

    assert tracker._last_submitted_wire_timestamp_ms == 1066
    assert tracker._last_submitted_media_timestamp_ms == 1066
    assert tracker._async_watchdog.snapshot().last_submission_ms == 1066


def test_callback_progress_reenables_conversion_and_submission():
    tracker = _tracker(max_backlog_ms=100)
    frame = _frame()
    for timestamp in (1000, 1033, 1066):
        tracker.process_frame(frame, timestamp)
    tracker.process_frame(frame, 1100)
    calls_before = tracker._landmarker.detect_async.call_count

    tracker._async_watchdog.record_callback(1033)
    tracker.process_frame(frame, 1101)

    assert tracker._landmarker.detect_async.call_count == calls_before + 1
    assert tracker._mediapipe_image.call_count == calls_before + 1
    assert tracker._last_submitted_media_timestamp_ms == 1101


def test_duplicate_wire_timestamp_is_rejected_before_conversion():
    tracker = _tracker()
    frame = _frame()

    tracker.process_frame(frame, 1000)
    calls_before = tracker._mediapipe_image.call_count
    tracker.process_frame(frame, 1000)

    assert tracker._mediapipe_image.call_count == calls_before
    assert tracker._landmarker.detect_async.call_count == 1


def test_throttled_frame_can_deliver_latest_completed_pose():
    tracker = _tracker(max_backlog_ms=100)
    frame = _frame()
    for timestamp in (1000, 1033, 1066):
        tracker.process_frame(frame, timestamp)
    pose = HeadPosition(
        x_cm=1.0,
        y_cm=2.0,
        z_cm=60.0,
        capture_timestamp_ms=1033,
    )
    tracker._latest_pose = pose

    assert tracker.process_frame(frame, 1100) is pose
    assert tracker.process_frame(frame, 1133) is None
    assert tracker._landmarker.detect_async.call_count == 3


def test_stall_escalates_even_after_backpressure_stops_new_submissions():
    tracker = _tracker(
        max_backlog_ms=100,
        stall_timeout_ms=5000,
    )
    frame = _frame()
    tracker.process_frame(frame, 1000)
    tracker.process_frame(frame, 1066)
    tracker.process_frame(frame, 1100)
    assert tracker._async_watchdog.snapshot().throttled_submission_count == 1

    with pytest.raises(AsyncInferenceFailure, match="callbacks stalled"):
        tracker.process_frame(frame, 6000)

    # Health escalation occurs before any conversion attempt.
    assert tracker._mediapipe_image.call_count == 2
    assert tracker._landmarker.detect_async.call_count == 2


def test_detect_async_failure_does_not_commit_submission_timeline():
    tracker = _tracker()
    tracker._landmarker.detect_async.side_effect = RuntimeError("rejected")

    assert tracker.process_frame(_frame(), 1000) is None

    assert tracker._last_submitted_wire_timestamp_ms is None
    assert tracker._last_submitted_media_timestamp_ms is None
    snapshot = tracker._async_watchdog.snapshot()
    assert snapshot.first_submission_ms is None
    assert snapshot.consecutive_submission_errors == 1


def test_zero_backlog_limit_preserves_every_frame_submission():
    tracker = _tracker(max_backlog_ms=0)
    frame = _frame()

    for timestamp in range(1000, 1400, 20):
        tracker.process_frame(frame, timestamp)

    assert tracker._landmarker.detect_async.call_count == 20
    assert tracker._mediapipe_image.call_count == 20
    assert tracker._async_watchdog.snapshot().throttled_submission_count == 0
