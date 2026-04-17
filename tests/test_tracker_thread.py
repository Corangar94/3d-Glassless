# tests/test_tracker_thread.py
import threading
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

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
    app = QApplication.instance() or QApplication([])
    return app


def _make_mock_cap(frames=3):
    cap = MagicMock()
    cap.isOpened.return_value = True
    frame = MagicMock()
    # _SignallingLoop verifies stream health with up to 15 reads before
    # entering the status loop, so include preflight frames in the mock.
    reads = [(True, frame)] * (15 + frames) + [(False, None)]
    cap.read.side_effect = reads
    return cap


def _mock_settings_reader():
    """Return a MagicMock that works both as a plain instance and context manager."""
    m = MagicMock()
    m.return_value.read.return_value = None
    m.return_value.__enter__ = MagicMock(return_value=m.return_value)
    m.return_value.__exit__ = MagicMock(return_value=False)
    return m


def _run_and_flush_events(thread: TrackerThread, qapp: QApplication) -> None:
    thread.run()
    qapp.processEvents()


def test_tracker_thread_emits_position_updated(qapp):
    mock_cap = _make_mock_cap(frames=2)
    mock_face_pos = MagicMock()
    mock_face_pos.x_cm = 1.0
    mock_face_pos.y_cm = 0.0
    mock_face_pos.z_cm = 60.0

    with (
        patch("launcher.tracker_thread.cv2.VideoCapture", return_value=mock_cap),
        patch("tracker.face_tracker.FaceTracker") as MockFT,
        patch("launcher.tracker_thread.FreetracWriter") as MockFW,
        patch("launcher.tracker_thread.SharedMemoryWriter") as MockSW,
        patch("launcher.tracker_thread.HeadSmoother") as MockHS,
        patch("launcher.tracker_thread.SharedSettingsReader", _mock_settings_reader()),
    ):
        ft_instance = MockFT.return_value.__enter__.return_value
        ft_instance.process_frame.return_value = mock_face_pos
        MockFW.return_value.__enter__.return_value = MagicMock()
        hs_instance = MockHS.return_value
        hs_instance.update.return_value = (1.0, 0.0, 60.0)

        thread = TrackerThread(camera_index=0, config=CONFIG)
        emissions = []
        thread.position_updated.connect(lambda *args: emissions.append(args))
        _run_and_flush_events(thread, qapp)

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
        patch("tracker.face_tracker.FaceTracker") as MockFT,
        patch("launcher.tracker_thread.FreetracWriter") as MockFW,
        patch("launcher.tracker_thread.SharedMemoryWriter") as MockSW,
        patch("launcher.tracker_thread.HeadSmoother") as MockHS,
        patch("launcher.tracker_thread.SharedSettingsReader", _mock_settings_reader()),
    ):
        MockFT.return_value.__enter__.return_value.process_frame.return_value = mock_face_pos
        MockFW.return_value.__enter__.return_value = MagicMock()
        MockHS.return_value.update.return_value = (0.0, 0.0, 60.0)

        thread = TrackerThread(camera_index=0, config=CONFIG)
        statuses = []
        thread.status_changed.connect(statuses.append)
        _run_and_flush_events(thread, qapp)

    assert "tracking" in statuses


def test_tracker_thread_stop_terminates_thread(qapp):
    # Cap that reads forever until stop event
    cap = MagicMock()
    cap.isOpened.return_value = True
    cap.read.return_value = (True, MagicMock())

    with (
        patch("launcher.tracker_thread.cv2.VideoCapture", return_value=cap),
        patch("tracker.face_tracker.FaceTracker") as MockFT,
        patch("launcher.tracker_thread.FreetracWriter") as MockFW,
        patch("launcher.tracker_thread.SharedMemoryWriter") as MockSW,
        patch("launcher.tracker_thread.HeadSmoother") as MockHS,
        patch("launcher.tracker_thread.SharedSettingsReader", _mock_settings_reader()),
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
        patch("tracker.face_tracker.FaceTracker") as MockFT,
        patch("launcher.tracker_thread.FreetracWriter") as MockFW,
        patch("launcher.tracker_thread.HeadSmoother"),
        patch("launcher.tracker_thread.SharedSettingsReader", _mock_settings_reader()),
    ):
        MockFT.return_value.__enter__.return_value = MagicMock()
        MockFW.return_value.__enter__.return_value = MagicMock()

        thread = TrackerThread(camera_index=0, config=CONFIG)
        statuses = []
        thread.status_changed.connect(statuses.append)
        _run_and_flush_events(thread, qapp)

    assert "error" in statuses


def test_tracker_thread_emits_error_when_camera_returns_no_frames(qapp):
    cap = MagicMock()
    cap.isOpened.return_value = True
    cap.read.return_value = (False, None)

    with (
        patch("launcher.tracker_thread.cv2.VideoCapture", return_value=cap),
        patch("tracker.face_tracker.FaceTracker") as MockFT,
        patch("launcher.tracker_thread.FreetracWriter") as MockFW,
        patch("launcher.tracker_thread.HeadSmoother"),
        patch("launcher.tracker_thread.SharedSettingsReader", _mock_settings_reader()),
    ):
        MockFT.return_value.__enter__.return_value = MagicMock()
        MockFW.return_value.__enter__.return_value = MagicMock()

        thread = TrackerThread(camera_index=0, config=CONFIG)
        statuses = []
        thread.status_changed.connect(statuses.append)
        _run_and_flush_events(thread, qapp)

    assert "error" in statuses


def test_tracker_thread_emits_hold_status(qapp):
    """status_changed emits 'hold' when face is lost but hold_ms has not expired."""
    mock_cap = _make_mock_cap(frames=2)
    face_pos = MagicMock()
    face_pos.x_cm = 1.0
    face_pos.y_cm = 0.0
    face_pos.z_cm = 60.0

    with (
        patch("launcher.tracker_thread.cv2.VideoCapture", return_value=mock_cap),
        patch("tracker.face_tracker.FaceTracker") as MockFT,
        patch("launcher.tracker_thread.FreetracWriter") as MockFW,
        patch("launcher.tracker_thread.SharedMemoryWriter") as MockSW,
        patch("launcher.tracker_thread.HeadSmoother") as MockHS,
        patch("launcher.tracker_thread.SharedSettingsReader", _mock_settings_reader()),
    ):
        ft_instance = MockFT.return_value.__enter__.return_value
        ft_instance.process_frame.side_effect = [face_pos, None]
        MockFW.return_value.__enter__.return_value = MagicMock()
        MockHS.return_value.update.return_value = (1.0, 0.0, 60.0)

        thread = TrackerThread(camera_index=0, config=CONFIG)
        statuses = []
        thread.status_changed.connect(statuses.append)
        _run_and_flush_events(thread, qapp)

    assert "hold" in statuses


def test_tracker_thread_emits_paused_status(qapp):
    """status_changed emits 'paused' when face was never detected."""
    mock_cap = _make_mock_cap(frames=1)

    with (
        patch("launcher.tracker_thread.cv2.VideoCapture", return_value=mock_cap),
        patch("tracker.face_tracker.FaceTracker") as MockFT,
        patch("launcher.tracker_thread.FreetracWriter") as MockFW,
        patch("launcher.tracker_thread.SharedMemoryWriter") as MockSW,
        patch("launcher.tracker_thread.HeadSmoother") as MockHS,
        patch("launcher.tracker_thread.SharedSettingsReader", _mock_settings_reader()),
    ):
        MockFT.return_value.__enter__.return_value.process_frame.return_value = None
        MockFW.return_value.__enter__.return_value = MagicMock()
        MockHS.return_value.update.return_value = (0.0, 0.0, 60.0)

        thread = TrackerThread(camera_index=0, config=CONFIG)
        statuses = []
        thread.status_changed.connect(statuses.append)
        _run_and_flush_events(thread, qapp)

    assert "paused" in statuses


def test_apply_deadzone_first_call_accepted():
    from launcher.tracker_thread import _apply_deadzone
    out, prev = _apply_deadzone((1.0, 0.0, 60.0), None, deadzone_cm=0.5)
    assert out == (1.0, 0.0, 60.0)
    assert prev == (1.0, 0.0, 60.0)


def test_apply_deadzone_suppresses_small_xy_but_passes_z():
    from launcher.tracker_thread import _apply_deadzone
    _, prev = _apply_deadzone((1.0, 0.0, 60.0), None, deadzone_cm=0.5)
    # XY moves 0.3 cm (< 0.5 cm deadzone), but Z changes from 60 → 65
    out, _ = _apply_deadzone((1.3, 0.0, 65.0), prev, deadzone_cm=0.5)
    assert out[0] == pytest.approx(1.0)   # X clamped
    assert out[1] == pytest.approx(0.0)   # Y clamped
    assert out[2] == pytest.approx(65.0)  # Z passed through


def test_apply_deadzone_passes_large_move():
    from launcher.tracker_thread import _apply_deadzone
    _, prev = _apply_deadzone((1.0, 0.0, 60.0), None, deadzone_cm=0.5)
    out, _ = _apply_deadzone((2.0, 0.0, 60.0), prev, deadzone_cm=0.5)
    assert out == (2.0, 0.0, 60.0)
