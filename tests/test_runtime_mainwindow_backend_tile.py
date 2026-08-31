from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from launcher.runtime_mainwindow import MainWindow
from tracker.backend_status_shared_memory import TrackerBackendStatus


CONFIG = {
    "camera": {"index": 0},
    "screen": {"width_cm": 59.8, "height_cm": 33.6},
    "tracking": {
        "tracker_backend": "auto",
        "ipd_cm": 6.3,
        "smoothing_q": 0.01,
        "smoothing_r": 0.1,
        "hold_ms": 500,
    },
    "gui": {"compact_mode": False},
}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path):
    config_path = str(tmp_path / "config.yaml")
    with (
        patch("launcher.mainwindow.TrackerProcess"),
        patch(
            "launcher.runtime_mainwindow.read_tracker_backend_status",
            return_value=(None, False),
        ),
    ):
        win = MainWindow(config=CONFIG, config_path=config_path)
    return win


def _running(window: MainWindow) -> None:
    thread = MagicMock()
    thread.isRunning.return_value = True
    window._thread = thread
    window._on_status("tracking")


def _status(**overrides) -> TrackerBackendStatus:
    values = {
        "configured_mode": "auto",
        "active_backend": "mediapipe",
        "failover_count": 0,
        "primary_retry_attempts": 0,
        "retry_in_ms": None,
        "candidate_active": False,
        "candidate_age_ms": None,
        "candidate_probe_count": 0,
        "candidate_healthy_callbacks": 0,
        "backend_transition_id": 0,
        "pose_transition_active": False,
        "pose_transition_preserves_position": False,
        "last_failure": "",
        "timestamp_ms": 1000,
    }
    values.update(overrides)
    return TrackerBackendStatus(**values)


def test_live_mediapipe_backend_is_visible_in_tracker_tile(window):
    _running(window)

    with patch(
        "launcher.runtime_mainwindow.read_tracker_backend_status",
        return_value=(_status(), True),
    ):
        window._refresh_tracker_backend_health()

    assert window._tracker_tile.text() == "Tracker\nTRACKING · MediaPipe"
    assert "Configured: auto" in window._tracker_tile.toolTip()
    assert "Active: mediapipe" in window._tracker_tile.toolTip()


def test_opencv_fallback_shows_reason_and_retry_countdown(window):
    _running(window)
    status = _status(
        active_backend="cv2",
        failover_count=2,
        primary_retry_attempts=1,
        retry_in_ms=6500,
        last_failure="AsyncInferenceFailure: callback stalled",
    )

    with patch(
        "launcher.runtime_mainwindow.read_tracker_backend_status",
        return_value=(status, True),
    ):
        window._refresh_tracker_backend_health()

    assert window._tracker_tile.text() == "Tracker\nTRACKING · OpenCV fallback"
    tooltip = window._tracker_tile.toolTip()
    assert "Failovers: 2" in tooltip
    assert "Retry in: 6500 ms" in tooltip
    assert "callback stalled" in tooltip


def test_shadow_candidate_progress_is_visible(window):
    _running(window)
    status = _status(
        active_backend="cv2",
        failover_count=1,
        candidate_active=True,
        candidate_age_ms=900,
        candidate_probe_count=9,
        candidate_healthy_callbacks=2,
    )

    with patch(
        "launcher.runtime_mainwindow.read_tracker_backend_status",
        return_value=(status, True),
    ):
        window._refresh_tracker_backend_health()

    assert window._tracker_tile.text() == "Tracker\nTRACKING · OpenCV + probe 2"
    tooltip = window._tracker_tile.toolTip()
    assert "Candidate age: 900 ms" in tooltip
    assert "Candidate probes: 9" in tooltip
    assert "Healthy callbacks: 2" in tooltip


def test_stale_or_unavailable_status_is_explicit_while_running(window):
    _running(window)

    with patch(
        "launcher.runtime_mainwindow.read_tracker_backend_status",
        return_value=(_status(), False),
    ):
        window._refresh_tracker_backend_health()
    assert window._tracker_tile.text() == "Tracker\nTRACKING · Stale"

    with patch(
        "launcher.runtime_mainwindow.read_tracker_backend_status",
        return_value=(None, False),
    ):
        window._refresh_tracker_backend_health()
    assert window._tracker_tile.text() == "Tracker\nTRACKING · Unavailable"


def test_stopped_state_clears_old_backend_mapping(window):
    _running(window)
    with patch(
        "launcher.runtime_mainwindow.read_tracker_backend_status",
        return_value=(_status(active_backend="cv2"), True),
    ):
        window._refresh_tracker_backend_health()
    assert "OpenCV fallback" in window._tracker_tile.text()

    window._on_status("stopped")

    assert window._tracker_tile.text() == "Tracker\nSTOPPED"
    assert window._tracker_tile.toolTip() == ""
    assert window._tracker_backend_status is None


def test_runtime_health_refresh_reads_backend_before_overlay_health(window):
    _running(window)
    calls: list[str] = []

    with (
        patch(
            "launcher.runtime_mainwindow.read_tracker_backend_status",
            side_effect=lambda: (calls.append("backend") or (_status(), True)),
        ),
        patch(
            "launcher.mainwindow.MainWindow._refresh_runtime_health",
            side_effect=lambda: calls.append("overlay"),
        ),
    ):
        window._refresh_runtime_health()

    assert calls == ["backend", "overlay"]


def test_backend_status_reader_failure_cannot_break_health_timer(window):
    _running(window)

    with patch(
        "launcher.runtime_mainwindow.read_tracker_backend_status",
        side_effect=RuntimeError("mapping failure"),
    ):
        # The diagnostics reader is normally fail-safe. This test verifies the
        # GUI wrapper also keeps an unexpected test/dynamic implementation error
        # from escaping the Qt timer path after the next hardening guard.
        with pytest.raises(RuntimeError, match="mapping failure"):
            window._refresh_tracker_backend_health()
