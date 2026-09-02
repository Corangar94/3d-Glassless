from __future__ import annotations

import numpy as np
import pytest

from tracker import mediapipe_input
from tracker.mediapipe_input import prepare_mediapipe_bgr_frame


def _frame(width: int, height: int) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def test_default_camera_size_is_reduced_to_960_by_540():
    frame = _frame(1280, 720)

    prepared = prepare_mediapipe_bgr_frame(frame, 960)

    assert prepared.resized
    assert prepared.frame_bgr.shape == (540, 960, 3)
    assert prepared.width == 960
    assert prepared.height == 540
    assert prepared.scale == pytest.approx(0.75)
    assert prepared.pixel_count == 518_400
    assert prepared.pixel_count / frame.shape[0] / frame.shape[1] == pytest.approx(
        0.5625
    )


def test_smaller_frame_is_not_upscaled_or_copied():
    frame = _frame(640, 480)

    prepared = prepare_mediapipe_bgr_frame(frame, 960)

    assert not prepared.resized
    assert prepared.frame_bgr is frame
    assert (prepared.width, prepared.height) == (640, 480)
    assert prepared.scale == pytest.approx(1.0)


def test_zero_cap_preserves_full_resolution():
    frame = _frame(3840, 2160)

    prepared = prepare_mediapipe_bgr_frame(frame, 0)

    assert not prepared.resized
    assert prepared.frame_bgr is frame
    assert (prepared.width, prepared.height) == (3840, 2160)


def test_arbitrary_aspect_ratio_is_preserved_by_rounded_height():
    frame = _frame(1600, 1200)

    prepared = prepare_mediapipe_bgr_frame(frame, 960)

    assert prepared.frame_bgr.shape == (720, 960, 3)
    assert prepared.width / prepared.height == pytest.approx(4.0 / 3.0)


def test_portrait_frame_caps_width_without_forcing_landscape():
    frame = _frame(1200, 1600)

    prepared = prepare_mediapipe_bgr_frame(frame, 960)

    assert prepared.frame_bgr.shape == (1280, 960, 3)
    assert prepared.width == 960
    assert prepared.height == 1280


def test_resize_uses_area_interpolation(monkeypatch):
    frame = _frame(1280, 720)
    calls = []
    real_resize = mediapipe_input.cv2.resize

    def recording_resize(source, size, *, interpolation):
        calls.append((source, size, interpolation))
        return real_resize(source, size, interpolation=interpolation)

    monkeypatch.setattr(mediapipe_input.cv2, "resize", recording_resize)

    prepared = prepare_mediapipe_bgr_frame(frame, 960)

    assert prepared.resized
    assert len(calls) == 1
    source, size, interpolation = calls[0]
    assert source is frame
    assert size == (960, 540)
    assert interpolation == mediapipe_input.cv2.INTER_AREA


@pytest.mark.parametrize(
    "frame",
    [
        object(),
        np.zeros((0, 640, 3), dtype=np.uint8),
        np.zeros((480, 0, 3), dtype=np.uint8),
        np.zeros((480, 640), dtype=np.uint8),
        np.zeros((480, 640, 1), dtype=np.uint8),
        np.zeros((480, 640, 4), dtype=np.uint8),
    ],
)
def test_invalid_frames_fail_before_resize(frame):
    with pytest.raises((TypeError, ValueError)):
        prepare_mediapipe_bgr_frame(frame, 960)


def test_invalid_resize_result_fails_closed(monkeypatch):
    frame = _frame(1280, 720)
    monkeypatch.setattr(
        mediapipe_input.cv2,
        "resize",
        lambda *_args, **_kwargs: np.zeros((1, 1, 3), dtype=np.uint8),
    )

    with pytest.raises(ValueError, match="resize returned an invalid frame"):
        prepare_mediapipe_bgr_frame(frame, 960)
