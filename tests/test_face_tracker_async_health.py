from __future__ import annotations

from types import SimpleNamespace
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


def _bare_tracker(
    *,
    stall_timeout_ms: int = 5000,
    max_errors: int = 3,
) -> FaceTracker:
    tracker = FaceTracker.__new__(FaceTracker)
    tracker._real_ipd_cm = 6.3
    tracker._screen_width_cm = 60.0
    tracker._screen_height_cm = 34.0
    tracker._camera_fov_deg = 90.0
    tracker._camera_geometry = None
    tracker._async_mode = True
    tracker._lock = threading.Lock()
    tracker._latest_pose = None
    tracker._last_delivered_timestamp_ms = None
    tracker._last_submitted_wire_timestamp_ms = None
    tracker._last_submitted_media_timestamp_ms = None
    tracker._minimum_result_media_timestamp_ms = None
    tracker._async_watchdog = AsyncInferenceWatchdog(
        max_consecutive_errors=max_errors,
        stall_timeout_ms=stall_timeout_ms,
    )
    tracker._closed = False
    tracker._landmarker = MagicMock()
    tracker._pose_from_result = MagicMock(
        side_effect=lambda _result, _width, _height, timestamp: HeadPosition(
            x_cm=1.0,
            y_cm=2.0,
            z_cm=60.0,
            capture_timestamp_ms=int(timestamp) & 0xFFFF_FFFF,
        )
    )
    return tracker


def _frame() -> np.ndarray:
    return np.zeros((4, 6, 3), dtype=np.uint8)


def test_repeated_detect_async_errors_escalate_after_bounded_attempts():
    tracker = _bare_tracker(max_errors=3)
    tracker._landmarker.detect_async.side_effect = RuntimeError("submit failed")

    assert tracker.process_frame(_frame(), 1000) is None
    assert tracker.process_frame(_frame(), 1033) is None
    with pytest.raises(AsyncInferenceFailure, match="submission failed 3"):
        tracker.process_frame(_frame(), 1066)

    assert tracker._landmarker.detect_async.call_count == 3


def test_one_successful_submission_resets_submission_error_episode():
    tracker = _bare_tracker(max_errors=3)
    tracker._landmarker.detect_async.side_effect = [
        RuntimeError("first"),
        RuntimeError("second"),
        None,
        RuntimeError("new episode"),
    ]

    for timestamp in (1000, 1033, 1066, 1099):
        assert tracker.process_frame(_frame(), timestamp) is None

    snapshot = tracker._async_watchdog.snapshot()
    assert snapshot.consecutive_submission_errors == 1


def test_no_callback_progress_escalates_after_timeout():
    tracker = _bare_tracker(stall_timeout_ms=5000)

    assert tracker.process_frame(_frame(), 1000) is None
    with pytest.raises(AsyncInferenceFailure, match="callbacks stalled"):
        tracker.process_frame(_frame(), 6000)


def test_successful_no_face_callback_counts_as_progress():
    tracker = _bare_tracker(stall_timeout_ms=5000)
    tracker._pose_from_result.return_value = None
    image = SimpleNamespace(width=640, height=480)

    assert tracker.process_frame(_frame(), 1000) is None
    tracker._on_result(object(), image, 1000)
    assert tracker.process_frame(_frame(), 5999) is None

    assert tracker._async_watchdog.snapshot().callback_lag_ms == 4999


def test_stale_callback_is_rejected_before_pose_conversion():
    tracker = _bare_tracker()
    tracker._last_submitted_media_timestamp_ms = 5000
    tracker.reset_session()
    image = SimpleNamespace(width=640, height=480)

    tracker._on_result(object(), image, 5000)

    tracker._pose_from_result.assert_not_called()
    assert tracker._latest_pose is None
    assert tracker._async_watchdog.snapshot().last_callback_ms is None


def test_reset_race_rechecks_callback_floor_before_publish():
    tracker = _bare_tracker()
    image = SimpleNamespace(width=640, height=480)

    def pose_then_reset(_result, _width, _height, timestamp):
        tracker._last_submitted_media_timestamp_ms = int(timestamp)
        tracker.reset_session()
        return HeadPosition(x_cm=1.0, y_cm=2.0, z_cm=60.0)

    tracker._pose_from_result.side_effect = pose_then_reset
    tracker._on_result(object(), image, 5000)

    assert tracker._latest_pose is None
    assert tracker._async_watchdog.snapshot().last_callback_ms is None


def test_repeated_callback_processing_errors_surface_on_camera_thread():
    tracker = _bare_tracker(max_errors=3)
    tracker._pose_from_result.side_effect = IndexError("malformed landmarks")
    image = SimpleNamespace(width=640, height=480)

    for timestamp in (1000, 1033, 1066):
        tracker._on_result(object(), image, timestamp)

    with pytest.raises(
        AsyncInferenceFailure,
        match="callback processing failed 3",
    ):
        tracker.process_frame(_frame(), 1099)

    assert tracker._landmarker.detect_async.call_count == 0


def test_good_callback_resets_callback_processing_error_episode():
    tracker = _bare_tracker(max_errors=3)
    image = SimpleNamespace(width=640, height=480)
    good_pose = HeadPosition(x_cm=1.0, y_cm=2.0, z_cm=60.0)
    tracker._pose_from_result.side_effect = [
        IndexError("bad"),
        IndexError("bad"),
        good_pose,
        IndexError("new episode"),
    ]

    for timestamp in (1000, 1033, 1066, 1099):
        tracker._on_result(object(), image, timestamp)

    snapshot = tracker._async_watchdog.snapshot()
    assert snapshot.consecutive_callback_errors == 1
    assert tracker._latest_pose is good_pose
    tracker._async_watchdog.raise_if_unhealthy()


def test_capture_session_reset_clears_watchdog_but_preserves_timestamp_timeline():
    tracker = _bare_tracker()
    tracker._last_submitted_wire_timestamp_ms = 1234
    tracker._last_submitted_media_timestamp_ms = 9000
    tracker._async_watchdog.record_submission(9000)
    tracker._async_watchdog.record_submission_error(RuntimeError("failed"))

    tracker.reset_session()

    snapshot = tracker._async_watchdog.snapshot()
    assert snapshot.first_submission_ms is None
    assert snapshot.consecutive_submission_errors == 0
    assert tracker._last_submitted_wire_timestamp_ms == 1234
    assert tracker._last_submitted_media_timestamp_ms == 9000
    assert tracker._minimum_result_media_timestamp_ms == 9000


def test_close_is_idempotent_and_blocks_late_callbacks():
    tracker = _bare_tracker()
    image = SimpleNamespace(width=640, height=480)

    tracker.close()
    tracker.close()
    tracker._on_result(object(), image, 1000)

    tracker._landmarker.close.assert_called_once()
    tracker._pose_from_result.assert_not_called()
