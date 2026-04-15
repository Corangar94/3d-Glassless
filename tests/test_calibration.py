# tests/test_calibration.py
import numpy as np
import pytest
from unittest.mock import patch, MagicMock
from launcher.calibration import detect_screen_cm, measure_head_distance

def test_detect_screen_cm_returns_floats():
    w, h = detect_screen_cm()
    assert isinstance(w, float) and isinstance(h, float)
    assert w >= 0.0 and h >= 0.0

def test_detect_screen_cm_nonzero_on_real_monitor():
    w, h = detect_screen_cm()
    if w == 0.0 and h == 0.0:
        pytest.skip("No physical monitor detected (headless/CI)")
    assert w > 10.0 and h > 5.0

def test_measure_head_distance_no_camera():
    import cv2
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = False
    with patch.object(cv2, "VideoCapture", return_value=mock_cap):
        assert measure_head_distance(ipd_mm=64.0) == 60.0

def test_measure_head_distance_no_face():
    import cv2
    fake_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.read.return_value = (True, fake_frame)
    with patch.object(cv2, "VideoCapture", return_value=mock_cap):
        with patch("launcher.calibration._detect_face_distance", return_value=None):
            assert measure_head_distance(ipd_mm=64.0) == 60.0
