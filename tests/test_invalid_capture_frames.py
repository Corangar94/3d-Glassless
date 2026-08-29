from __future__ import annotations

from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from tracker.main import TrackingLoop
from tracker.safe_video_capture import (
    InvalidCaptureFrameError,
    SafeVideoCapture,
)


class _NativeCapture:
    def __init__(self, results) -> None:
        self._results = iter(results)
        self.release_calls = 0

    def isOpened(self) -> bool:
        return True

    def read(self):
        return next(self._results)

    def retrieve(self):
        return next(self._results)

    def release(self) -> None:
        self.release_calls += 1


class _BrokenShape:
    @property
    def shape(self):
        raise RuntimeError("shape metadata unavailable")


class _OpaqueFrame:
    pass


class _SettingsReader:
    def read(self):
        return None

    def close(self) -> None:
        pass


def _safe(*results) -> SafeVideoCapture:
    native = _NativeCapture(results)
    return SafeVideoCapture(_factory=lambda: native)


def test_success_with_null_frame_fails_closed():
    cap = _safe((True, None))

    assert cap.read() == (False, None)
    assert cap.failures[-1].stage == "read"
    assert cap.failures[-1].error_type == "InvalidCaptureFrameError"


def test_zero_height_zero_width_and_one_dimensional_frames_fail_closed():
    invalid_frames = (
        np.empty((0, 640, 3), dtype=np.uint8),
        np.empty((480, 0, 3), dtype=np.uint8),
        np.empty((32,), dtype=np.uint8),
    )

    for frame in invalid_frames:
        cap = _safe((True, frame))
        assert cap.read() == (False, None)
        assert cap.failures[-1].error_type == "InvalidCaptureFrameError"


def test_valid_grayscale_and_color_numpy_frames_pass():
    grayscale = np.zeros((8, 12), dtype=np.uint8)
    color = np.zeros((8, 12, 3), dtype=np.uint8)

    gray_cap = _safe((True, grayscale))
    color_cap = _safe((True, color))

    assert gray_cap.read() == (True, grayscale)
    assert color_cap.read() == (True, color)
    assert gray_cap.failures == ()
    assert color_cap.failures == ()


def test_failed_status_never_propagates_a_stale_frame_object():
    stale = np.ones((4, 4, 3), dtype=np.uint8)
    cap = _safe((False, stale))

    assert cap.read() == (False, None)
    assert cap.failures == ()


def test_opaque_frame_types_remain_compatible():
    opaque = _OpaqueFrame()
    cap = _safe((True, opaque))

    assert cap.read() == (True, opaque)
    assert cap.failures == ()


def test_unreadable_shape_metadata_fails_closed():
    cap = _safe((True, _BrokenShape()))

    assert cap.read() == (False, None)
    assert cap.failures[-1].error_type == "InvalidCaptureFrameError"


def test_retrieve_applies_the_same_frame_validation():
    cap = _safe((True, np.empty((0, 1), dtype=np.uint8)))

    assert cap.retrieve() == (False, None)
    assert cap.failures[-1].stage == "retrieve"
    assert cap.failures[-1].error_type == "InvalidCaptureFrameError"


def test_cv2_umat_is_not_rejected_for_lacking_numpy_metadata():
    frame = cv2.UMat(np.zeros((4, 6, 3), dtype=np.uint8))
    cap = _safe((True, frame))

    ok, returned = cap.read()

    assert ok
    assert returned is frame
    assert cap.failures == ()


def test_invalid_success_frames_enter_existing_reconnect_path():
    invalid_native = _NativeCapture(
        [(True, None), (True, None), (True, None)]
    )
    recovered_frame = np.zeros((8, 12, 3), dtype=np.uint8)
    recovered_native = _NativeCapture([(True, recovered_frame)])
    invalid = SafeVideoCapture(_factory=lambda: invalid_native)
    recovered = SafeVideoCapture(_factory=lambda: recovered_native)
    tracker = MagicMock()
    tracker.process_frame.return_value = None
    smoother = MagicMock()
    loop = TrackingLoop(
        tracker=tracker,
        writer=MagicMock(),
        smoother=smoother,
        hold_ms=0,
    )

    with (
        patch("tracker.main._open_camera", side_effect=[invalid, recovered]),
        patch("tracker.main.SharedSettingsReader", return_value=_SettingsReader()),
        patch("tracker.main.time.sleep"),
    ):
        loop.run(camera_index=0, max_frames=1)

    assert invalid_native.release_calls == 1
    assert tracker.reset_session.call_count == 1
    assert smoother.reset.call_count == 1
    assert tracker.process_frame.call_count == 1


def test_invalid_frame_error_is_distinct_from_backend_exception():
    assert issubclass(InvalidCaptureFrameError, RuntimeError)
