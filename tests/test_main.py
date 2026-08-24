# tests/test_main.py
import math
import types
import pytest
from unittest.mock import MagicMock, patch

import tracker.main as tracker_main
from tracker.main import (
    TrackingLoop,
    _apply_camera_tilt,
    _calibrate_tilt,
)
from tracker.face_tracker import HeadPosition
from tracker.shared_settings import OverlaySettings


# ── _calibrate_tilt tests ───────────────────────────────────────────────────

def test_calibrate_tilt_returns_none_when_too_few_samples():
    assert _calibrate_tilt([1.0], [45.0]) is None


def test_calibrate_tilt_computes_correct_angle():
    # arctan(13.8 / 45) ≈ 17.0°
    y = [13.8] * 20
    z = [45.0] * 20
    result = _calibrate_tilt(y, z)
    assert result is not None
    assert abs(result - 17.0) < 0.5


def test_calibrate_tilt_returns_none_for_zero_z():
    assert _calibrate_tilt([1.0] * 15, [0.0] * 15) is None


# ── _apply_camera_tilt tests ────────────────────────────────────────────────

def test_tilt_zero_is_identity():
    x, y, z = _apply_camera_tilt(1.0, 2.0, 3.0, 0.0)
    assert x == 1.0 and y == 2.0 and z == 3.0


def test_tilt_corrects_y_offset():
    """20° downward camera tilt: y=13.8, z=45 should give y≈-2.4, z≈47."""
    x, y, z = _apply_camera_tilt(0.0, 13.8, 45.0, 20.0)
    assert abs(y - (-2.4)) < 0.2   # y corrected to near zero
    assert abs(z - 47.0) < 0.5     # z slightly adjusted


def test_tilt_x_unchanged():
    x, y, z = _apply_camera_tilt(5.0, 13.8, 45.0, 20.0)
    assert x == 5.0  # x is not affected by X-axis rotation


def _make_mock_cap():
    """Return a mock VideoCapture that always reads successfully."""
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.read.return_value = (True, MagicMock())
    return mock_cap


def test_load_face_tracker_auto_prefers_mediapipe_backend(monkeypatch):
    mp_module = types.SimpleNamespace(FaceTracker=object)
    cv2_module = types.SimpleNamespace(FaceTracker=str)

    def fake_import(name):
        return {
            "tracker.face_tracker": mp_module,
            "tracker.face_tracker_cv2": cv2_module,
        }[name]

    monkeypatch.setattr(
        tracker_main,
        "importlib",
        types.SimpleNamespace(import_module=fake_import),
        raising=False,
    )

    cls, backend = tracker_main._load_face_tracker_class("auto")

    assert cls is object
    assert backend == "mediapipe"


def test_load_face_tracker_auto_falls_back_to_cv2_when_mediapipe_unavailable(monkeypatch):
    cv2_module = types.SimpleNamespace(FaceTracker=str)

    def fake_import(name):
        if name == "tracker.face_tracker":
            raise ImportError("no mediapipe")
        return cv2_module

    monkeypatch.setattr(
        tracker_main,
        "importlib",
        types.SimpleNamespace(import_module=fake_import),
        raising=False,
    )

    cls, backend = tracker_main._load_face_tracker_class("auto")

    assert cls is str
    assert backend == "cv2"


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


def test_tracking_loop_opens_camera_with_directshow_backend_on_windows():
    mock_tracker = MagicMock()
    mock_tracker.process_frame.return_value = None
    loop = TrackingLoop(
        tracker=mock_tracker,
        writer=MagicMock(),
        smoother=MagicMock(),
    )
    mock_cap = _make_mock_cap()

    with patch("tracker.main.cv2.VideoCapture", return_value=mock_cap) as video_capture:
        loop.run(camera_index=0, max_frames=1)

    video_capture.assert_called_once_with(0, tracker_main.cv2.CAP_DSHOW)


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
        camera_tilt_deg=20.0,
    )

    mock_cap = _make_mock_cap()
    with patch("tracker.main.cv2.VideoCapture", return_value=mock_cap):
        loop.run(camera_index=0, max_frames=3)

    for call in mock_writer.write.call_args_list:
        assert call.kwargs["x"] == 0.0
        assert call.kwargs["y"] == 0.0
        assert call.kwargs["z"] == 60.0
    assert [call.args[0] for call in mock_writer.write_state.call_args_list] == [
        "paused", "paused", "paused"
    ]


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


def test_tracking_loop_applies_live_smoothing_without_tracker_deadzone():
    """Tracker consumes smoothing settings; the overlay owns the sole XY deadzone."""
    mock_tracker = MagicMock()
    mock_tracker.process_frame.side_effect = [
        HeadPosition(x_cm=1.0, y_cm=0.0, z_cm=60.0),
        HeadPosition(x_cm=1.5, y_cm=0.0, z_cm=65.0),
    ]

    mock_writer = MagicMock()
    mock_smoother = MagicMock()
    mock_smoother.update.side_effect = [
        (1.0, 0.0, 60.0),
        (1.5, 0.0, 65.0),
    ]

    class FakeSettingsReader:
        def read(self):
            return OverlaySettings(deadzone_mm=10.0, smoothing_alpha=0.3)

        def close(self):
            pass

    loop = TrackingLoop(
        tracker=mock_tracker,
        writer=mock_writer,
        smoother=mock_smoother,
        hold_ms=500,
    )

    mock_cap = _make_mock_cap()
    with (
        patch("tracker.main.cv2.VideoCapture", return_value=mock_cap),
        patch("tracker.main.SharedSettingsReader", return_value=FakeSettingsReader(), create=True),
    ):
        loop.run(camera_index=0, max_frames=2)

    assert mock_smoother.set_measurement_noise.call_args_list[-1].args == (0.3,)
    assert mock_smoother.update.call_args_list[0].args == (1.0, 0.0, 60.0)
    assert mock_smoother.update.call_args_list[1].args == (1.5, 0.0, 65.0)


def test_tracking_loop_publishes_state_before_pose_commit():
    """A new G3D pose timestamp must never expose the previous face state."""
    events: list[tuple[str, object]] = []

    class RecordingWriter:
        def write_state(self, state: str) -> None:
            events.append(("state", state))

        def write(self, *, x: float, y: float, z: float) -> None:
            events.append(("pose", (x, y, z)))

    mock_tracker = MagicMock()
    mock_tracker.process_frame.return_value = HeadPosition(
        x_cm=1.0, y_cm=2.0, z_cm=60.0
    )
    mock_smoother = MagicMock()
    mock_smoother.update.return_value = (1.0, 2.0, 60.0)
    loop = TrackingLoop(
        tracker=mock_tracker,
        writer=RecordingWriter(),
        smoother=mock_smoother,
    )

    with patch("tracker.main.cv2.VideoCapture", return_value=_make_mock_cap()):
        loop.run(camera_index=0, max_frames=1)

    assert events == [
        ("state", "tracking"),
        ("pose", (1.0, 2.0, 60.0)),
    ]


def test_limit_pose_step_clamps_implausible_tracking_spikes():
    """A single bad tracker estimate should not publish a full parallax jump."""
    prev = (0.0, 0.0, 60.0)

    out = tracker_main._limit_pose_step(
        raw=(30.0, 40.0, 160.0),
        prev=prev,
        max_xy_step_cm=10.0,
        max_z_step_cm=12.0,
    )

    assert out == pytest.approx((6.0, 8.0, 72.0))


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


def test_tracking_loop_recovers_after_transient_camera_read_failures():
    mock_tracker = MagicMock()
    mock_tracker.process_frame.return_value = None
    mock_cap = _make_mock_cap()
    mock_cap.read.side_effect = [
        (False, None),
        (False, None),
        (True, MagicMock()),
    ]
    loop = TrackingLoop(
        tracker=mock_tracker,
        writer=MagicMock(),
        smoother=MagicMock(),
    )

    with (
        patch("tracker.main.cv2.VideoCapture", return_value=mock_cap),
        patch("tracker.main.time.sleep"),
    ):
        loop.run(camera_index=0, max_frames=1)

    assert mock_cap.read.call_count == 3
    assert mock_tracker.process_frame.call_count == 1


def test_tracking_loop_reopens_stalled_camera():
    stalled_cap = _make_mock_cap()
    stalled_cap.read.return_value = (False, None)
    recovered_cap = _make_mock_cap()
    mock_tracker = MagicMock()
    mock_tracker.process_frame.return_value = None
    loop = TrackingLoop(
        tracker=mock_tracker,
        writer=MagicMock(),
        smoother=MagicMock(),
    )

    with (
        patch(
            "tracker.main.cv2.VideoCapture",
            side_effect=[stalled_cap, recovered_cap],
        ) as video_capture,
        patch("tracker.main.time.sleep"),
    ):
        loop.run(camera_index=0, max_frames=1)

    assert video_capture.call_count == 2
    stalled_cap.release.assert_called()
    assert mock_tracker.process_frame.call_count == 1


def test_tracking_loop_calls_on_position_hook():
    """_on_position is called once per frame with correct coordinates and status."""
    positions = []

    class RecordingLoop(TrackingLoop):
        def _on_position(self, x, y, z, status):
            positions.append((x, y, z, status))

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

    assert positions[0][0] == pytest.approx(1.0)
    assert positions[0][1] == pytest.approx(0.0)
    assert positions[0][2] == pytest.approx(60.0)
    assert positions[0][3] == "tracking"
    assert positions[1][3] == "hold"
