from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

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


@pytest.fixture
def window(qapp, tmp_path):
    with patch("launcher.mainwindow.TrackerProcess"):
        return MainWindow(
            config=CONFIG,
            config_path=str(tmp_path / "config.yaml"),
        )


def test_runtime_panel_exposes_one_click_recovery(window):
    labels = {button.text() for button in window.findChildren(QPushButton)}
    assert "Recover runtime" in labels


def test_tracker_error_restarts_when_user_still_wants_runtime(window):
    tracker = MagicMock()
    tracker.isRunning.return_value = True
    window._thread = tracker
    window._runtime_requested = True
    window._overlay = MagicMock()

    with patch.object(window, "_start_tracking") as restart:
        window._on_status("error")
        tracker.stop.assert_called_once()
        window._on_tracker_stopped(tracker)
        QTest.qWait(10)

    restart.assert_called_once_with(recovery=True)
    assert window._tracker_recovery_pending is False


def test_manual_recovery_resets_circuit_and_starts_when_stopped(window):
    for _ in range(window._recovery.policy.max_failures):
        window._recovery.record_failure("tracker", "camera failed")
    assert window._recovery.snapshot("tracker").circuit_open
    window._thread = None

    with patch.object(window, "_start_tracking") as start:
        window._manual_recover_runtime()
        QTest.qWait(10)

    assert not window._recovery.snapshot("tracker").circuit_open
    start.assert_called_once_with(recovery=True)
    assert window._runtime_requested is True


def test_manual_stop_cancels_pending_recovery_generation(window):
    window._runtime_requested = True
    window._tracker_recovery_pending = True
    window._overlay_recovery_pending = True
    previous_generation = window._recovery_generation
    window._thread = None
    window._overlay = MagicMock()

    window._stop_tracking()

    assert window._runtime_requested is False
    assert window._tracker_recovery_pending is False
    assert window._overlay_recovery_pending is False
    assert window._recovery_generation == previous_generation + 1


def test_first_overlay_failure_keeps_immediate_recovery_behavior(window):
    tracker = MagicMock()
    tracker.isRunning.return_value = True
    window._thread = tracker
    window._runtime_requested = True
    window._overlay = MagicMock()
    window._active_profile = MagicMock(executable_path=r"C:\Games\Game.exe")

    window._restart_overlay_from_health("process exited")

    window._overlay.restart_async.assert_called_once_with(r"C:\Games\Game.exe")
    assert window._overlay_started is True


def test_open_circuit_pauses_automatic_runtime_and_surfaces_retry(window):
    window._runtime_requested = True
    window._overlay = MagicMock()
    window._hidden_for_overlay = True
    window.showNormal = MagicMock()

    window._pause_recovery("overlay", "repeated crash", 60.0)

    assert window._runtime_requested is False
    assert window._action_btn.text() == "↻ RETRY RUNTIME"
    assert "RECOVERY PAUSED" in window._status_label.text()
    assert "repeated crash" in window._status_label.toolTip()
    window.showNormal.assert_called_once()


def test_stable_overlay_health_resets_failure_episode(window):
    window._runtime_requested = True
    window._overlay_started = True
    window._thread = MagicMock()
    window._thread.isRunning.return_value = True
    window._overlay = MagicMock()
    window._overlay.is_transitioning.return_value = False
    window._overlay.is_running.return_value = True
    window._recovery.record_failure("overlay", "temporary loss", now_s=0.0)
    window._recovery.mark_healthy("overlay", now_s=1.0)
    window._recovery.mark_healthy(
        "overlay",
        now_s=1.0 + window._recovery.policy.stable_reset_s,
    )

    snapshot = window._recovery.snapshot(
        "overlay",
        now_s=2.0 + window._recovery.policy.stable_reset_s,
    )
    assert snapshot.failure_count == 0
    assert snapshot.consecutive_failures == 0
