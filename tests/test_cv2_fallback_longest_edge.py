from __future__ import annotations

import cv2
import numpy as np
import pytest

from tracker.cv2_temporal_tracker import EyePair, FaceBox
from tracker.face_tracker_cv2 import FaceTracker


def _tracker(cap: int = 640) -> FaceTracker:
    tracker = FaceTracker.__new__(FaceTracker)
    tracker._detection_width_px = cap
    return tracker


def _frame(width: int, height: int) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def test_landscape_default_behavior_remains_640_by_360():
    gray, scale = _tracker()._tracking_gray(_frame(1280, 720))

    assert gray.shape == (360, 640)
    assert gray.flags.c_contiguous
    assert scale == pytest.approx(0.5)


def test_rotated_default_camera_is_bounded_to_360_by_640():
    gray, scale = _tracker()._tracking_gray(_frame(720, 1280))

    assert gray.shape == (640, 360)
    assert max(gray.shape) == 640
    assert scale == pytest.approx(0.5)


def test_tall_portrait_camera_cannot_bypass_budget_through_height():
    gray, scale = _tracker()._tracking_gray(_frame(1200, 1600))

    assert gray.shape == (640, 480)
    assert max(gray.shape) == 640
    assert gray.shape[1] / gray.shape[0] == pytest.approx(3.0 / 4.0)
    assert scale == pytest.approx(0.4)


@pytest.mark.parametrize("width,height", [(640, 480), (480, 640), (320, 240)])
def test_frames_with_both_edges_inside_cap_are_not_resized(
    width: int,
    height: int,
    monkeypatch,
):
    frame = _frame(width, height)
    real_cvt_color = cv2.cvtColor
    resize_calls: list[object] = []

    monkeypatch.setattr(
        "tracker.face_tracker_cv2.cv2.resize",
        lambda *args, **kwargs: resize_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "tracker.face_tracker_cv2.cv2.cvtColor",
        real_cvt_color,
    )

    gray, scale = _tracker()._tracking_gray(frame)

    assert gray.shape == (height, width)
    assert scale == pytest.approx(1.0)
    assert resize_calls == []


def test_downscale_uses_area_interpolation(monkeypatch):
    frame = _frame(720, 1280)
    calls: list[tuple[tuple[int, int], int]] = []
    real_resize = cv2.resize

    def recording_resize(source, size, *, interpolation):
        calls.append((size, interpolation))
        return real_resize(source, size, interpolation=interpolation)

    monkeypatch.setattr(
        "tracker.face_tracker_cv2.cv2.resize",
        recording_resize,
    )

    gray, _scale = _tracker()._tracking_gray(frame)

    assert gray.shape == (640, 360)
    assert calls == [((360, 640), cv2.INTER_AREA)]


def test_uniform_scale_maps_boxes_and_eyes_back_to_original_pixels():
    scale = 0.5
    box = FaceBox(50.0, 80.0, 120.0, 160.0)
    eyes = EyePair((80.0, 120.0), (140.0, 120.0))

    original_box = FaceTracker._original_box(box, scale)
    original_eyes = FaceTracker._original_eyes(eyes, scale)

    assert original_box == FaceBox(100.0, 160.0, 240.0, 320.0)
    assert original_eyes == EyePair((160.0, 240.0), (280.0, 240.0))


def test_rotated_frame_uses_about_one_third_of_previous_width_only_pixels():
    # Before this change a 720x1280 frame became roughly 640x1138 because only
    # width was capped. Longest-edge scaling produces 360x640 instead.
    gray, _scale = _tracker()._tracking_gray(_frame(720, 1280))
    previous_width_only_pixels = 640 * round(1280 * (640 / 720))

    assert gray.size == 360 * 640
    assert gray.size / previous_width_only_pixels == pytest.approx(
        0.3163,
        abs=0.001,
    )
