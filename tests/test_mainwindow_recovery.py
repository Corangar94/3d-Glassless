from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from launcher import diagnostics
from launcher.mainwindow import MainWindow
from launcher.overlay_process import OverlayStartError
from launcher.runtime_supervisor import RecoverySnapshot


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


def _healthy_summary() -> diagnostics.OverlayRuntimeSummary:
    return diagnostics.OverlayRuntimeSummary(
        frame_count=120,
        acq_ok=118,
        acq_timeout=2,
        acq_lost=0,
        acq_other=0,
        shm_status="LIVE",
        shm_changes_per_sec=30,
        depth_total=60,
        depth_hz=15,
        head_z_cm=60.0,
        has_frame=True,
        capture_state="running",
        capture_reason="bound_desktop",
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


def test_nonretryable_overlay_start_error_stops_without_crash_loop(window):
    tracker = MagicMock()
    tracker.isRunning.return_value = True
    window._thread = tracker
    window._runtime_requested = True
    window._overlay = MagicMock()
    window._overlay.start.side_effect = OverlayStartError("runtime assets missing")

    window._on_tracker_status_for_overlay("tracking")

    assert window._runtime_requested is False
    assert window._tracker_recovery_pending is False
    assert window._overlay_recovery_pending is False
    assert window._recovery.snapshot("tracker").failure_count == 0
    assert window._recovery.snapshot("overlay").failure_count == 0
    tracker.stop.assert_called_once()
    assert "OVERLAY ERROR" in window._status_label.text()


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


def test_paused_primary_action_runs_manual_recovery_instead_of_stop(window):
    window._recovery_paused = True
    window._runtime_requested = False
    window._thread = MagicMock()
    window._thread.isRunning.return_value = True

    with (
        patch.object(window, "_manual_recover_runtime") as recover,
        patch.object(window, "_stop_tracking") as stop,
    ):
        window._toggle_tracking()

    recover.assert_called_once()
    stop.assert_not_called()


def test_manual_stop_cancels_pending_recovery_generation(window):
    window._runtime_requested = True
    window._tracker_recovery_pending = True
    window._overlay_recovery_pending = True
    previous_generation = window._recovery_generation
    window._thread = None
    window._overlay = MagicMock()

    window._stop_tracking()

    assert window._runtime_requested is False
    assert window._recovery_paused is False
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
    assert window._overlay_recovery_pending is False


def test_native_recovery_cancels_queued_delayed_overlay_restart(window):
    tracker = MagicMock()
    tracker.isRunning.return_value = True
    window._thread = tracker
    window._runtime_requested = True
    window._overlay_started = True
    window._overlay = MagicMock()
    window._overlay.is_running.return_value = True
    window._overlay.is_transitioning.return_value = False
    window._active_profile = MagicMock(executable_path=r"C:\Games\Game.exe")
    window._recovery.record_failure("overlay", "first episode")

    with patch("launcher.mainwindow.QTimer.singleShot") as single_shot:
        window._restart_overlay_from_health("second episode")

    assert window._overlay_recovery_pending is True
    single_shot.assert_called_once()
    generation = window._recovery_generation

    window._apply_runtime_health(_healthy_summary())
    assert window._overlay_recovery_pending is False

    window._overlay.restart_async.reset_mock()
    window._execute_overlay_recovery(generation, "stale timer")
    window._overlay.restart_async.assert_not_called()


def test_open_circuit_pauses_automatic_runtime_and_surfaces_retry(window):
    tracker = MagicMock()
    tracker.isRunning.return_value = True
    window._thread = tracker
    window._runtime_requested = True
    window._overlay = MagicMock()
    window._hidden_for_overlay = True
    window.showNormal = MagicMock()

    with patch("launcher.mainwindow.QTimer.singleShot") as single_shot:
        window._pause_recovery("overlay", "repeated crash", 60.0)

    assert window._runtime_requested is False
    assert window._recovery_paused is True
    assert window._action_btn.text() == "↻ RETRY RUNTIME"
    assert "RECOVERY PAUSED" in window._status_label.text()
    assert "repeated crash" in window._status_label.toolTip()
    assert "overrides the cooldown" in window._status_label.toolTip()
    tracker.stop.assert_called_once()
    window.showNormal.assert_called_once()
    single_shot.assert_called_once()


def test_cooldown_expiry_automatically_restarts_full_runtime(window):
    window._runtime_requested = False
    window._recovery_paused = True
    generation = window._recovery_generation
    snapshot = RecoverySnapshot(
        component="overlay",
        failure_count=0,
        consecutive_failures=0,
        circuit_open=False,
        retry_after_s=0.0,
        last_reason="",
        stable_for_s=0.0,
    )

    with (
        patch.object(window._recovery, "snapshot", return_value=snapshot),
        patch.object(window, "_execute_tracker_recovery") as execute,
    ):
        window._resume_recovery_after_cooldown(generation, "overlay")

    assert window._recovery_paused is False
    assert window._runtime_requested is True
    assert "CANCEL RECOVERY" in window._action_btn.text()
    execute.assert_called_once_with(generation)


def test_stale_cooldown_timer_is_cancelled_by_generation(window):
    window._runtime_requested = False
    window._recovery_paused = True

    with patch.object(window, "_execute_tracker_recovery") as execute:
        window._resume_recovery_after_cooldown(
            window._recovery_generation - 1,
            "tracker",
        )

    execute.assert_not_called()
    assert window._runtime_requested is False
    assert window._recovery_paused is True


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
