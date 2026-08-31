"""Operator-facing MainWindow enhancements backed by runtime diagnostics."""
from __future__ import annotations

from typing import Optional

from launcher.mainwindow import MainWindow as _BaseMainWindow, _STATUS_TEXT
from launcher.tracker_backend_diagnostics import (
    read_tracker_backend_status,
    tracker_backend_tile_text,
)
from tracker.backend_status_shared_memory import TrackerBackendStatus


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
        super().__init__(config=config, config_path=config_path, parent=parent)

    @staticmethod
    def _plain_tracker_status(status: str) -> str:
        text = _STATUS_TEXT.get(status, f"● {status.upper()}")
        return (
            text.replace("● ", "")
            .replace("⟳ ", "")
            .replace("✕ ", "")
        )

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
        status, fresh = read_tracker_backend_status()
        label, tooltip = tracker_backend_tile_text(status, fresh=fresh)
        self._tracker_backend_status = status
        self._tracker_backend_status_fresh = fresh
        self._tracker_backend_label = label
        self._tracker_backend_tooltip = tooltip
        self._render_tracker_tile()

    def _on_status(self, status: str) -> None:
        super()._on_status(status)
        if status in {"stopped", "error"}:
            self._clear_tracker_backend_tile()
        else:
            self._render_tracker_tile()

    def _refresh_runtime_health(self) -> None:
        self._refresh_tracker_backend_health()
        super()._refresh_runtime_health()
