from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from launcher.tracker_process import TrackerProcess


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _spy_list(spy: QSignalSpy) -> list:
    return [spy.at(i) for i in range(spy.count())]


def test_tracker_process_emits_error_when_subprocess_fails(qapp):
    tracker = TrackerProcess(config_path="missing.yaml")
    spy = QSignalSpy(tracker.status_changed)

    with patch(
        "launcher.tracker_process.subprocess.Popen",
        side_effect=OSError("cannot launch tracker"),
    ):
        started = tracker.start()

    assert started is False
    assert [s[0] for s in _spy_list(spy)] == ["error"]
    assert not tracker.isRunning()


def test_tracker_process_start_returns_true_when_subprocess_starts(qapp):
    tracker = TrackerProcess(config_path="config.yaml")

    with (
        patch("launcher.tracker_process.subprocess.Popen"),
        patch("launcher.tracker_process.SharedMemoryReader"),
    ):
        assert tracker.start() is True


def test_tracker_process_uses_private_child_mode_when_frozen(qapp):
    tracker = TrackerProcess(config_path="config.yaml")

    with (
        patch("launcher.tracker_process.sys.frozen", True, create=True),
        patch("launcher.tracker_process.sys.executable", r"C:\app\Glassless3D.exe"),
    ):
        assert tracker._tracker_command() == [
            r"C:\app\Glassless3D.exe",
            "--tracker-child",
            "--config",
            "config.yaml",
        ]


def test_tracker_process_restarts_when_shared_memory_is_stale(qapp):
    tracker = TrackerProcess(config_path="config.yaml", stale_restart_ms=100, max_restarts=1)
    first_proc = MagicMock()
    first_proc.poll.return_value = None
    second_proc = MagicMock()
    second_proc.poll.return_value = None
    reader = MagicMock()
    reader.read.return_value = (1.0, 2.0, 60.0, 10)
    tracker._proc = first_proc
    tracker._shm = reader
    tracker._last_ts = 10
    tracker._last_ts_time = 1.0
    tracker._start_time = 1.0
    tracker._desired_running = True
    spy = QSignalSpy(tracker.status_changed)

    with (
        patch("launcher.tracker_process.time.monotonic", return_value=1.2),
        patch("launcher.tracker_process.subprocess.Popen", return_value=second_proc) as popen,
        patch("launcher.tracker_process.SharedMemoryReader", return_value=reader),
    ):
        tracker._poll()
        for _ in range(50):
            if popen.called:
                break
            QTest.qWait(2)

    first_proc.terminate.assert_called_once()
    popen.assert_called_once()
    assert tracker._proc is second_proc
    assert [s[0] for s in _spy_list(spy)] == ["restarting", "initializing"]


def test_tracker_process_emits_error_when_stale_restart_budget_is_exhausted(qapp):
    tracker = TrackerProcess(config_path="config.yaml", stale_restart_ms=100, max_restarts=0)
    proc = MagicMock()
    proc.poll.return_value = None
    reader = MagicMock()
    reader.read.return_value = (1.0, 2.0, 60.0, 10)
    tracker._proc = proc
    tracker._shm = reader
    tracker._last_ts = 10
    tracker._last_ts_time = 1.0
    tracker._start_time = 1.0
    tracker._desired_running = True
    spy = QSignalSpy(tracker.status_changed)

    with patch("launcher.tracker_process.time.monotonic", return_value=1.2):
        tracker._poll()

    proc.terminate.assert_called_once()
    assert tracker._proc is None
    assert [s[0] for s in _spy_list(spy)] == ["error"]


def test_tracker_process_module_has_qapplication(qapp):
    assert QApplication.instance() is not None


def test_tracker_process_uses_face_validity_state_not_pose_timestamp(qapp):
    tracker = TrackerProcess(config_path="config.yaml")
    proc = MagicMock()
    proc.poll.return_value = None
    pose_reader = MagicMock()
    pose_reader.read.return_value = (0.0, 0.0, 60.0, 11)
    state_reader = MagicMock()
    state_reader.read.return_value = ("paused", 12)
    tracker._proc = proc
    tracker._shm = pose_reader
    tracker._state_shm = state_reader
    tracker._start_time = tracker._last_ts_time = 1.0
    spy = QSignalSpy(tracker.status_changed)

    with patch("launcher.tracker_process.time.monotonic", return_value=1.1):
        tracker._poll()

    assert [s[0] for s in _spy_list(spy)] == ["paused"]


def test_tracker_process_emits_validity_before_corresponding_pose(qapp):
    tracker = TrackerProcess(config_path="config.yaml")
    proc = MagicMock()
    proc.poll.return_value = None
    pose_reader = MagicMock()
    pose_reader.read.return_value = (0.0, 0.0, 60.0, 11)
    state_reader = MagicMock()
    state_reader.read.return_value = ("paused", 12)
    tracker._proc = proc
    tracker._shm = pose_reader
    tracker._state_shm = state_reader
    tracker._start_time = tracker._last_ts_time = 1.0
    events: list[tuple[str, object]] = []
    tracker.status_changed.connect(lambda status: events.append(("status", status)))
    tracker.position_updated.connect(
        lambda x, y, z: events.append(("pose", (x, y, z)))
    )

    with patch("launcher.tracker_process.time.monotonic", return_value=1.1):
        tracker._poll()

    assert events == [
        ("status", "paused"),
        ("pose", (0.0, 0.0, 60.0)),
    ]


def test_stop_during_retirement_prevents_pending_restart(qapp):
    tracker = TrackerProcess(config_path="config.yaml")
    retiring = MagicMock()
    tracker._retiring_proc = retiring
    tracker._desired_running = True
    stopped = QSignalSpy(tracker.stopped)

    tracker.stop()
    tracker._on_termination_finished(retiring, True)

    assert tracker._desired_running is False
    assert tracker._retiring_proc is None
    assert stopped.count() == 1


def test_start_during_retirement_waits_before_launching(qapp):
    tracker = TrackerProcess(config_path="config.yaml")
    retiring = MagicMock()
    replacement = MagicMock()
    replacement.poll.return_value = None
    tracker._retiring_proc = retiring

    with (
        patch("launcher.tracker_process.subprocess.Popen", return_value=replacement) as popen,
        patch("launcher.tracker_process.SharedMemoryReader"),
        patch("launcher.tracker_process.TrackingStateReader"),
    ):
        assert tracker.start() is True
        popen.assert_not_called()
        tracker._on_termination_finished(retiring, False)

    popen.assert_called_once()
    assert tracker._proc is replacement


def test_initialization_timeout_retires_child(qapp):
    tracker = TrackerProcess(config_path="config.yaml")
    proc = MagicMock()
    proc.poll.return_value = None
    reader = MagicMock()
    reader.read.return_value = None
    tracker._proc = proc
    tracker._shm = reader
    tracker._desired_running = True
    tracker._start_time = 1.0
    spy = QSignalSpy(tracker.status_changed)

    with (
        patch("launcher.tracker_process._INIT_TIMEOUT_S", 1.0),
        patch("launcher.tracker_process.time.monotonic", return_value=3.0),
        patch.object(tracker, "_begin_termination") as retire,
    ):
        tracker._poll()

    retire.assert_called_once_with(proc)
    assert tracker._proc is None
    assert tracker._desired_running is False
    assert [s[0] for s in _spy_list(spy)] == ["error"]


def test_lifecycle_reaper_is_not_daemon(qapp):
    tracker = TrackerProcess(config_path="config.yaml")
    proc = MagicMock()
    proc.poll.return_value = None

    with (
        patch.object(tracker, "_wait_then_kill"),
        patch("launcher.tracker_process.threading.Thread") as thread,
    ):
        tracker._begin_termination(proc)

    assert thread.call_args.kwargs["daemon"] is False
