"""Run the tracker as a child process; surface head-pose data via SHM polling.

Replaces TrackerThread for environments where importing mediapipe inside a
QThread causes a DirectML/COM deadlock.  The subprocess has its own Python
interpreter, so it's completely isolated from Qt's GPU/COM state.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from tracker.shared_memory import SharedMemoryReader

_POLL_MS = 50           # 20 Hz UI refresh
_STALE_MS = 800         # no SHM update for this long → emit "paused"
_INIT_TIMEOUT_S = 45.0  # subprocess hasn't written SHM after this long → "error"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


class TrackerProcess(QObject):
    """Spawns 'python -m tracker' and exposes the same signals as TrackerThread.

    frame_ready is declared for API compatibility but never emits — camera
    preview is not available in subprocess mode.
    """

    position_updated = Signal(float, float, float)  # x_cm, y_cm, z_cm
    frame_ready = Signal(bytes)                      # API-compat only; never fires
    status_changed = Signal(str)  # "initializing"|"tracking"|"paused"|"error"

    def __init__(self, config_path: str, parent: object = None) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self._config_path = config_path
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._shm: Optional[SharedMemoryReader] = None
        self._last_ts: Optional[int] = None
        self._last_ts_time: float = 0.0
        self._start_time: float = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Launch the tracker subprocess and begin polling shared memory."""
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "tracker", "--config", self._config_path],
            cwd=str(_project_root()),
        )
        self._shm = SharedMemoryReader("G3D")
        self._last_ts = None
        self._start_time = time.monotonic()
        self._last_ts_time = self._start_time
        self._timer.start()
        self.status_changed.emit("initializing")

    def stop(self) -> None:
        """Terminate the subprocess and release resources."""
        self._timer.stop()
        if self._shm is not None:
            self._shm.close()
            self._shm = None
        proc = self._proc
        self._proc = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass

    # ── Polling ───────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        """Called every _POLL_MS ms; reads SHM and emits signals."""
        proc = self._proc
        if proc is None or self._shm is None:
            return

        if proc.poll() is not None:
            # Subprocess exited on its own (error or camera not found)
            self._timer.stop()
            self.status_changed.emit("error")
            return

        data = self._shm.read()
        now = time.monotonic()

        if data is None:
            # SHM not yet created — subprocess still starting
            if now - self._start_time > _INIT_TIMEOUT_S:
                self._timer.stop()
                self.status_changed.emit("error")
            return

        x, y, z, ts = data

        if ts != self._last_ts:
            self._last_ts = ts
            self._last_ts_time = now
            self.position_updated.emit(x, y, z)
            self.status_changed.emit("tracking")
        elif (now - self._last_ts_time) * 1000 > _STALE_MS:
            self.status_changed.emit("paused")
