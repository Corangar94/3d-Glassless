from __future__ import annotations

from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest

from tracker.camera_frame import (
    CameraFrameFormatError,
    normalize_camera_frame,
)
from tracker.main import TrackingLoop


class _SettingsReader:
    def read(self):
        return None

    def close(self) -> None:
        pass


def test_bgr_uint8_frame_is_preserved_and_made_contiguous():
    source = np.zeros((8, 12, 3), dtype=np.uint8)[:, ::2]
    assert not source.flags.c_contiguous

    normalized = normalize_camera_frame(source)

    assert isinstance(normalized, np.ndarray)
    assert normalized.shape == source.shape
    assert normalized.dtype == np.uint8
    assert normalized.flags.c_contiguous
    assert np.array_equal(normalized, source)


def test_grayscale_and_one_channel_frames_expand_to_bgr():
    gray = np.arange(48, dtype=np.uint8).reshape(6, 8)
    one_channel = gray[..., None]

    gray_bgr = normalize_camera_frame(gray)
    one_bgr = normalize_camera_frame(one_channel)

    assert gray_bgr.shape == (6, 8, 3)
    assert one_bgr.shape == (6, 8, 3)
    assert np.array_equal(gray_bgr[..., 0], gray)
    assert np.array_equal(gray_bgr[..., 0], gray_bgr[..., 1])
    assert np.array_equal(gray_bgr, one_bgr)


def test_bgra_frame_drops_alpha_without_reordering_bgr():
    bgra = np.zeros((2, 3, 4), dtype=np.uint8)
    bgra[..., 0] = 11
    bgra[..., 1] = 22
    bgra[..., 2] = 33
    bgra[..., 3] = 99

    bgr = normalize_camera_frame(bgra)

    assert bgr.shape == (2, 3, 3)
    assert np.all(bgr[..., 0] == 11)
    assert np.all(bgr[..., 1] == 22)
    assert np.all(bgr[..., 2] == 33)


def test_boolean_and_uint16_frames_convert_deterministically():
    boolean = np.array([[False, True]], dtype=np.bool_)
    sixteen_bit = np.array([[0, 65535]], dtype=np.uint16)

    boolean_bgr = normalize_camera_frame(boolean)
    sixteen_bgr = normalize_camera_frame(sixteen_bit)

    assert boolean_bgr[0, 0, 0] == 0
    assert boolean_bgr[0, 1, 0] == 255
    assert sixteen_bgr[0, 0, 0] == 0
    assert sixteen_bgr[0, 1, 0] == 255


def test_umat_is_downloaded_and_normalized():
    gray = np.arange(24, dtype=np.uint8).reshape(4, 6)
    umat = cv2.UMat(gray)

    result = normalize_camera_frame(umat)

    assert isinstance(result, np.ndarray)
    assert result.shape == (4, 6, 3)
    assert result.flags.c_contiguous


def test_opaque_test_double_is_preserved():
    opaque = object()

    assert normalize_camera_frame(opaque) is opaque


@pytest.mark.parametrize(
    "frame",
    [
        np.empty((0, 4, 3), dtype=np.uint8),
        np.empty((4, 0, 3), dtype=np.uint8),
        np.zeros((4,), dtype=np.uint8),
        np.zeros((4, 6, 2), dtype=np.uint8),
        np.zeros((4, 6, 5), dtype=np.uint8),
        np.zeros((4, 6, 3), dtype=np.float32),
        np.zeros((4, 6, 3), dtype=np.int16),
    ],
)
def test_invalid_numpy_formats_fail_closed(frame):
    with pytest.raises(CameraFrameFormatError):
        normalize_camera_frame(frame)


class _Capture:
    def __init__(self, reads) -> None:
        self._reads = iter(reads)
        self.released = False

    def isOpened(self) -> bool:
        return True

    def read(self):
        return next(self._reads)

    def release(self) -> None:
        self.released = True


class _Tracker:
    def __init__(self) -> None:
        self.frames: list[object] = []
        self.reset_count = 0

    def process_frame(self, frame, capture_timestamp_ms=None):
        self.frames.append(frame)
        return None

    def reset_session(self) -> None:
        self.reset_count += 1


class _Smoother:
    def __init__(self) -> None:
        self.reset_count = 0

    def reset(self) -> None:
        self.reset_count += 1

    def set_measurement_noise(self, _value: float) -> None:
        pass

    def update(self, x, y, z, dt_seconds=None):
        return x, y, z


def test_tracking_loop_normalizes_grayscale_before_hooks_and_tracker():
    gray = np.zeros((8, 12), dtype=np.uint8)
    cap = _Capture([(True, gray)])
    tracker = _Tracker()
    loop = TrackingLoop(
        tracker=tracker,
        writer=MagicMock(),
        smoother=_Smoother(),
        hold_ms=0,
    )
    hook_frames: list[object] = []
    loop._on_frame = hook_frames.append

    with (
        patch("tracker.main._open_camera", return_value=cap),
        patch("tracker.main.SharedSettingsReader", return_value=_SettingsReader()),
    ):
        loop.run(camera_index=0, max_frames=1)

    assert len(hook_frames) == 1
    assert isinstance(hook_frames[0], np.ndarray)
    assert hook_frames[0].shape == (8, 12, 3)
    assert tracker.frames[0] is hook_frames[0]


def test_three_incompatible_array_frames_enter_existing_reconnect_path():
    bad = np.zeros((8, 12, 2), dtype=np.uint8)
    recovered = np.zeros((8, 12, 3), dtype=np.uint8)
    first_cap = _Capture([(True, bad), (True, bad), (True, bad)])
    second_cap = _Capture([(True, recovered)])
    tracker = _Tracker()
    smoother = _Smoother()
    loop = TrackingLoop(
        tracker=tracker,
        writer=MagicMock(),
        smoother=smoother,
        hold_ms=0,
    )

    with (
        patch(
            "tracker.main._open_camera",
            side_effect=[first_cap, second_cap],
        ) as open_camera,
        patch("tracker.main.SharedSettingsReader", return_value=_SettingsReader()),
        patch("tracker.main.time.sleep"),
    ):
        loop.run(camera_index=0, max_frames=1)

    assert open_camera.call_count == 2
    assert first_cap.released
    assert tracker.reset_count == 1
    assert smoother.reset_count == 1
    assert len(tracker.frames) == 1
    assert tracker.frames[0].shape == (8, 12, 3)
