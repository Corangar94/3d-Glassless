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


def _install_control_ids(monkeypatch):
    values = {
        "CAP_PROP_AUTOFOCUS": 1001,
        "CAP_PROP_FOCUS": 1002,
        "CAP_PROP_AUTO_EXPOSURE": 1003,
        "CAP_PROP_EXPOSURE": 1004,
    }
    for name, value in values.items():
        monkeypatch.setattr(cv2, name, value, raising=False)
    return values


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
    ids = _install_control_ids(monkeypatch)
    cap = MagicMock()
    cap.get.side_effect = lambda property_id: (
        42.0 if property_id == ids["CAP_PROP_FOCUS"] else -6.0
    )
    cap.set.return_value = True

    result = try_lock_camera_controls(cap)

    assert result["autofocus_locked"] is True
    assert result["focus_preserved"] is True
    assert result["auto_exposure_locked"] is True
    assert result["exposure_preserved"] is True
    assert "errors" not in result


def test_none_control_values_do_not_crash_or_get_written_back(monkeypatch):
    ids = _install_control_ids(monkeypatch)
    cap = MagicMock()
    cap.get.return_value = None
    cap.set.return_value = True

    result = try_lock_camera_controls(cap)

    assert result["focus_value"] is None
    assert result["exposure_value"] is None
    assert result["autofocus_locked"] is True
    assert result["auto_exposure_locked"] is True
    assert result["focus_preserved"] is False
    assert result["exposure_preserved"] is False
    written_properties = [call.args[0] for call in cap.set.call_args_list]
    assert ids["CAP_PROP_FOCUS"] not in written_properties
    assert ids["CAP_PROP_EXPOSURE"] not in written_properties
    assert any("focus read failed" in error for error in result["errors"])
    assert any("exposure read failed" in error for error in result["errors"])


def test_backend_get_and_set_exceptions_are_reported_not_raised(monkeypatch):
    _install_control_ids(monkeypatch)
    cap = MagicMock()
    cap.get.side_effect = RuntimeError("unsupported get")
    cap.set.side_effect = RuntimeError("unsupported set")

    result = try_lock_camera_controls(cap)

    assert result["autofocus_locked"] is False
    assert result["auto_exposure_locked"] is False
    assert result["focus_preserved"] is False
    assert result["exposure_preserved"] is False
    assert len(result["errors"]) >= 5
    assert all("RuntimeError" in error for error in result["errors"])


def test_nonfinite_values_are_not_restored(monkeypatch):
    ids = _install_control_ids(monkeypatch)
    cap = MagicMock()
    cap.get.return_value = float("nan")
    cap.set.return_value = True

    result = try_lock_camera_controls(cap)

    assert result["focus_value"] is None
    assert result["exposure_value"] is None
    assert result["focus_preserved"] is False
    assert result["exposure_preserved"] is False
    written_properties = [call.args[0] for call in cap.set.call_args_list]
    assert ids["CAP_PROP_FOCUS"] not in written_properties
    assert ids["CAP_PROP_EXPOSURE"] not in written_properties
    assert any("non-finite" in error for error in result["errors"])


def test_auto_exposure_uses_backend_fallback_before_restoring_value(monkeypatch):
    ids = _install_control_ids(monkeypatch)
    cap = MagicMock()
    cap.get.side_effect = lambda property_id: (
        42.0 if property_id == ids["CAP_PROP_FOCUS"] else -6.0
    )

    def set_control(property_id, value):
        if property_id == ids["CAP_PROP_AUTO_EXPOSURE"]:
            return value == 0.0
        return True

    cap.set.side_effect = set_control

    result = try_lock_camera_controls(cap)

    assert result["auto_exposure_locked"] is True
    assert result["exposure_preserved"] is True
    auto_values = [
        call.args[1]
        for call in cap.set.call_args_list
        if call.args[0] == ids["CAP_PROP_AUTO_EXPOSURE"]
    ]
    assert auto_values == [0.25, 0.0]
    assert (ids["CAP_PROP_EXPOSURE"], -6.0) in [
        tuple(call.args) for call in cap.set.call_args_list
    ]


def test_tracker_runtime_integrates_quality_monitor_and_opt_in_lock():
    source = open("tracker/main.py", encoding="utf-8").read()

    assert "CameraQualityMonitor" in source
    assert "camera_quality.stable_for_lock" in source
    assert "try_lock_camera_controls(cap)" in source
    assert 'lock_controls_after_warmup' in source
