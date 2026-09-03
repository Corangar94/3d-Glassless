from __future__ import annotations

import cv2
import numpy as np
import pytest

from tracker.camera_quality import CameraQualityMonitor


def _frame(width: int, height: int, value: int = 127) -> np.ndarray:
    return np.full((height, width, 3), value, dtype=np.uint8)


def test_landscape_analysis_keeps_existing_320_by_180_budget():
    reduced = CameraQualityMonitor._reduced_analysis_frame(
        _frame(1280, 720)
    )

    assert reduced.shape == (180, 320, 3)
    assert reduced.flags.c_contiguous


def test_rotated_analysis_is_bounded_to_180_by_320():
    reduced = CameraQualityMonitor._reduced_analysis_frame(
        _frame(720, 1280)
    )

    assert reduced.shape == (320, 180, 3)
    assert max(reduced.shape[:2]) == 320


def test_tall_portrait_frame_cannot_bypass_analysis_budget():
    reduced = CameraQualityMonitor._reduced_analysis_frame(
        _frame(1200, 1600)
    )

    assert reduced.shape == (320, 240, 3)
    assert max(reduced.shape[:2]) == 320
    assert reduced.shape[1] / reduced.shape[0] == pytest.approx(3.0 / 4.0)


@pytest.mark.parametrize(
    "width,height",
    [(320, 180), (180, 320), (160, 120), (120, 160)],
)
def test_frames_with_both_edges_inside_budget_are_not_resized(
    width: int,
    height: int,
):
    frame = _frame(width, height)

    reduced = CameraQualityMonitor._reduced_analysis_frame(frame)

    assert reduced is frame


def test_downscale_uses_area_interpolation(monkeypatch):
    frame = _frame(720, 1280)
    calls: list[tuple[object, tuple[int, int], int]] = []
    real_resize = cv2.resize

    def recording_resize(source, size, *, interpolation):
        calls.append((source, size, interpolation))
        return real_resize(source, size, interpolation=interpolation)

    monkeypatch.setattr("tracker.camera_quality.cv2.resize", recording_resize)

    reduced = CameraQualityMonitor._reduced_analysis_frame(frame)

    assert reduced.shape == (320, 180, 3)
    assert calls == [(frame, (180, 320), cv2.INTER_AREA)]


def test_constant_frame_metrics_are_orientation_invariant():
    landscape = CameraQualityMonitor._analyze_frame(
        _frame(1280, 720, 96)
    )
    portrait = CameraQualityMonitor._analyze_frame(
        _frame(720, 1280, 96)
    )

    assert landscape == pytest.approx(portrait)
    brightness, dark_fraction, clipped_fraction, sharpness = landscape
    assert brightness == pytest.approx(96 / 255.0)
    assert dark_fraction == 0.0
    assert clipped_fraction == 0.0
    assert sharpness == 0.0


def test_rotated_analysis_uses_about_one_third_of_width_only_pixels():
    reduced = CameraQualityMonitor._reduced_analysis_frame(
        _frame(720, 1280)
    )
    previous_width_only_pixels = 320 * round(1280 * (320 / 720))

    assert reduced.shape[0] * reduced.shape[1] == 180 * 320
    assert (
        reduced.shape[0] * reduced.shape[1] / previous_width_only_pixels
        == pytest.approx(0.3164, abs=0.001)
    )


def test_update_keeps_original_cadence_and_quality_contract():
    monitor = CameraQualityMonitor(
        window_size=8,
        minimum_sharpness=0.0,
        minimum_fps=20.0,
        analysis_interval_ms=80,
    )
    frame = _frame(720, 1280, 96)

    first = monitor.update(frame, 1000)
    second = monitor.update(frame, 1033)

    assert monitor.image_analysis_count == 1
    assert first.brightness == pytest.approx(96 / 255.0)
    assert second.brightness == pytest.approx(first.brightness)
    assert second.fps == pytest.approx(1000.0 / 33.0)
