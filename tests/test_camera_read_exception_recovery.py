from unittest.mock import MagicMock, patch

from tracker.camera_reconnect_retry import CameraReconnectPolicy
from tracker.main import TrackingLoop


class _SettingsReader:
    def read(self):
        return None

    def close(self) -> None:
        pass


class _Tracker:
    def __init__(self) -> None:
        self.reset_count = 0
        self.process_count = 0

    def reset_session(self) -> None:
        self.reset_count += 1

    def process_frame(self, _frame, capture_timestamp_ms=None):
        self.process_count += 1
        return None


class _Smoother:
    def __init__(self) -> None:
        self.reset_count = 0

    def reset(self) -> None:
        self.reset_count += 1

    def set_measurement_noise(self, _value: float) -> None:
        pass

    def update(self, x, y, z, dt_seconds=None):
        return x, y, z


class _Writer:
    def __init__(self) -> None:
        self.states: list[str] = []

    def write_state(self, state: str) -> None:
        self.states.append(state)

    def write_pose(self, _pose, *, valid: bool) -> None:
        pass


def _cap(*, reads) -> MagicMock:
    cap = MagicMock()
    cap.isOpened.return_value = True
    cap.read.side_effect = reads
    return cap


def test_repeated_camera_read_exceptions_rotate_to_recovered_capture():
    broken = _cap(
        reads=[
            RuntimeError("device unplugged"),
            RuntimeError("device unplugged"),
            RuntimeError("device unplugged"),
        ]
    )
    recovered = _cap(reads=[(True, object())])
    tracker = _Tracker()
    smoother = _Smoother()
    writer = _Writer()
    loop = TrackingLoop(
        tracker=tracker,
        writer=writer,
        smoother=smoother,
        hold_ms=0,
        camera_reconnect_policy=CameraReconnectPolicy(
            immediate_retries=1,
            max_failures=4,
            base_delay_s=0.0,
            max_delay_s=0.0,
            max_outage_s=10.0,
        ),
    )

    with (
        patch(
            "tracker.main._open_camera",
            side_effect=[broken, recovered],
        ) as open_camera,
        patch("tracker.main.SharedSettingsReader", return_value=_SettingsReader()),
        patch("tracker.main.time.sleep"),
    ):
        loop.run(camera_index=0, max_frames=1)

    assert open_camera.call_count == 2
    assert broken.read.call_count == 3
    assert broken.release.called
    assert tracker.reset_count == 1
    assert smoother.reset_count == 1
    assert tracker.process_count == 1
    assert "paused" in writer.states
