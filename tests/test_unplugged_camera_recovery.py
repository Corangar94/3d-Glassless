from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tracker.camera_reconnect_retry import CameraReconnectPolicy
from tracker.main import TrackingLoop, _camera_reconnect_policy
from tracker.pose import FilteredPose


class _SettingsReader:
    def __init__(self) -> None:
        self.closed = False

    def read(self):
        return None

    def close(self) -> None:
        self.closed = True


class _Writer:
    def __init__(self) -> None:
        self.states: list[str] = []
        self.poses: list[tuple[FilteredPose, bool]] = []

    def write_state(self, state: str) -> None:
        self.states.append(state)

    def write_pose(self, pose: FilteredPose, *, valid: bool) -> None:
        self.poses.append((pose, valid))


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


class _StopDuringWait:
    def __init__(self) -> None:
        self._set = False
        self.wait_calls: list[float] = []

    def is_set(self) -> bool:
        return self._set

    def wait(self, timeout: float) -> bool:
        self.wait_calls.append(timeout)
        self._set = True
        return True


def _cap(*, opened: bool, reads=None) -> MagicMock:
    cap = MagicMock()
    cap.isOpened.return_value = opened
    if reads is not None:
        cap.read.side_effect = reads
    return cap


def _policy(
    *,
    immediate_retries: int = 1,
    max_failures: int = 6,
    base_delay_s: float = 0.0,
    max_delay_s: float = 0.0,
    max_outage_s: float = 60.0,
    heartbeat_s: float = 1.0,
) -> CameraReconnectPolicy:
    return CameraReconnectPolicy(
        immediate_retries=immediate_retries,
        max_failures=max_failures,
        base_delay_s=base_delay_s,
        max_delay_s=max_delay_s,
        max_outage_s=max_outage_s,
        heartbeat_s=heartbeat_s,
    )


def _loop(
    *,
    writer: _Writer | None = None,
    tracker: _Tracker | None = None,
    smoother: _Smoother | None = None,
    stop_event=None,
    policy: CameraReconnectPolicy | None = None,
) -> TrackingLoop:
    return TrackingLoop(
        tracker=tracker or _Tracker(),
        writer=writer or _Writer(),
        smoother=smoother or _Smoother(),
        hold_ms=0,
        stop_event=stop_event,
        camera_reconnect_policy=policy or _policy(),
    )


def test_initially_unplugged_camera_recovers_without_process_exit():
    unavailable_one = _cap(opened=False)
    unavailable_two = _cap(opened=False)
    recovered = _cap(opened=True, reads=[(True, object())])
    writer = _Writer()
    tracker = _Tracker()
    reader = _SettingsReader()
    loop = _loop(writer=writer, tracker=tracker)

    with (
        patch(
            "tracker.main._open_camera",
            side_effect=[unavailable_one, unavailable_two, recovered],
        ) as open_camera,
        patch("tracker.main.SharedSettingsReader", return_value=reader),
    ):
        loop.run(camera_index=2, max_frames=1)

    assert open_camera.call_count == 3
    assert tracker.process_count == 1
    assert loop._camera_reconnect.failure_count == 0
    assert writer.states.count("paused") >= 3
    assert all(not valid for _pose, valid in writer.poses)
    unavailable_one.release.assert_called()
    unavailable_two.release.assert_called()
    recovered.release.assert_called()
    assert reader.closed


def test_stop_during_backoff_exits_cleanly_without_escalation():
    stop_event = _StopDuringWait()
    reader = _SettingsReader()
    loop = _loop(
        stop_event=stop_event,
        policy=_policy(
            immediate_retries=0,
            max_failures=5,
            base_delay_s=2.0,
            max_delay_s=2.0,
        ),
    )

    with (
        patch("tracker.main._open_camera", return_value=_cap(opened=False)),
        patch("tracker.main.SharedSettingsReader", return_value=reader),
    ):
        loop.run(camera_index=0)

    assert stop_event.wait_calls == [1.0]
    assert reader.closed


def test_local_budget_exhaustion_escalates_to_launcher_supervisor():
    loop = _loop(
        policy=_policy(
            immediate_retries=0,
            max_failures=2,
            base_delay_s=0.0,
            max_delay_s=0.0,
        )
    )

    with (
        patch("tracker.main._open_camera", return_value=_cap(opened=False)),
        patch("tracker.main.SharedSettingsReader", return_value=_SettingsReader()),
    ):
        with pytest.raises(RuntimeError, match="recovery exhausted after 2 failures"):
            loop.run(camera_index=7)


def test_stalled_session_can_survive_all_backends_missing_then_recover():
    stalled = _cap(
        opened=True,
        reads=[(False, None), (False, None), (False, None)],
    )
    unplugged = _cap(opened=False)
    recovered = _cap(opened=True, reads=[(True, object())])
    tracker = _Tracker()
    smoother = _Smoother()
    writer = _Writer()
    loop = _loop(writer=writer, tracker=tracker, smoother=smoother)

    with (
        patch(
            "tracker.main._open_camera",
            side_effect=[stalled, unplugged, recovered],
        ) as open_camera,
        patch("tracker.main.SharedSettingsReader", return_value=_SettingsReader()),
        patch("tracker.main.time.sleep"),
    ):
        loop.run(camera_index=0, max_frames=1)

    assert open_camera.call_count == 3
    assert tracker.reset_count == 1
    assert smoother.reset_count == 1
    assert tracker.process_count == 1
    assert "paused" in writer.states
    assert loop._camera_reconnect.failure_count == 0


def test_long_backoff_refreshes_paused_heartbeat_once_per_second():
    writer = _Writer()
    loop = _loop(
        writer=writer,
        policy=_policy(
            immediate_retries=0,
            max_failures=5,
            base_delay_s=2.5,
            max_delay_s=2.5,
            heartbeat_s=1.0,
        ),
    )

    with patch("tracker.main.time.sleep") as sleep:
        assert loop._wait_for_camera_retry(2.5)

    assert sleep.call_count == 3
    assert writer.states == ["paused", "paused"]
    assert all(not valid for _pose, valid in writer.poses)


def test_invalid_reconnect_config_falls_back_to_safe_defaults(capsys):
    policy = _camera_reconnect_policy(
        {"reconnect": {"max_failures": 0, "heartbeat_s": -1}}
    )

    assert policy == CameraReconnectPolicy()
    assert "using safe defaults" in capsys.readouterr().out


def test_valid_reconnect_config_is_applied():
    policy = _camera_reconnect_policy(
        {
            "reconnect": {
                "immediate_retries": 2,
                "max_failures": 9,
                "base_delay_s": 0.25,
                "max_delay_s": 4.0,
                "max_outage_s": 30.0,
                "heartbeat_s": 0.5,
            }
        }
    )

    assert policy.immediate_retries == 2
    assert policy.max_failures == 9
    assert policy.base_delay_s == 0.25
    assert policy.max_delay_s == 4.0
    assert policy.max_outage_s == 30.0
    assert policy.heartbeat_s == 0.5
