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

from launcher.status_emission import (
    StatusEmissionGate,
    StatusEmissionSnapshot,
)
from launcher.tracker_poll_admission import (
    TrackerPollAdmission,
    TrackerPollAdmissionPolicy,
    TrackerPollAdmissionSnapshot,
    wire_timestamp_ms,
)
from tracker.shared_memory import SharedMemoryReader, TrackingStateReader

_POLL_MS = 50           # 20 Hz UI refresh
_STALE_MS = 800         # no SHM update for this long -> emit "paused"
_STALE_RESTART_MS = 2500
_INIT_TIMEOUT_S = 45.0  # current child hasn't published after this long -> "error"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


class TrackerProcess(QObject):
    """Spawns 'python -m tracker' and exposes the same signals as TrackerThread.

    ``position_updated`` remains the historical three-value signal. New launcher
    consumers can use ``position_sampled`` to retain the producer's wrap-safe
    shared-memory publish timestamp instead of retiming motion at Qt receipt.

    frame_ready is declared for API compatibility but never emits — camera
    preview is not available in subprocess mode.
    """

    position_updated = Signal(float, float, float)  # x_cm, y_cm, z_cm
    # ``object`` preserves the full uint32 range; Qt ``int`` is signed 32-bit.
    position_sampled = Signal(float, float, float, object)  # + publish timestamp ms
    frame_ready = Signal(bytes)                      # API-compat only; never fires
    status_changed = Signal(str)  # initializing|tracking|hold|paused|restarting|error
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
        self._status_emission = StatusEmissionGate()
        self._poll_admission = TrackerPollAdmission(
            TrackerPollAdmissionPolicy(maximum_pose_age_ms=_STALE_MS)
        )
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._termination_finished.connect(self._on_termination_finished)
        self._retiring_proc: Optional[subprocess.Popen[bytes]] = None
        self._desired_running = False

    def _emit_status(self, status: object, *, force: bool = False) -> bool:
        """Publish one state transition and suppress exact consecutive repeats."""
        decision = self._status_emission.emit(
            status,
            self.status_changed.emit,
            force=force,
        )
        return decision.emitted

    def status_emission_snapshot(self) -> StatusEmissionSnapshot:
        """Return transition counters for launcher diagnostics and tests."""
        return self._status_emission.snapshot()

    def poll_admission_snapshot(self) -> TrackerPollAdmissionSnapshot:
        """Return current-session pose/state admission diagnostics."""
        return self._poll_admission.snapshot()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Launch the tracker subprocess and begin polling shared memory."""
        was_desired_running = self._desired_running
        self._desired_running = True
        self._restart_count = 0
        if self.isRunning():
            return True
        if not was_desired_running:
            # A user-visible new start must emit its initial state even when the
            # previous lifecycle ended on the same status text.
            self._status_emission.reset()
        if self._retiring_proc is not None:
            self._emit_status("initializing")
            return True

        if not self._launch_process():
            self._desired_running = False
            self._emit_status("error")
            return False

        self._timer.start()
        self._emit_status("initializing")
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
        # Capture the shared uptime boundary before Popen. A child can publish as
        # soon as it starts, and any retained mapping older than this boundary
        # belongs to a previous tracker session.
        launch_started_s = time.monotonic()
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
        self._start_time = launch_started_s
        self._last_ts_time = launch_started_s
        self._poll_admission.reset_session(
            wire_timestamp_ms(launch_started_s)
        )
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
            self._emit_status("initializing")
        else:
            self._desired_running = False
            self._emit_status("error")
            self.stopped.emit()

    def _restart_after_stale(self) -> None:
        self._timer.stop()
        if self._restart_count >= self._max_restarts:
            self._desired_running = False
            self._retire_current_process()
            self._emit_status("error")
            return

        self._restart_count += 1
        self._desired_running = True
        self._emit_status("restarting")
        self._retire_current_process()

    # ── Polling ───────────────────────────────────────────────────────────────

    def _handle_no_fresh_pose(self, now: float) -> None:
        """Apply startup or post-publication timeout semantics as appropriate."""
        if self._last_ts is None:
            # Retained old mappings are equivalent to no mapping during startup.
            # Give the current child the full model/camera initialization budget
            # instead of restarting it after the shorter live-stream timeout.
            if now - self._start_time > _INIT_TIMEOUT_S:
                self._desired_running = False
                self._retire_current_process()
                self._emit_status("error")
            return

        stale_ms = (now - self._last_ts_time) * 1000.0
        if stale_ms > self._stale_restart_ms:
            self._restart_after_stale()
        elif stale_ms > _STALE_MS:
            self._emit_status("paused")

    def _poll(self) -> None:
        """Called every _POLL_MS ms; reads and correlates pose/state SHM."""
        proc = self._proc
        if proc is None or self._shm is None:
            return

        if proc.poll() is not None:
            # Subprocess exited on its own (error or camera not found)
            self._timer.stop()
            self._desired_running = False
            self._proc = None
            self._close_readers()
            self._emit_status("error")
            self.stopped.emit()
            return

        data = self._shm.read()
        now = time.monotonic()

        if data is None:
            self._handle_no_fresh_pose(now)
            return

        x, y, z, ts = data
        if ts == self._last_ts:
            self._handle_no_fresh_pose(now)
            return

        pose_decision = self._poll_admission.evaluate_pose(
            ts,
            wire_timestamp_ms(now),
        )
        if not pose_decision.accepted:
            self._handle_no_fresh_pose(now)
            return

        try:
            state_data = (
                self._state_shm.read()
                if self._state_shm is not None
                else None
            )
        except Exception:
            # Shared-memory readers normally fail closed themselves. Keep the Qt
            # polling timer alive if an injected or platform reader still raises.
            state_data = None

        current_status = self._status_emission.snapshot().last_status
        state_decision = self._poll_admission.resolve_state(
            pose_decision.timestamp_ms,
            state_data,
            current_status=current_status,
            session_elapsed_ms=max(
                0.0,
                (now - self._start_time) * 1000.0,
            ),
        )
        if state_decision.status is not None:
            self._emit_status(state_decision.status)

        # A current-session pose that is waiting for state correlation is still
        # consumed so an initial neutral frame cannot be retried and later
        # relabeled as tracking by the legacy grace fallback.
        accepted_timestamp_ms = int(pose_decision.timestamp_ms)
        self._last_ts = accepted_timestamp_ms
        self._last_ts_time = now
        if not state_decision.publish_pose:
            return

        # Status must already be established or deliberately preserved before
        # either position signal. MainWindow uses it to admit live auto-tuning.
        self.position_updated.emit(x, y, z)
        self.position_sampled.emit(x, y, z, accepted_timestamp_ms)
