from __future__ import annotations

from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

from tracker.camera_quality import CameraQualityMonitor


def _textured_frame(brightness: int = 120) -> np.ndarray:
    frame = np.full((240, 320, 3), brightness, dtype=np.uint8)
    for x in range(0, 320, 16):
        cv2.line(frame, (x, 0), (x, 239), (255, 255, 255), 2)
    for y in range(0, 240, 16):
        cv2.line(frame, (0, y), (319, y), (0, 0, 0), 2)
    return frame


def test_image_analysis_is_sampled_while_fps_uses_every_frame(monkeypatch):
    monitor = CameraQualityMonitor(
        window_size=30,
        minimum_sharpness=1.0,
        minimum_fps=20.0,
        analysis_interval_ms=80,
    )
    original = monitor._analyze_frame
    analyze = MagicMock(side_effect=original)
    monkeypatch.setattr(monitor, "_analyze_frame", analyze)
    frame = _textured_frame()

    for index in range(10):
        status = monitor.update(frame, index * 33)

    assert analyze.call_count == 4  # frames at 0, 99, 198, and 297 ms
    assert monitor.image_analysis_count == 4
    assert status.fps is not None and status.fps > 29.0
    assert len(monitor._samples) == 10
    assert sum(sample.analyzed for sample in monitor._samples) == 4


def test_skipped_frames_reuse_latest_metrics_without_touching_pixels(monkeypatch):
    monitor = CameraQualityMonitor(
        minimum_sharpness=0.0,
        minimum_fps=1.0,
        analysis_interval_ms=80,
    )
    dark = np.full((120, 160, 3), 20, dtype=np.uint8)
    bright = np.full((120, 160, 3), 220, dtype=np.uint8)
    first = monitor.update(dark, 0)

    def fail_if_analyzed(_frame):
        raise AssertionError("image analysis ran before its interval")

    monkeypatch.setattr(monitor, "_analyze_frame", fail_if_analyzed)
    second = monitor.update(bright, 33)

    assert second.brightness == pytest.approx(first.brightness)
    assert monitor._samples[-1].analyzed is False
    assert monitor.image_analysis_count == 1


def test_new_image_metrics_are_observed_at_the_next_due_sample():
    monitor = CameraQualityMonitor(
        window_size=8,
        minimum_sharpness=0.0,
        minimum_fps=1.0,
        analysis_interval_ms=80,
    )
    dark = np.full((120, 160, 3), 20, dtype=np.uint8)
    bright = np.full((120, 160, 3), 220, dtype=np.uint8)

    monitor.update(dark, 0)
    monitor.update(bright, 33)
    monitor.update(bright, 66)
    monitor.update(bright, 99)

    assert monitor.image_analysis_count == 2
    assert monitor._samples[-1].analyzed is True
    assert monitor._samples[-1].brightness > 0.8


def test_analysis_schedule_is_wrap_safe():
    monitor = CameraQualityMonitor(
        minimum_sharpness=0.0,
        minimum_fps=1.0,
        analysis_interval_ms=80,
    )
    frame = _textured_frame()
    start = 0xFFFF_FFD0

    monitor.update(frame, start)
    monitor.update(frame, 20)  # 68 ms after start across rollover
    monitor.update(frame, 80)  # 128 ms after start across rollover

    assert [sample.analyzed for sample in monitor._samples] == [True, False, True]
    assert monitor.image_analysis_count == 2


def test_zero_interval_preserves_every_frame_analysis_mode():
    monitor = CameraQualityMonitor(
        minimum_sharpness=0.0,
        minimum_fps=1.0,
        analysis_interval_ms=0,
    )
    frame = _textured_frame()

    for index in range(5):
        monitor.update(frame, index * 33)

    assert monitor.image_analysis_count == 5
    assert all(sample.analyzed for sample in monitor._samples)


def test_control_lock_requires_real_image_samples_not_duplicated_frames():
    monitor = CameraQualityMonitor(
        window_size=30,
        minimum_sharpness=1.0,
        minimum_fps=20.0,
        analysis_interval_ms=1_000,
    )
    frame = _textured_frame()

    for index in range(35):
        status = monitor.update(frame, index * 33)

    assert status.quality == "GOOD"
    assert status.fps is not None and status.fps > 29.0
    assert monitor.image_analysis_count == 2
    assert not status.stable_for_lock


def test_default_interval_still_reaches_stable_lock_after_warmup():
    monitor = CameraQualityMonitor(
        window_size=30,
        minimum_sharpness=1.0,
        minimum_fps=20.0,
    )
    frame = _textured_frame()

    for index in range(35):
        status = monitor.update(frame, index * 33)

    assert monitor.analysis_interval_ms == 80
    assert monitor.image_analysis_count >= 10
    assert status.stable_for_lock


def test_reset_clears_sampling_and_cadence_state():
    monitor = CameraQualityMonitor(analysis_interval_ms=80)
    frame = _textured_frame()
    monitor.update(frame, 1000)
    monitor.update(frame, 1033)

    monitor.reset()

    assert monitor.image_analysis_count == 0
    assert monitor.status().quality == "UNKNOWN"
    first = monitor.update(frame, 5000)
    assert first.fps is None
    assert monitor._samples[-1].analyzed is True


def test_negative_analysis_interval_fails_closed():
    with pytest.raises(ValueError):
        CameraQualityMonitor(analysis_interval_ms=-1)
