from __future__ import annotations

from unittest.mock import MagicMock

from launcher import runtime_mainwindow
from launcher.runtime_mainwindow import MainWindow


def _window(*, status: str = "initializing") -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    window._tracking_status = status
    window._auto_tuner = MagicMock()
    window._last_auto_tune_write_s = 123.0
    window._auto_tune_status = MagicMock()
    return window


def test_entering_tracking_resets_tuner_and_write_throttle():
    window = _window(status="initializing")

    reset = window._reset_auto_tuner_on_tracking_boundary(
        "initializing",
        "tracking",
    )

    assert reset
    window._auto_tuner.reset.assert_called_once_with()
    assert window._last_auto_tune_write_s == 0.0
    window._auto_tune_status.setText.assert_called_once_with(
        "Auto tuning is calibrating this tracking episode"
    )


def test_leaving_tracking_resets_without_changing_status_label():
    window = _window(status="tracking")

    reset = window._reset_auto_tuner_on_tracking_boundary(
        "tracking",
        "hold",
    )

    assert reset
    window._auto_tuner.reset.assert_called_once_with()
    assert window._last_auto_tune_write_s == 0.0
    window._auto_tune_status.setText.assert_not_called()


def test_nontracking_or_duplicate_transitions_do_not_reset():
    window = _window(status="initializing")

    assert not window._reset_auto_tuner_on_tracking_boundary(
        "initializing",
        "hold",
    )
    assert not window._reset_auto_tuner_on_tracking_boundary(
        "tracking",
        "tracking",
    )

    window._auto_tuner.reset.assert_not_called()
    assert window._last_auto_tune_write_s == 123.0


def test_missing_or_legacy_tuner_remains_compatible():
    window = _window()
    window._auto_tuner = object()

    assert not window._reset_auto_tuner_on_tracking_boundary(
        "paused",
        "tracking",
    )
    assert window._last_auto_tune_write_s == 123.0


def test_on_status_uses_pre_super_status_for_episode_boundary(monkeypatch):
    window = _window(status="initializing")
    window._tracker_is_running = lambda: False
    window._render_tracker_tile = MagicMock()
    window._clear_tracker_backend_tile = MagicMock()

    def base_on_status(owner, status):
        owner._tracking_status = status

    monkeypatch.setattr(
        runtime_mainwindow._BaseMainWindow,
        "_on_status",
        base_on_status,
    )

    window._on_status("tracking")

    assert window._tracking_status == "tracking"
    window._auto_tuner.reset.assert_called_once_with()
    window._render_tracker_tile.assert_called_once_with()


def test_fast_tracking_hold_tracking_round_trip_starts_two_episodes(
    monkeypatch,
):
    window = _window(status="tracking")
    window._tracker_is_running = lambda: False
    window._render_tracker_tile = MagicMock()
    window._clear_tracker_backend_tile = MagicMock()

    monkeypatch.setattr(
        runtime_mainwindow._BaseMainWindow,
        "_on_status",
        lambda owner, status: setattr(owner, "_tracking_status", status),
    )

    window._on_status("hold")
    window._last_auto_tune_write_s = 99.0
    window._on_status("tracking")

    assert window._auto_tuner.reset.call_count == 2
    assert window._last_auto_tune_write_s == 0.0
    window._auto_tune_status.setText.assert_called_once_with(
        "Auto tuning is calibrating this tracking episode"
    )
