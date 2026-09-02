from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
import threading

import numpy as np
import pytest

from tracker.face_tracker import FaceTracker
from tracker.pose import HeadPosition


def _frame(width: int = 1280, height: int = 720) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def _landmarks():
    points = [
        SimpleNamespace(x=0.5, y=0.5, presence=1.0, visibility=1.0)
        for _ in range(474)
    ]
    points[468] = SimpleNamespace(
        x=0.45,
        y=0.46,
        presence=1.0,
        visibility=1.0,
    )
    points[473] = SimpleNamespace(
        x=0.55,
        y=0.46,
        presence=1.0,
        visibility=1.0,
    )
    return points


def _result():
    return SimpleNamespace(
        face_landmarks=[_landmarks()],
        facial_transformation_matrixes=[],
    )


def _bare_tracker(*, max_width: int | None = 960) -> FaceTracker:
    tracker = FaceTracker.__new__(FaceTracker)
    tracker._lock = threading.Lock()
    tracker._real_ipd_cm = 6.3
    tracker._camera_fov_deg = 90.0
    tracker._camera_geometry = None
    if max_width is not None:
        tracker._max_input_width_px = max_width
    return tracker


def test_preparation_resizes_before_rgb_image_conversion(monkeypatch):
    tracker = _bare_tracker(max_width=960)
    received_shapes = []
    monkeypatch.setattr(
        tracker,
        "_mediapipe_image",
        lambda frame_bgr: received_shapes.append(frame_bgr.shape) or "image",
    )

    image, width, height = tracker._prepared_mediapipe_image(_frame())

    assert image == "image"
    assert received_shapes == [(540, 960, 3)]
    assert (width, height) == (960, 540)


def test_bare_legacy_tracker_without_width_setting_keeps_full_resolution(
    monkeypatch,
):
    tracker = _bare_tracker(max_width=None)
    received_shapes = []
    monkeypatch.setattr(
        tracker,
        "_mediapipe_image",
        lambda frame_bgr: received_shapes.append(frame_bgr.shape) or "image",
    )

    _image, width, height = tracker._prepared_mediapipe_image(_frame())

    assert received_shapes == [(720, 1280, 3)]
    assert (width, height) == (1280, 720)
    assert tracker.max_input_width_px == 0


def test_fov_fallback_pose_geometry_is_resolution_invariant():
    tracker = _bare_tracker(max_width=960)

    full = tracker._pose_from_result(_result(), 1280, 720, 1000)
    bounded = tracker._pose_from_result(_result(), 960, 540, 1000)

    assert full is not None
    assert bounded is not None
    assert bounded.xyz == pytest.approx(full.xyz, rel=1e-6, abs=1e-6)
    assert bounded.yaw_deg == pytest.approx(full.yaw_deg)
    assert bounded.pitch_deg == pytest.approx(full.pitch_deg)
    assert bounded.roll_deg == pytest.approx(full.roll_deg)


def test_synchronous_mode_passes_prepared_dimensions_to_pose_geometry(
    monkeypatch,
):
    tracker = _bare_tracker(max_width=960)
    tracker._async_mode = False
    tracker._landmarker = SimpleNamespace(detect=lambda image: "result")
    monkeypatch.setattr(
        tracker,
        "_prepared_mediapipe_image",
        lambda frame: ("prepared-image", 960, 540),
    )
    pose = HeadPosition(
        x_cm=1.0,
        y_cm=2.0,
        z_cm=60.0,
        capture_timestamp_ms=1000,
    )
    conversion = MagicMock(return_value=pose)
    monkeypatch.setattr(tracker, "_pose_from_result", conversion)

    output = tracker.process_frame(_frame(), capture_timestamp_ms=1000)

    assert output is pose
    conversion.assert_called_once_with("result", 960, 540, 1000)


def test_async_mode_submits_prepared_image_after_backlog_admission(monkeypatch):
    tracker = _bare_tracker(max_width=960)
    tracker._async_mode = True
    tracker._last_submitted_wire_timestamp_ms = None
    tracker._last_submitted_media_timestamp_ms = None
    tracker._latest_pose = None
    tracker._last_delivered_timestamp_ms = None
    tracker._async_max_backlog_ms = 150
    tracker._async_watchdog = MagicMock()
    tracker._async_watchdog.should_submit.return_value = True
    tracker._async_result_freshness = None
    tracker._closed = False
    tracker._minimum_result_media_timestamp_ms = None
    tracker._landmarker = MagicMock()
    prepared = MagicMock(return_value=("prepared-image", 960, 540))
    monkeypatch.setattr(tracker, "_prepared_mediapipe_image", prepared)

    output = tracker.process_frame(_frame(), capture_timestamp_ms=1000)

    assert output is None
    prepared.assert_called_once()
    tracker._landmarker.detect_async.assert_called_once_with(
        "prepared-image",
        1000,
    )
    tracker._async_watchdog.should_submit.assert_called_once_with(
        1000,
        max_backlog_ms=150,
    )


def test_backlog_rejection_avoids_resize_conversion_and_allocation(monkeypatch):
    tracker = _bare_tracker(max_width=960)
    tracker._async_mode = True
    tracker._last_submitted_wire_timestamp_ms = 900
    tracker._last_submitted_media_timestamp_ms = 900
    tracker._latest_pose = None
    tracker._last_delivered_timestamp_ms = None
    tracker._async_max_backlog_ms = 150
    tracker._async_watchdog = MagicMock()
    tracker._async_watchdog.should_submit.return_value = False
    tracker._async_result_freshness = None
    tracker._closed = False
    tracker._minimum_result_media_timestamp_ms = None
    tracker._landmarker = MagicMock()
    monkeypatch.setattr(
        tracker,
        "_prepared_mediapipe_image",
        MagicMock(side_effect=AssertionError("preparation should be skipped")),
    )

    output = tracker.process_frame(_frame(), capture_timestamp_ms=1000)

    assert output is None
    tracker._landmarker.detect_async.assert_not_called()
    tracker._async_watchdog.record_throttled_submission.assert_called_once_with()


@pytest.mark.parametrize("value", [-1, 1, 319, 8193, 960.5, True])
def test_invalid_input_cap_fails_before_model_creation(value):
    with pytest.raises(ValueError, match="max_input_width_px"):
        FaceTracker(
            real_ipd_cm=6.3,
            screen_width_cm=60.0,
            screen_height_cm=34.0,
            max_input_width_px=value,
        )
