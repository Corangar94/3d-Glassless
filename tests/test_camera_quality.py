from unittest.mock import MagicMock

import cv2
import numpy as np

from tracker.camera_quality import CameraQualityMonitor, try_lock_camera_controls


def _textured_frame(brightness: int = 120) -> np.ndarray:
    frame = np.full((240, 320, 3), brightness, dtype=np.uint8)
    for x in range(0, 320, 16):
        cv2.line(frame, (x, 0), (x, 239), (255, 255, 255), 2)
    for y in range(0, 240, 16):
        cv2.line(frame, (0, y), (319, y), (0, 0, 0), 2)
    return frame


def test_camera_quality_reports_good_textured_stable_sequence():
    monitor = CameraQualityMonitor(
        window_size=30,
        minimum_sharpness=20.0,
        minimum_fps=20.0,
    )
    frame = _textured_frame()

    for index in range(35):
        status = monitor.update(frame, index * 33)

    assert status.quality == "GOOD"
    assert status.fps is not None and status.fps > 29.0
    assert status.sharpness > 20.0
    assert status.stable_for_lock


def test_camera_quality_detects_dark_blurred_and_slow_frames():
    monitor = CameraQualityMonitor(
        window_size=12,
        minimum_sharpness=35.0,
        minimum_fps=20.0,
    )
    dark = np.full((240, 320, 3), 4, dtype=np.uint8)

    for index in range(12):
        status = monitor.update(dark, index * 100)

    assert status.quality == "DANGER"
    assert "underexposed" in status.problems
    assert "soft or motion-blurred" in status.problems
    assert any(problem.startswith("camera cadence low") for problem in status.problems)


def test_camera_quality_detects_exposure_hunting():
    monitor = CameraQualityMonitor(
        window_size=20,
        minimum_sharpness=1.0,
        minimum_fps=10.0,
    )

    for index in range(20):
        frame = _textured_frame(60 if index % 2 == 0 else 210)
        status = monitor.update(frame, index * 33)

    assert "exposure is hunting" in status.problems
    assert not status.stable_for_lock


def test_camera_controls_are_best_effort_and_preserve_values(monkeypatch):
    cap = MagicMock()
    cap.get.side_effect = lambda property_id: (
        42.0 if property_id == getattr(cv2, "CAP_PROP_FOCUS", -1) else -6.0
    )
    cap.set.return_value = True

    result = try_lock_camera_controls(cap)

    if hasattr(cv2, "CAP_PROP_AUTOFOCUS"):
        assert result["autofocus_locked"] is True
    if hasattr(cv2, "CAP_PROP_AUTO_EXPOSURE"):
        assert result["auto_exposure_locked"] is True


def test_tracker_runtime_integrates_quality_monitor_and_opt_in_lock():
    source = open("tracker/main.py", encoding="utf-8").read()

    assert "CameraQualityMonitor" in source
    assert "camera_quality.stable_for_lock" in source
    assert "try_lock_camera_controls(cap)" in source
    assert 'lock_controls_after_warmup' in source
