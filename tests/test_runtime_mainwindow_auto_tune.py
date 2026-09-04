from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from launcher import runtime_mainwindow
from launcher.auto_tune import TrackingAutoTuner
from launcher.auto_tune_timeline import AutoTuneSampleTimeline
from launcher.runtime_mainwindow import (
    MainWindow,
    _TimestampedAutoTuner,
)


class _Signal:
    def __init__(self) -> None:
        self.connections: list[object] = []

    def connect(self, callback: object) -> None:
        self.connections.append(callback)

    def disconnect(self, callback: object) -> None:
        try:
            self.connections.remove(callback)
        except ValueError as error:
            raise RuntimeError("callback is not connected") from error


class _Tracker:
    def __init__(self) -> None:
        self.position_updated = _Signal()
        self.position_sampled = _Signal()


def _window(*, status: str = "initializing") -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    window._tracking_status = status
    window._auto_tuner = MagicMock()
    window._auto_tune_sample_timeline = AutoTuneSampleTimeline()
    window._last_auto_tune_write_s = 123.0
    window._auto_tune_status = MagicMock()
    return window


def test_entering_tracking_resets_tuner_clock_and_write_throttle():
    window = _window(status="initializing")
    window._auto_tune_sample_timeline.accept(5000)

    reset = window._reset_auto_tuner_on_tracking_boundary(
        "initializing",
        "tracking",
    )

    assert reset
    window._auto_tuner.reset.assert_called_once_with()
    snapshot = window._auto_tune_sample_timeline.snapshot()
    assert snapshot.reset_count == 1
    assert snapshot.last_wire_timestamp_ms is None
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
    assert window._auto_tune_sample_timeline.snapshot().reset_count == 1
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
    assert window._auto_tune_sample_timeline.snapshot().reset_count == 0
    assert window._last_auto_tune_write_s == 123.0


def test_missing_or_legacy_tuner_remains_compatible():
    window = _window()
    window._auto_tuner = object()

    assert not window._reset_auto_tuner_on_tracking_boundary(
        "paused",
        "tracking",
    )
    # The producer clock can still be cleared independently even when a legacy
    # owner has no resettable tuner object.
    assert window._auto_tune_sample_timeline.snapshot().reset_count == 1
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
    assert window._auto_tune_sample_timeline.snapshot().reset_count == 1
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
    assert window._auto_tune_sample_timeline.snapshot().reset_count == 2
    assert window._last_auto_tune_write_s == 0.0
    window._auto_tune_status.setText.assert_called_once_with(
        "Auto tuning is calibrating this tracking episode"
    )


def test_timestamp_adapter_substitutes_one_producer_time_then_falls_back():
    delegate = MagicMock()
    delegate.update.side_effect = ["producer", "fallback"]
    adapter = _TimestampedAutoTuner(delegate)

    adapter.arm(10.033)
    assert adapter.update(1.0, 2.0, 60.0, 999.0) == "producer"
    assert adapter.update(2.0, 2.0, 60.0, 1000.0) == "fallback"

    first_args = delegate.update.call_args_list[0].args
    second_args = delegate.update.call_args_list[1].args
    assert first_args[:3] == (1.0, 2.0, 60.0)
    assert first_args[3] == pytest.approx(10.033)
    assert second_args == (2.0, 2.0, 60.0, 1000.0)


def test_timestamp_adapter_reset_clears_armed_sample():
    delegate = MagicMock()
    adapter = _TimestampedAutoTuner(delegate)
    adapter.arm(10.0)

    adapter.reset()
    adapter.update(1.0, 2.0, 60.0, 77.0)

    delegate.reset.assert_called_once_with()
    delegate.update.assert_called_once_with(1.0, 2.0, 60.0, 77.0)


def test_producer_cadence_prevents_false_ui_gap_episode_reset():
    producer_tuner = TrackingAutoTuner()
    producer_adapter = _TimestampedAutoTuner(producer_tuner)
    producer_adapter.arm(1.000)
    first = producer_adapter.update(0.0, 0.0, 60.0, 100.0)
    producer_adapter.arm(1.033)
    second = producer_adapter.update(1.0, 0.0, 60.0, 999.0)

    assert producer_tuner.episode_reset_count == 0
    assert second.speed_cm_s > 0.0
    assert second.smoothing_alpha < first.smoothing_alpha

    # The same positions retimed at delayed Qt receipt would look like a long
    # gap, reset the episode, and erase the measured motion.
    receipt_tuner = TrackingAutoTuner()
    receipt_tuner.update(0.0, 0.0, 60.0, 100.0)
    receipt_result = receipt_tuner.update(1.0, 0.0, 60.0, 999.0)
    assert receipt_tuner.episode_reset_count == 1
    assert receipt_result.speed_cm_s == pytest.approx(0.0)


def test_timestamped_slot_uses_producer_time_not_qt_fallback(monkeypatch):
    window = _window(status="tracking")
    window._auto_tune_enabled = True
    delegate = MagicMock()
    window._auto_tuner = _TimestampedAutoTuner(delegate)

    monkeypatch.setattr(
        runtime_mainwindow._BaseMainWindow,
        "_on_position",
        lambda owner, x, y, z: owner._auto_tuner.update(
            x,
            y,
            z,
            9999.0,
        ),
    )

    window._on_timestamped_position(0.0, 0.0, 60.0, 1000)
    window._on_timestamped_position(1.0, 0.0, 60.0, 1033)

    timestamps = [call.args[3] for call in delegate.update.call_args_list]
    assert timestamps == pytest.approx([1.0, 1.033])


def test_duplicate_or_backward_producer_time_drops_whole_pose(monkeypatch):
    window = _window(status="tracking")
    window._auto_tune_enabled = True
    window._auto_tuner = _TimestampedAutoTuner(MagicMock())
    base_slot = MagicMock()
    monkeypatch.setattr(
        runtime_mainwindow._BaseMainWindow,
        "_on_position",
        lambda owner, x, y, z: base_slot(owner, x, y, z),
    )

    window._on_timestamped_position(0.0, 0.0, 60.0, 1000)
    window._on_timestamped_position(99.0, 0.0, 60.0, 1000)
    window._on_timestamped_position(88.0, 0.0, 60.0, 999)

    base_slot.assert_called_once_with(window, 0.0, 0.0, 60.0)
    assert window._auto_tune_sample_timeline.snapshot().rejected_count == 2


def test_timestamped_slot_disarms_override_when_base_slot_raises(monkeypatch):
    window = _window(status="tracking")
    window._auto_tune_enabled = True
    delegate = MagicMock()
    adapter = _TimestampedAutoTuner(delegate)
    window._auto_tuner = adapter
    monkeypatch.setattr(
        runtime_mainwindow._BaseMainWindow,
        "_on_position",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("ui failed")),
    )

    with pytest.raises(RuntimeError, match="ui failed"):
        window._on_timestamped_position(0.0, 0.0, 60.0, 1000)

    adapter.update(1.0, 0.0, 60.0, 22.0)
    delegate.update.assert_called_once_with(1.0, 0.0, 60.0, 22.0)


def test_timestamped_signal_replaces_legacy_connection_without_duplication():
    window = _window(status="tracking")
    tracker = _Tracker()
    tracker.position_updated.connect(window._on_position)

    assert window._bind_timestamped_pose_signal(tracker)

    assert tracker.position_updated.connections == []
    assert tracker.position_sampled.connections == [
        window._on_timestamped_position
    ]


def test_failed_selective_disconnect_keeps_legacy_fallback():
    window = _window(status="tracking")
    tracker = _Tracker()

    assert not window._bind_timestamped_pose_signal(tracker)
    assert tracker.position_updated.connections == []
    assert tracker.position_sampled.connections == []


def test_false_disconnect_result_keeps_legacy_fallback():
    window = _window(status="tracking")
    tracker = _Tracker()
    tracker.position_updated.connect(window._on_position)
    tracker.position_updated.disconnect = MagicMock(return_value=False)

    assert not window._bind_timestamped_pose_signal(tracker)
    assert tracker.position_sampled.connections == []


def test_sampled_connect_failure_restores_legacy_connection():
    window = _window(status="tracking")
    tracker = _Tracker()
    tracker.position_updated.connect(window._on_position)
    tracker.position_sampled.connect = MagicMock(
        side_effect=RuntimeError("sampled signal unavailable")
    )

    assert not window._bind_timestamped_pose_signal(tracker)
    assert tracker.position_updated.connections == [window._on_position]


def test_failed_sampled_and_legacy_reconnect_does_not_break_startup():
    window = _window(status="tracking")
    tracker = _Tracker()
    tracker.position_updated.connect(window._on_position)
    tracker.position_sampled.connect = MagicMock(
        side_effect=RuntimeError("sampled signal unavailable")
    )
    tracker.position_updated.connect = MagicMock(
        side_effect=RuntimeError("legacy reconnect unavailable")
    )

    assert not window._bind_timestamped_pose_signal(tracker)
    assert tracker.position_updated.connections == []


def test_start_tracking_binds_timestamped_signal_after_base_start(monkeypatch):
    window = _window(status="stopped")
    tracker = _Tracker()

    def base_start(owner, *, recovery=False):
        assert recovery
        tracker.position_updated.connect(owner._on_position)
        owner._thread = tracker

    monkeypatch.setattr(
        runtime_mainwindow._BaseMainWindow,
        "_start_tracking",
        base_start,
    )

    window._start_tracking(recovery=True)

    assert tracker.position_updated.connections == []
    assert tracker.position_sampled.connections == [
        window._on_timestamped_position
    ]


def test_toggle_reinstalls_adapter_and_resets_producer_clock(monkeypatch):
    window = _window(status="stopped")
    window._auto_tune_sample_timeline.accept(1000)
    replacement = MagicMock()

    monkeypatch.setattr(
        runtime_mainwindow._BaseMainWindow,
        "_on_auto_tune_toggle",
        lambda owner, checked: setattr(owner, "_auto_tuner", replacement),
    )

    window._on_auto_tune_toggle(True)

    assert isinstance(window._auto_tuner, _TimestampedAutoTuner)
    assert window._auto_tuner.delegate is replacement
    snapshot = window._auto_tune_sample_timeline.snapshot()
    assert snapshot.reset_count == 1
    assert snapshot.last_wire_timestamp_ms is None
