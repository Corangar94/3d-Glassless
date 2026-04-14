# tests/test_main.py
import pytest
from unittest.mock import MagicMock, patch

from tracker.main import TrackingLoop
from tracker.face_tracker import HeadPosition


def _make_mock_cap():
    """Return a mock VideoCapture that always reads successfully."""
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.read.return_value = (True, MagicMock())
    return mock_cap


def test_tracking_loop_terminates_after_max_frames():
    """Loop must stop after exactly max_frames frames and not hang."""
    mock_tracker = MagicMock()
    mock_tracker.process_frame.return_value = None  # no face

    mock_writer = MagicMock()
    mock_smoother = MagicMock()
    mock_smoother.update.return_value = (0.0, 0.0, 60.0)

    loop = TrackingLoop(
        tracker=mock_tracker,
        writer=mock_writer,
        smoother=mock_smoother,
        hold_ms=500,
    )

    mock_cap = _make_mock_cap()
    with patch("tracker.main.cv2.VideoCapture", return_value=mock_cap):
        loop.run(camera_index=0, max_frames=5)

    assert mock_tracker.process_frame.call_count == 5


def test_tracking_loop_writes_default_when_no_face_and_hold_expired():
    """When face is lost and hold_ms=0, loop writes (0, 0, 60) immediately."""
    mock_tracker = MagicMock()
    mock_tracker.process_frame.return_value = None

    mock_writer = MagicMock()
    mock_smoother = MagicMock()
    mock_smoother.update.return_value = (0.0, 0.0, 60.0)

    loop = TrackingLoop(
        tracker=mock_tracker,
        writer=mock_writer,
        smoother=mock_smoother,
        hold_ms=0,
    )

    mock_cap = _make_mock_cap()
    with patch("tracker.main.cv2.VideoCapture", return_value=mock_cap):
        loop.run(camera_index=0, max_frames=3)

    for call in mock_writer.write.call_args_list:
        assert call.kwargs["z"] == 60.0


def test_tracking_loop_smooths_face_position():
    """When a face is detected, smoother.update() is called with the detected position."""
    mock_tracker = MagicMock()
    mock_tracker.process_frame.return_value = HeadPosition(
        x_cm=5.0, y_cm=-2.0, z_cm=55.0
    )

    mock_writer = MagicMock()
    mock_smoother = MagicMock()
    mock_smoother.update.return_value = (4.9, -1.9, 55.1)

    loop = TrackingLoop(
        tracker=mock_tracker,
        writer=mock_writer,
        smoother=mock_smoother,
        hold_ms=500,
    )

    mock_cap = _make_mock_cap()
    with patch("tracker.main.cv2.VideoCapture", return_value=mock_cap):
        loop.run(camera_index=0, max_frames=1)

    mock_smoother.update.assert_called_once_with(5.0, -2.0, 55.0)
    mock_writer.write.assert_called_once_with(x=4.9, y=-1.9, z=55.1)


def test_tracking_loop_holds_last_position_during_hold_window():
    """During hold period, loop replays last smoothed output without updating smoother."""
    mock_tracker = MagicMock()
    # Frame 1: face detected; frames 2-3: face lost but hold not expired
    mock_tracker.process_frame.side_effect = [
        HeadPosition(x_cm=5.0, y_cm=-2.0, z_cm=55.0),
        None,
        None,
    ]
    mock_writer = MagicMock()
    mock_smoother = MagicMock()
    mock_smoother.update.return_value = (4.9, -1.9, 55.1)

    loop = TrackingLoop(
        tracker=mock_tracker,
        writer=mock_writer,
        smoother=mock_smoother,
        hold_ms=500,
    )
    mock_cap = _make_mock_cap()
    with patch("tracker.main.cv2.VideoCapture", return_value=mock_cap):
        loop.run(camera_index=0, max_frames=3)

    # smoother.update called exactly once (frame 1 only); not called during hold
    assert mock_smoother.update.call_count == 1
    # All three frames write the same held position
    for call in mock_writer.write.call_args_list:
        assert call.kwargs == {"x": 4.9, "y": -1.9, "z": 55.1}


def test_tracking_loop_raises_on_camera_open_failure():
    """RuntimeError is raised immediately when the camera cannot be opened."""
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = False
    loop = TrackingLoop(
        tracker=MagicMock(),
        writer=MagicMock(),
        smoother=MagicMock(),
    )
    with patch("tracker.main.cv2.VideoCapture", return_value=mock_cap):
        with pytest.raises(RuntimeError, match="Could not open camera 0"):
            loop.run(camera_index=0)


def test_tracking_loop_calls_on_position_hook():
    """_on_position is called once per frame with correct status strings."""
    positions = []

    class RecordingLoop(TrackingLoop):
        def _on_position(self, x, y, z, status):
            positions.append(status)

    mock_tracker = MagicMock()
    mock_tracker.process_frame.side_effect = [
        HeadPosition(x_cm=1.0, y_cm=0.0, z_cm=60.0),
        None,
    ]
    mock_writer = MagicMock()
    mock_smoother = MagicMock()
    mock_smoother.update.return_value = (1.0, 0.0, 60.0)

    loop = RecordingLoop(
        tracker=mock_tracker,
        writer=mock_writer,
        smoother=mock_smoother,
        hold_ms=500,
    )
    mock_cap = _make_mock_cap()
    with patch("tracker.main.cv2.VideoCapture", return_value=mock_cap):
        loop.run(camera_index=0, max_frames=2)

    assert positions[0] == "tracking"
    assert positions[1] in ("hold", "paused")
