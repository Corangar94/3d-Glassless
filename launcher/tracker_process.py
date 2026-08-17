"""Run the tracker as a child process; surface head-pose data via SHM polling.

Replaces TrackerThread for environments where importing mediapipe inside a
QThread causes a DirectML/COM deadlock.  The subprocess has its own Python
interpreter, so it's completely isolated from Qt's GPU/COM state.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from tracker.shared_memory import SharedMemoryReader, TrackingStateReader

_POLL_MS = 50           # 20 Hz UI refresh
_STALE_MS = 800         # no SHM update for this long -> emit "paused"
_STALE_RESTART_MS = 2500
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
    stopped = Signal()
    _termination_finished = Signal(object, bool)

    def __init__(
        self,
        config_path: str,
        parent: object = None,
        stale_restart_ms: int = _STALE_RESTART_MS,
        max_restarts: int = 2,
    ) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self._config_path = config_path
        self._stale_restart_ms = stale_restart_ms
        self._max_restarts = max_restarts
        self._restart_count = 0
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._shm: Optional[SharedMemoryReader] = None
        self._state_shm: Optional[TrackingStateReader] = None
        self._last_ts: Optional[int] = None
        self._last_ts_time: float = 0.0
        self._start_time: float = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._termination_finished.connect(self._on_termination_finished)
        self._retiring_proc: Optional[subprocess.Popen[bytes]] = None
        self._desired_running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Launch the tracker subprocess and begin polling shared memory."""
        self._desired_running = True
        self._restart_count = 0
        if self.isRunning():
            return True
        if self._retiring_proc is not None:
            self.status_changed.emit("initializing")
            return True

        if not self._launch_process():
            self._desired_running = False
            self.status_changed.emit("error")
            return False

        self._timer.start()
        self.status_changed.emit("initializing")
        return True

    def _tracker_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [
                sys.executable,
                "--tracker-child",
                "--config",
                self._config_path,
            ]
        return [
            sys.executable,
            "-m",
            "tracker",
            "--config",
            self._config_path,
        ]

    def _launch_process(self) -> bool:
        try:
            proc = subprocess.Popen(
                self._tracker_command(),
                cwd=str(_project_root()),
            )
        except OSError:
            return False

        self._proc = proc
        self._shm = SharedMemoryReader("G3D")
        self._state_shm = TrackingStateReader("G3D_State")
        self._last_ts = None
        self._start_time = time.monotonic()
        self._last_ts_time = self._start_time
        return True

    def isRunning(self) -> bool:
        """API-compat with QThread.isRunning()."""
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        """Terminate the subprocess and release resources."""
        self._desired_running = False
        self._timer.stop()
        self._close_readers()
        proc = self._proc
        self._proc = None
        if proc is not None and proc.poll() is None:
            self._begin_termination(proc)
        elif self._retiring_proc is None:
            self.stopped.emit()

    def _close_readers(self) -> None:
        if self._shm is not None:
            self._shm.close()
            self._shm = None
        if self._state_shm is not None:
            self._state_shm.close()
            self._state_shm = None

    def _retire_current_process(self) -> None:
        self._timer.stop()
        self._close_readers()
        proc = self._proc
        self._proc = None
        if proc is not None and proc.poll() is None:
            self._begin_termination(proc)
        elif self._desired_running:
            self._launch_after_retirement()
        else:
            self.stopped.emit()

    def _begin_termination(self, proc: subprocess.Popen[bytes]) -> None:
        """Retire one child off-thread; completion is queued back to Qt."""
        if self._retiring_proc is not None:
            return
        self._retiring_proc = proc
        try:
            proc.terminate()
        except OSError:
            self._termination_finished.emit(proc, False)
            return

        def reap() -> None:
            self._wait_then_kill(proc)
            self._termination_finished.emit(proc, False)

        threading.Thread(
            target=reap,
            name="g3d-tracker-lifecycle",
            daemon=False,
        ).start()

    @staticmethod
    def _wait_then_kill(proc: subprocess.Popen[bytes]) -> None:
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        except OSError:
            pass

    def _on_termination_finished(
        self, proc: subprocess.Popen[bytes], _obsolete_restart: bool
    ) -> None:
        if proc is not self._retiring_proc:
            return
        self._retiring_proc = None
        if self._desired_running:
            self._launch_after_retirement()
        else:
            self.stopped.emit()

    def _launch_after_retirement(self) -> None:
        if self._launch_process():
            self._timer.start()
            self.status_changed.emit("initializing")
        else:
            self._desired_running = False
            self.status_changed.emit("error")
            self.stopped.emit()

    def _restart_after_stale(self) -> None:
        self._timer.stop()
        if self._restart_count >= self._max_restarts:
            self._desired_running = False
            self._retire_current_process()
            self.status_changed.emit("error")
            return

        self._restart_count += 1
        self._desired_running = True
        self.status_changed.emit("restarting")
        self._retire_current_process()

    # ── Polling ───────────────────────────────────────────────────────────────

    def _poll(self) -> None:
        """Called every _POLL_MS ms; reads SHM and emits signals."""
        proc = self._proc
        if proc is None or self._shm is None:
            return

        if proc.poll() is not None:
            # Subprocess exited on its own (error or camera not found)
            self._timer.stop()
            self._desired_running = False
            self._proc = None
            self._close_readers()
            self.status_changed.emit("error")
            self.stopped.emit()
            return

        data = self._shm.read()
        now = time.monotonic()

        if data is None:
            # SHM not yet created — subprocess still starting
            if now - self._start_time > _INIT_TIMEOUT_S:
                self._desired_running = False
                self._retire_current_process()
                self.status_changed.emit("error")
            return

        x, y, z, ts = data

        if ts != self._last_ts:
            self._last_ts = ts
            self._last_ts_time = now
            state_data = self._state_shm.read() if self._state_shm is not None else None
            self.status_changed.emit(state_data[0] if state_data is not None else "tracking")
            # Status must lead the corresponding pose.  MainWindow uses the
            # current status to decide whether a pose may feed auto-tuning;
            # emitting a paused fallback first would contaminate calibration.
            self.position_updated.emit(x, y, z)
        else:
            stale_ms = (now - self._last_ts_time) * 1000
            if stale_ms > self._stale_restart_ms:
                self._restart_after_stale()
            elif stale_ms > _STALE_MS:
                self.status_changed.emit("paused")
