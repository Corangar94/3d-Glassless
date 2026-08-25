from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from launcher.mainwindow import MainWindow


CONFIG = {
    "camera": {"index": 0},
    "screen": {"width_cm": 59.8, "height_cm": 33.6},
    "tracking": {
        "ipd_cm": 6.3,
        "smoothing_q": 2.0,
        "smoothing_r": 0.1,
        "hold_ms": 500,
    },
    "overlay": {"depth_performance_mode": "auto"},
    "gui": {"compact_mode": False},
}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_stopped_signal_callback_binds_original_tracker_owner(qapp, tmp_path):
    tracker = MagicMock()
    tracker.start.return_value = True
    tracker.isRunning.return_value = True
    with patch("launcher.mainwindow.TrackerProcess", return_value=tracker):
        window = MainWindow(
            config=CONFIG,
            config_path=str(tmp_path / "config.yaml"),
        )
        window._start_tracking()

    callback = tracker.stopped.connect.call_args.args[0]
    replacement = MagicMock()
    replacement.isRunning.return_value = True
    window._thread = replacement

    callback()

    assert window._thread is replacement
    assert window._tracker_stop_pending is False


def test_bound_stopped_callback_retires_the_matching_tracker(qapp, tmp_path):
    tracker = MagicMock()
    tracker.start.return_value = True
    tracker.isRunning.return_value = True
    with patch("launcher.mainwindow.TrackerProcess", return_value=tracker):
        window = MainWindow(
            config=CONFIG,
            config_path=str(tmp_path / "config.yaml"),
        )
        window._start_tracking()

    callback = tracker.stopped.connect.call_args.args[0]
    window._runtime_requested = False
    callback()

    assert window._thread is None
    assert window._tracker_stop_pending is False
    assert window._action_btn.text() == "▶ START TRACKING"
