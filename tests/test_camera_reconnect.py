from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import threading

import numpy as np

from tracker.camera_quality import CameraQualityMonitor
from tracker.face_tracker import FaceTracker
from tracker.main import TrackingLoop
from tracker.pose import FilteredPose, HeadPosition


def _textured_frame(brightness: int = 120) -> np.ndarray:
    frame = np.full((120, 160, 3), brightness, dtype=np.uint8)
    frame[:, ::8] = 255
    frame[::8, :] = 0
    return frame


def test_camera_quality_reset_starts_a_new_warmup_window():
    monitor = CameraQualityMonitor(
        window_size=8,
        minimum_sharpness=1.0,
        minimum_fps=1.0,
    )
    frame = _textured_frame()
    for index in range(10):
        status = monitor.update(frame, 1000 + index * 33)

    assert status.fps is not None
    monitor.reset()

    empty = monitor.status()
    assert empty.quality == "UNKNOWN"
    assert empty.fps is None
    assert not empty.stable_for_lock

    first = monitor.update(frame, 5000)
    assert first.fps is None
    assert not first.stable_for_lock


def _bare_async_tracker() -> FaceTracker:
    tracker = FaceTracker.__new__(FaceTracker)
    tracker._lock = threading.Lock()
    tracker._latest_pose = HeadPosition(
        x_cm=1.0,
        y_cm=2.0,
        z_cm=60.0,
        capture_timestamp_ms=100,
    )
    tracker._last_delivered_timestamp_ms = 100
    tracker._last_submitted_wire_timestamp_ms = 1234
    tracker._last_submitted_media_timestamp_ms = 5000
    tracker._minimum_result_media_timestamp_ms = None
    tracker._closed = False
    tracker._pose_from_result = lambda _result, _width, _height, timestamp: HeadPosition(
        x_cm=3.0,
        y_cm=4.0,
        z_cm=61.0,
        capture_timestamp_ms=int(timestamp) & 0xFFFF_FFFF,
    )
    return tracker


def test_mediapipe_session_reset_discards_inflight_old_callbacks():
    tracker = _bare_async_tracker()
    image = SimpleNamespace(width=640, height=480)

    tracker.reset_session()

    assert tracker._latest_pose is None
    assert tracker._last_delivered_timestamp_ms is None
    assert tracker._minimum_result_media_timestamp_ms == 5000

    tracker._on_result(object(), image, 5000)
    assert tracker._latest_pose is None

    tracker._on_result(object(), image, 5001)
    assert tracker._latest_pose is not None
    assert tracker._latest_pose.capture_timestamp_ms == 5001


def test_mediapipe_reset_preserves_monotonic_submission_timeline():
    tracker = _bare_async_tracker()

    tracker.reset_session()

    assert tracker._last_submitted_wire_timestamp_ms == 1234
    assert tracker._last_submitted_media_timestamp_ms == 5000


class _SequenceTracker:
    def __init__(self) -> None:
        self._positions = iter((0.0, 30.0))
        self.reset_count = 0

    def process_frame(
        self,
        _frame: object,
        capture_timestamp_ms: int | None = None,
    ) -> HeadPosition:
        return HeadPosition(
            x_cm=next(self._positions),
            y_cm=0.0,
            z_cm=60.0,
            confidence=1.0,
            capture_timestamp_ms=int(capture_timestamp_ms or 1),
        )

    def reset_session(self) -> None:
        self.reset_count += 1


class _PassthroughSmoother:
    def __init__(self) -> None:
        self.reset_count = 0
        self.measurement_noise = 0.0

    def update(
        self,
        x: float,
        y: float,
        z: float,
        dt_seconds: float | None = None,
    ) -> tuple[float, float, float]:
        return x, y, z

    def reset(self) -> None:
        self.reset_count += 1

    def set_measurement_noise(self, value: float) -> None:
        self.measurement_noise = value


class _ImmediateStableQuality:
    def __init__(self) -> None:
        self.reset_count = 0
        self.update_count = 0

    def reset(self) -> None:
        self.reset_count += 1

    def update(self, _frame: object, _timestamp_ms: int) -> SimpleNamespace:
        self.update_count += 1
        return SimpleNamespace(
            quality="GOOD",
            brightness=0.5,
            brightness_jitter=0.0,
            sharpness=100.0,
            fps=30.0,
            problems=(),
            stable_for_lock=True,
        )


class _RecordingWriter:
    def __init__(self) -> None:
        self.states: list[str] = []
        self.poses: list[tuple[FilteredPose, bool]] = []

    def write_state(self, state: str) -> None:
        self.states.append(state)

    def write_pose(self, pose: FilteredPose, *, valid: bool) -> None:
        self.poses.append((pose, valid))


class _SettingsReader:
    def read(self):
        return None

    def close(self) -> None:
        pass


def _capture(read_side_effect) -> MagicMock:
    cap = MagicMock()
    cap.isOpened.return_value = True
    cap.read.side_effect = read_side_effect
    return cap


def test_reopen_resets_temporal_state_and_rearms_camera_controls():
    first_frame = _textured_frame(100)
    second_frame = _textured_frame(140)
    first_cap = _capture(
        [
            (True, first_frame),
            (False, None),
            (False, None),
            (False, None),
        ]
    )
    second_cap = _capture([(True, second_frame)])
    tracker = _SequenceTracker()
    smoother = _PassthroughSmoother()
    quality = _ImmediateStableQuality()
    writer = _RecordingWriter()
    loop = TrackingLoop(
        tracker=tracker,
        writer=writer,
        smoother=smoother,
        hold_ms=500,
        camera_quality_monitor=quality,
        lock_camera_controls=True,
    )

    with (
        patch("tracker.main._open_camera", side_effect=[first_cap, second_cap]),
        patch("tracker.main.SharedSettingsReader", return_value=_SettingsReader()),
        patch("tracker.main.try_lock_camera_controls", return_value={}) as lock,
        patch("tracker.main.time.sleep"),
    ):
        loop.run(camera_index=0, max_frames=2)

    assert tracker.reset_count == 1
    assert smoother.reset_count == 1
    assert quality.reset_count == 1
    assert lock.call_count == 2
    first_cap.release.assert_called()

    assert "paused" in writer.states
    paused_packets = [pose for pose, valid in writer.poses if not valid]
    assert paused_packets
    assert paused_packets[-1].xyz == (0.0, 0.0, 60.0)

    tracking_packets = [pose for pose, valid in writer.poses if valid]
    assert tracking_packets[0].x_cm == 0.0
    # The old raw pose was cleared, so the new session's 30 cm position is not
    # incorrectly clamped to a 10 cm step from the retired camera session.
    assert tracking_packets[-1].x_cm == 30.0


def test_capture_reset_clears_hold_and_measurement_state():
    tracker = _SequenceTracker()
    smoother = _PassthroughSmoother()
    quality = _ImmediateStableQuality()
    writer = _RecordingWriter()
    loop = TrackingLoop(
        tracker=tracker,
        writer=writer,
        smoother=smoother,
        camera_quality_monitor=quality,
    )
    loop._last_face_ms = 123.0
    loop._last_raw_pos = (9.0, 8.0, 70.0)
    loop._last_measurement_s = 456.0
    loop._last_output_pose = FilteredPose(x_cm=9.0, y_cm=8.0, z_cm=70.0)

    with patch("tracker.main.monotonic_ms", return_value=4242):
        timestamp = loop._reset_capture_session()

    assert timestamp == 4242
    assert loop._last_face_ms is None
    assert loop._last_raw_pos is None
    assert loop._last_measurement_s is None
    assert loop._last_output_pose.xyz == (0.0, 0.0, 60.0)
    assert loop._last_output_pose.publish_timestamp_ms == 4242
    assert loop._last_output_pose.prediction_target_timestamp_ms == 4242
    assert writer.states[-1] == "paused"
    assert writer.poses[-1][1] is False
