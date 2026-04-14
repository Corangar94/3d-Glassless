# tests/test_tracker_thread.py
import threading
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QSignalSpy

from launcher.tracker_thread import TrackerThread

CONFIG = {
    "camera": {"index": 0},
    "screen": {"width_cm": 59.8, "height_cm": 33.6},
    "tracking": {
        "ipd_cm": 6.3,
        "smoothing_q": 0.01,
        "smoothing_r": 0.1,
        "hold_ms": 500,
    },
}


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    return app


def _make_mock_cap(frames=3):
    cap = MagicMock()
    cap.isOpened.return_value = True
    frame = MagicMock()
    reads = [(True, frame)] * frames + [(False, None)]
    cap.read.side_effect = reads
    return cap


def _spy_list(spy: QSignalSpy) -> list:
    """Convert QSignalSpy to a plain list (handles PySide6 6.x API)."""
    return [spy.at(i) for i in range(spy.count())]


def test_tracker_thread_emits_position_updated(qapp):
    mock_cap = _make_mock_cap(frames=2)
    mock_face_pos = MagicMock()
    mock_face_pos.x_cm = 1.0
    mock_face_pos.y_cm = 0.0
    mock_face_pos.z_cm = 60.0

    with (
        patch("launcher.tracker_thread.cv2.VideoCapture", return_value=mock_cap),
        patch("launcher.tracker_thread.FaceTracker") as MockFT,
        patch("launcher.tracker_thread.FreetracWriter") as MockFW,
        patch("launcher.tracker_thread.HeadSmoother") as MockHS,
    ):
        ft_instance = MockFT.return_value.__enter__.return_value
        ft_instance.process_frame.return_value = mock_face_pos
        MockFW.return_value.__enter__.return_value = MagicMock()
        hs_instance = MockHS.return_value
        hs_instance.update.return_value = (1.0, 0.0, 60.0)

        thread = TrackerThread(camera_index=0, config=CONFIG)
        spy = QSignalSpy(thread.position_updated)
        thread.start()
        thread.wait(2000)

    emissions = _spy_list(spy)
    assert len(emissions) >= 1
    first = emissions[0]
    assert first[0] == pytest.approx(1.0)


def test_tracker_thread_emits_status_changed_tracking(qapp):
    mock_cap = _make_mock_cap(frames=1)
    mock_face_pos = MagicMock()
    mock_face_pos.x_cm = 0.0
    mock_face_pos.y_cm = 0.0
    mock_face_pos.z_cm = 60.0

    with (
        patch("launcher.tracker_thread.cv2.VideoCapture", return_value=mock_cap),
        patch("launcher.tracker_thread.FaceTracker") as MockFT,
        patch("launcher.tracker_thread.FreetracWriter") as MockFW,
        patch("launcher.tracker_thread.HeadSmoother") as MockHS,
    ):
        MockFT.return_value.__enter__.return_value.process_frame.return_value = mock_face_pos
        MockFW.return_value.__enter__.return_value = MagicMock()
        MockHS.return_value.update.return_value = (0.0, 0.0, 60.0)

        thread = TrackerThread(camera_index=0, config=CONFIG)
        spy = QSignalSpy(thread.status_changed)
        thread.start()
        thread.wait(2000)

    statuses = [s[0] for s in _spy_list(spy)]
    assert "tracking" in statuses


def test_tracker_thread_stop_terminates_thread(qapp):
    # Cap that reads forever until stop event
    cap = MagicMock()
    cap.isOpened.return_value = True
    cap.read.return_value = (True, MagicMock())

    with (
        patch("launcher.tracker_thread.cv2.VideoCapture", return_value=cap),
        patch("launcher.tracker_thread.FaceTracker") as MockFT,
        patch("launcher.tracker_thread.FreetracWriter") as MockFW,
        patch("launcher.tracker_thread.HeadSmoother") as MockHS,
    ):
        MockFT.return_value.__enter__.return_value.process_frame.return_value = None
        MockFW.return_value.__enter__.return_value = MagicMock()
        MockHS.return_value.update.return_value = (0.0, 0.0, 60.0)

        thread = TrackerThread(camera_index=0, config=CONFIG)
        thread.start()
        assert thread.isRunning()
        thread.stop()
        assert not thread.isRunning()


def test_tracker_thread_emits_error_status_on_camera_failure(qapp):
    cap = MagicMock()
    cap.isOpened.return_value = False

    with (
        patch("launcher.tracker_thread.cv2.VideoCapture", return_value=cap),
        patch("launcher.tracker_thread.FaceTracker") as MockFT,
        patch("launcher.tracker_thread.FreetracWriter") as MockFW,
        patch("launcher.tracker_thread.HeadSmoother"),
    ):
        MockFT.return_value.__enter__.return_value = MagicMock()
        MockFW.return_value.__enter__.return_value = MagicMock()

        thread = TrackerThread(camera_index=0, config=CONFIG)
        spy = QSignalSpy(thread.status_changed)
        thread.start()
        thread.wait(2000)

    statuses = [s[0] for s in _spy_list(spy)]
    assert "error" in statuses
