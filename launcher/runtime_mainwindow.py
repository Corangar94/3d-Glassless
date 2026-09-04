"""Operator-facing MainWindow enhancements backed by runtime diagnostics."""
from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Optional

from launcher.auto_tune_publication import (
    AutoTunePublicationSnapshot,
    AutoTunePublicationWriter,
)
from launcher.auto_tune_timeline import AutoTuneSampleTimeline
from launcher.mainwindow import MainWindow as _BaseMainWindow, _STATUS_TEXT
from launcher.tracker_backend_diagnostics import (
    read_tracker_backend_status,
    tracker_backend_tile_text,
)
from tracker.backend_status_shared_memory import TrackerBackendStatus


_NO_TIMESTAMP = object()


class _TimestampedAutoTuner:
    """Substitute one producer timestamp while preserving base-window logic."""

    def __init__(self, delegate: object) -> None:
        self._delegate = delegate
        self._pending_timestamp_s: object = _NO_TIMESTAMP

    @property
    def delegate(self) -> object:
        return self._delegate

    def arm(self, timestamp_s: float) -> None:
        self._pending_timestamp_s = float(timestamp_s)

    def disarm(self) -> None:
        self._pending_timestamp_s = _NO_TIMESTAMP

    def update(
        self,
        x_cm: float,
        y_cm: float,
        z_cm: float,
        fallback_timestamp_s: float,
    ) -> Any:
        pending = self._pending_timestamp_s
        # One pose consumes one override even when the delegate raises.
        self._pending_timestamp_s = _NO_TIMESTAMP
        timestamp_s = (
            fallback_timestamp_s
            if pending is _NO_TIMESTAMP
            else float(pending)
        )
        return self._delegate.update(
            x_cm,
            y_cm,
            z_cm,
            timestamp_s,
        )

    def reset(self) -> Any:
        self.disarm()
        return self._delegate.reset()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class MainWindow(_BaseMainWindow):
    """Add live tracker-backend state without enlarging the base window module."""

    def __init__(
        self,
        config: dict,
        config_path: str,
        parent: Optional[object] = None,
    ) -> None:
        # Base initialization invokes virtual status/health methods, so establish
        # these fields before delegating to it.
        self._tracker_backend_status: TrackerBackendStatus | None = None
        self._tracker_backend_status_fresh = False
        self._tracker_backend_label = ""
        self._tracker_backend_tooltip = ""
        self._auto_tune_sample_timeline = AutoTuneSampleTimeline()
        self._auto_tune_publication_writer: AutoTunePublicationWriter | None = None
        super().__init__(config=config, config_path=config_path, parent=parent)
        self._install_timestamped_auto_tuner()
        self._install_auto_tune_publication_writer()

    @staticmethod
    def _plain_tracker_status(status: str) -> str:
        text = _STATUS_TEXT.get(status, f"● {status.upper()}")
        return (
            text.replace("● ", "")
            .replace("⟳ ", "")
            .replace("✕ ", "")
        )

    def _install_timestamped_auto_tuner(self) -> bool:
        tuner = getattr(self, "_auto_tuner", None)
        if isinstance(tuner, _TimestampedAutoTuner):
            return True
        if not callable(getattr(tuner, "update", None)):
            return False
        self._auto_tuner = _TimestampedAutoTuner(tuner)
        return True

    def _install_auto_tune_publication_writer(self) -> bool:
        writer = getattr(self, "_settings_writer", None)
        if isinstance(writer, AutoTunePublicationWriter):
            self._auto_tune_publication_writer = writer
            return True
        if not callable(getattr(writer, "write", None)):
            return False
        publication = AutoTunePublicationWriter(writer)
        self._settings_writer = publication
        self._auto_tune_publication_writer = publication
        settings = getattr(self, "_settings", None)
        if settings is not None:
            # Base initialization already published this snapshot before the
            # wrapper was installed, so align the coalescing baseline with it.
            publication.seed(settings)
        return True

    def auto_tune_publication_snapshot(
        self,
    ) -> AutoTunePublicationSnapshot | None:
        writer = getattr(self, "_auto_tune_publication_writer", None)
        snapshot = getattr(writer, "publication_snapshot", None)
        return snapshot() if callable(snapshot) else None

    def _reset_auto_tune_publication(self) -> None:
        writer = getattr(self, "_auto_tune_publication_writer", None)
        reset = getattr(writer, "reset_publication", None)
        if callable(reset):
            reset()

    def _auto_tune_publication_context(self):
        writer = getattr(self, "_auto_tune_publication_writer", None)
        arm = getattr(writer, "auto_tune_write", None)
        if (
            self._auto_tune_enabled
            and self._tracking_status == "tracking"
            and callable(arm)
        ):
            return arm()
        return nullcontext()

    def _dispatch_position_with_publication_gate(
        self,
        x_cm: float,
        y_cm: float,
        z_cm: float,
    ) -> None:
        # The base slot keeps its established 250 ms attempt throttle. Only the
        # shared-settings write reached from that slot is marked as auto-tune.
        with self._auto_tune_publication_context():
            super()._on_position(x_cm, y_cm, z_cm)

    def _on_position(self, x: float, y: float, z: float) -> None:
        """Retain the legacy signal path with auto-tune write coalescing."""
        self._dispatch_position_with_publication_gate(x, y, z)

    def _bind_timestamped_pose_signal(self, tracker: object) -> bool:
        """Prefer producer-timestamped samples without duplicating pose updates."""
        sampled = getattr(tracker, "position_sampled", None)
        connect_sampled = getattr(sampled, "connect", None)
        legacy = getattr(tracker, "position_updated", None)
        disconnect_legacy = getattr(legacy, "disconnect", None)
        connect_legacy = getattr(legacy, "connect", None)
        if not callable(connect_sampled) or not callable(disconnect_legacy):
            return False
        try:
            disconnected = disconnect_legacy(self._on_position)
        except (RuntimeError, TypeError):
            # Keep the already-connected legacy path rather than risk duplicate
            # position handling when an unfamiliar signal implementation refuses
            # selective disconnection.
            return False
        if disconnected is False:
            return False
        try:
            connect_sampled(self._on_timestamped_position)
        except (RuntimeError, TypeError):
            if callable(connect_legacy):
                try:
                    connect_legacy(self._on_position)
                except (RuntimeError, TypeError):
                    # A dynamically patched/legacy signal may reject both
                    # operations. Leave startup alive; the tracker process and
                    # overlay continue even if launcher pose telemetry is absent.
                    pass
            return False
        return True

    def _start_tracking(self, *, recovery: bool = False) -> None:
        super()._start_tracking(recovery=recovery)
        tracker = getattr(self, "_thread", None)
        if tracker is not None:
            self._bind_timestamped_pose_signal(tracker)

    def _on_timestamped_position(
        self,
        x_cm: float,
        y_cm: float,
        z_cm: float,
        publish_timestamp_ms: object,
    ) -> None:
        """Feed the tuner producer time while retaining local write throttling."""
        sample_time_s = self._auto_tune_sample_timeline.accept(
            publish_timestamp_ms
        )
        if sample_time_s is None:
            # Never turn a duplicate/backward/malformed producer sample into new
            # motion by assigning it the current Qt callback time.
            return

        tuner = getattr(self, "_auto_tuner", None)
        arm = getattr(tuner, "arm", None)
        disarm = getattr(tuner, "disarm", None)
        should_arm = bool(
            self._auto_tune_enabled
            and self._tracking_status == "tracking"
            and callable(arm)
        )
        if should_arm:
            arm(sample_time_s)
        try:
            # Producer time changes only the tuner's motion estimate. The
            # publication gate and the base 250 ms throttle use launcher time.
            self._dispatch_position_with_publication_gate(
                x_cm,
                y_cm,
                z_cm,
            )
        finally:
            if should_arm and callable(disarm):
                disarm()

    def _on_auto_tune_toggle(self, checked: bool) -> None:
        super()._on_auto_tune_toggle(checked)
        self._auto_tune_sample_timeline.reset()
        self._install_timestamped_auto_tuner()
        self._install_auto_tune_publication_writer()
        self._reset_auto_tune_publication()

    def _render_tracker_tile(self) -> None:
        tile = getattr(self, "_tracker_tile", None)
        if tile is None:
            return
        value = self._plain_tracker_status(self._tracking_status)
        if self._tracker_backend_label:
            value = f"{value} · {self._tracker_backend_label}"
        tile.setText(f"Tracker\n{value}")
        tile.setToolTip(self._tracker_backend_tooltip)

    def _clear_tracker_backend_tile(self) -> None:
        self._tracker_backend_status = None
        self._tracker_backend_status_fresh = False
        self._tracker_backend_label = ""
        self._tracker_backend_tooltip = ""
        self._render_tracker_tile()

    def _refresh_tracker_backend_health(self) -> None:
        # Named mappings can outlive a just-finished child briefly. Never show an
        # old process as current when this launcher does not own a running tracker.
        if (
            self._tracking_status in {"stopped", "error"}
            or not self._tracker_is_running()
        ):
            self._clear_tracker_backend_tile()
            return
        try:
            status, fresh = read_tracker_backend_status()
            label, tooltip = tracker_backend_tile_text(
                status,
                fresh=fresh,
            )
        except Exception:
            # The Qt health timer must remain alive even if a dynamically patched
            # reader or unexpected platform boundary violates its fail-safe API.
            status, fresh = None, False
            label, tooltip = (
                "Unavailable",
                "Tracker backend status could not be read",
            )
        self._tracker_backend_status = status
        self._tracker_backend_status_fresh = fresh
        self._tracker_backend_label = label
        self._tracker_backend_tooltip = tooltip
        self._render_tracker_tile()

    def _reset_auto_tuner_on_tracking_boundary(
        self,
        previous_status: object,
        current_status: object,
    ) -> bool:
        """Start a fresh auto-tuning episode when tracking starts or stops."""
        previous = str(previous_status or "").strip().lower()
        current = str(current_status or "").strip().lower()
        if previous == current or "tracking" not in {previous, current}:
            return False
        timeline = getattr(self, "_auto_tune_sample_timeline", None)
        reset_timeline = getattr(timeline, "reset", None)
        if callable(reset_timeline):
            reset_timeline()
        self._reset_auto_tune_publication()
        tuner = getattr(self, "_auto_tuner", None)
        reset = getattr(tuner, "reset", None)
        if not callable(reset):
            return False
        reset()
        if hasattr(self, "_last_auto_tune_write_s"):
            # Let the first accepted pose of a new episode publish its stable
            # distance/smoothing values immediately instead of inheriting the
            # previous episode's write-throttle deadline.
            self._last_auto_tune_write_s = 0.0
        label = getattr(self, "_auto_tune_status", None)
        set_text = getattr(label, "setText", None)
        if current == "tracking" and callable(set_text):
            set_text("Auto tuning is calibrating this tracking episode")
        return True

    def _on_status(self, status: str) -> None:
        previous_status = getattr(self, "_tracking_status", None)
        super()._on_status(status)
        self._reset_auto_tuner_on_tracking_boundary(
            previous_status,
            status,
        )
        if status in {"stopped", "error"}:
            self._clear_tracker_backend_tile()
        elif self._tracker_is_running():
            self._refresh_tracker_backend_health()
        else:
            self._render_tracker_tile()

    def _refresh_runtime_health(self) -> None:
        self._refresh_tracker_backend_health()
        super()._refresh_runtime_health()
