from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from launcher import diagnostics
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


def test_periodic_health_tick_advances_tracker_and_overlay_stability(qapp, tmp_path):
    with patch("launcher.mainwindow.TrackerProcess"):
        window = MainWindow(
            config=CONFIG,
            config_path=str(tmp_path / "config.yaml"),
        )
    window._tracking_status = "tracking"
    window._runtime_requested = True
    window._overlay_started = True
    window._thread = MagicMock()
    window._thread.isRunning.return_value = True
    window._overlay = MagicMock()
    window._overlay.is_running.return_value = True
    window._overlay.is_transitioning.return_value = False
    summary = diagnostics.OverlayRuntimeSummary(
        frame_count=120,
        acq_ok=120,
        acq_timeout=0,
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

    with patch.object(window._recovery, "mark_healthy") as mark_healthy:
        window._apply_runtime_health(summary)

    mark_healthy.assert_any_call("tracker")
    mark_healthy.assert_any_call("overlay")


def test_hold_state_does_not_reset_tracker_failure_episode(qapp, tmp_path):
    with patch("launcher.mainwindow.TrackerProcess"):
        window = MainWindow(
            config=CONFIG,
            config_path=str(tmp_path / "config.yaml"),
        )
    window._tracking_status = "hold"
    window._runtime_requested = True
    window._overlay_started = True
    window._thread = MagicMock()
    window._thread.isRunning.return_value = True
    window._overlay = MagicMock()
    window._overlay.is_running.return_value = True
    window._overlay.is_transitioning.return_value = False
    summary = diagnostics.OverlayRuntimeSummary(
        frame_count=120,
        acq_ok=120,
        acq_timeout=0,
        acq_lost=0,
        acq_other=0,
        shm_status="LIVE",
        shm_changes_per_sec=0,
        depth_total=60,
        depth_hz=15,
        head_z_cm=60.0,
        has_frame=True,
        capture_state="running",
        capture_reason="bound_desktop",
    )

    with patch.object(window._recovery, "mark_healthy") as mark_healthy:
        window._apply_runtime_health(summary)

    assert mark_healthy.call_args_list.count((("tracker",), {})) == 0
    mark_healthy.assert_any_call("overlay")
