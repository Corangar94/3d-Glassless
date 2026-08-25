# tests/test_calibration.py
import numpy as np
import pytest
from unittest.mock import patch, MagicMock
from launcher.calibration import (
    detect_screen_cm,
    measure_head_distance,
    measure_head_distance_or_none,
    _detect_face_distance,
)


def test_detect_screen_cm_returns_floats():
    w, h = detect_screen_cm()
    assert isinstance(w, float) and isinstance(h, float)
    assert w >= 0.0 and h >= 0.0


def test_detect_screen_cm_nonzero_on_real_monitor():
    w, h = detect_screen_cm()
    if w == 0.0 and h == 0.0:
        pytest.skip("No physical monitor detected (headless/CI)")
    assert w > 10.0 and h > 5.0


def _make_mock_cv2(cap: MagicMock) -> MagicMock:
    """Build a cv2 mock whose VideoCapture returns `cap`."""
    mock = MagicMock()
    mock.VideoCapture.return_value = cap
    mock.COLOR_BGR2RGB = 4  # cv2.COLOR_BGR2RGB == 4
    mock.cvtColor.side_effect = lambda src, _code: src  # return frame unchanged
    return mock


def test_measure_head_distance_no_camera():
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = False
    mock_cv2 = _make_mock_cv2(mock_cap)
    with patch.dict("sys.modules", {"cv2": mock_cv2}):
        assert measure_head_distance(ipd_mm=64.0) == 60.0


def test_measure_head_distance_or_none_releases_unopened_camera():
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = False
    mock_cv2 = _make_mock_cv2(mock_cap)
    with patch.dict("sys.modules", {"cv2": mock_cv2}):
        assert measure_head_distance_or_none(ipd_mm=64.0) is None
    mock_cap.release.assert_called_once()


def test_measure_head_distance_no_face():
    fake_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.read.return_value = (True, fake_frame)
    mock_cv2 = _make_mock_cv2(mock_cap)
    with patch.dict("sys.modules", {"cv2": mock_cv2}):
        with (
            patch("launcher.calibration._create_face_landmarker") as create,
            patch(
                "launcher.calibration._detect_face_distance_with_landmarker",
                return_value=None,
            ),
        ):
            create.return_value.__enter__.return_value = MagicMock()
            assert measure_head_distance(ipd_mm=64.0) == 60.0


def test_detect_face_distance_returns_none_on_no_face():
    """_detect_face_distance returns None when MediaPipe finds no landmarks."""
    fake_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    mock_result = MagicMock()
    mock_result.face_landmarks = []

    mock_lmk = MagicMock()
    mock_lmk.__enter__ = MagicMock(return_value=mock_lmk)
    mock_lmk.__exit__ = MagicMock(return_value=False)
    mock_lmk.detect.return_value = mock_result

    mock_cv2 = MagicMock()
    mock_cv2.cvtColor.return_value = fake_frame
    mock_cv2.COLOR_BGR2RGB = 4  # cv2.COLOR_BGR2RGB == 4

    mock_image_module = MagicMock()
    mock_image_module.Image = MagicMock()
    mock_image_module.ImageFormat.SRGB = "SRGB"

    # Calibration now imports only the exact Face Landmarker modules. Patch the
    # landmarker factory seam and the narrow image module instead of replacing
    # `mediapipe.tasks` with a non-package mock.
    with (
        patch("launcher.calibration._create_face_landmarker", return_value=mock_lmk),
        patch.dict(
            "sys.modules",
            {
                "cv2": mock_cv2,
                "mediapipe.tasks.python.vision.core.image": mock_image_module,
            },
        ),
    ):
        result = _detect_face_distance(fake_frame, ipd_mm=64.0)

    assert result is None
