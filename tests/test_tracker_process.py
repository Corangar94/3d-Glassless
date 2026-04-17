from unittest.mock import patch

import pytest
from PySide6.QtTest import QSignalSpy
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


def test_tracker_process_module_has_qapplication(qapp):
    assert QApplication.instance() is not None
