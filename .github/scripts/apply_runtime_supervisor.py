from __future__ import annotations

from pathlib import Path
import re


PATH = Path("launcher/mainwindow.py")
text = PATH.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    text = text.replace(old, new, 1)


def regex_once(pattern: str, replacement: str, label: str) -> None:
    global text
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match, found {count}")
    text = updated


replace_once(
    "from launcher.tracker_process import TrackerProcess\n",
    "from launcher.tracker_process import TrackerProcess\n"
    "from launcher.runtime_supervisor import RecoveryPolicy, RuntimeRecoveryController\n",
    "runtime supervisor import",
)

replace_once(
    '''        self._capture_loss_count = 0
        self._debug_monitor_proc: Optional[subprocess.Popen[bytes]] = None
''',
    '''        self._capture_loss_count = 0
        self._runtime_requested = False
        self._recovery_generation = 0
        self._tracker_failure_reason: str | None = None
        self._tracker_recovery_pending = False
        self._overlay_recovery_pending = False
        recovery_config = config.get("recovery", {})
        if not isinstance(recovery_config, dict):
            recovery_config = {}
        try:
            recovery_policy = RecoveryPolicy(
                immediate_retries=int(recovery_config.get("immediate_retries", 1)),
                base_delay_s=float(recovery_config.get("base_delay_s", 1.0)),
                max_delay_s=float(recovery_config.get("max_delay_s", 20.0)),
                max_failures=int(recovery_config.get("max_failures", 5)),
                failure_window_s=float(recovery_config.get("failure_window_s", 90.0)),
                cooldown_s=float(recovery_config.get("cooldown_s", 60.0)),
                stable_reset_s=float(recovery_config.get("stable_reset_s", 30.0)),
            )
        except (TypeError, ValueError):
            recovery_policy = RecoveryPolicy()
        self._recovery = RuntimeRecoveryController(recovery_policy)
        self._debug_monitor_proc: Optional[subprocess.Popen[bytes]] = None
''',
    "recovery state initialization",
)

replace_once(
    '''        side.addWidget(self._operator_button("Run diagnostics", self._run_diagnostics))
        side.addWidget(self._operator_button("Collect support bundle", self._collect_support_bundle))
''',
    '''        side.addWidget(self._operator_button("Run diagnostics", self._run_diagnostics))
        side.addWidget(self._operator_button("Recover runtime", self._manual_recover_runtime))
        side.addWidget(self._operator_button("Collect support bundle", self._collect_support_bundle))
''',
    "recovery operator button",
)

replace_once(
    '''    def _toggle_tracking(self) -> None:
        if self._thread and self._thread.isRunning():
            self._stop_tracking()
        else:
            self._start_tracking()

    def _start_tracking(self) -> None:
        if self._tracker_stop_pending:
            return
''',
    '''    def _toggle_tracking(self) -> None:
        if self._runtime_requested or (self._thread and self._thread.isRunning()):
            self._stop_tracking()
        else:
            self._start_tracking()

    def _start_tracking(self, *, recovery: bool = False) -> None:
        if self._tracker_stop_pending:
            return
        if not recovery:
            self._recovery.reset()
            self._recovery_generation += 1
        self._runtime_requested = True
        self._tracker_recovery_pending = False
        self._tracker_failure_reason = None
''',
    "tracking intent and recovery-aware start",
)

replace_once(
    '''        if not self._policy_decision.allows(Backend.DESKTOP_OVERLAY):
            self._on_status("error")
            self._status_label.setToolTip("The active game profile does not permit desktop overlay runtime.")
            return
''',
    '''        if not self._policy_decision.allows(Backend.DESKTOP_OVERLAY):
            self._runtime_requested = False
            self._on_status("error")
            self._status_label.setToolTip("The active game profile does not permit desktop overlay runtime.")
            return
''',
    "disallowed profile clears intent",
)

replace_once(
    '''        if not tracker.start():
            self._overlay_started = False
            self._thread = None
            self._on_status("error")
            self._action_btn.setText("▶ START TRACKING")
            self._action_btn.setStyleSheet(
                f"background:{_ACCENT};color:#111;font-weight:900;"
                "font-size:13px;padding:12px;border:none;border-radius:8px;"
            )
            return
''',
    '''        if not tracker.start():
            self._overlay_started = False
            self._thread = None
            self._tracker_failure_reason = "tracker child failed to start"
            self._on_status("error")
            self._queue_tracker_recovery(self._tracker_failure_reason)
            return
''',
    "start failure schedules recovery",
)

replace_once(
    '''        except OverlayStartError as e:
            self._on_status("error")
''',
    '''        except OverlayStartError as e:
            self._tracker_failure_reason = f"overlay launch failed: {e}"
            self._on_status("error")
''',
    "overlay launch failure reason",
)

replace_once(
    '''    def _stop_tracking(self) -> None:
        if self._thread:
''',
    '''    def _stop_tracking(self) -> None:
        self._runtime_requested = False
        self._recovery_generation += 1
        self._tracker_failure_reason = None
        self._tracker_recovery_pending = False
        self._overlay_recovery_pending = False
        self._recovery.reset()
        if self._thread:
''',
    "manual stop cancels recovery",
)

replace_once(
    '''        self._action_btn.setEnabled(True)
        self._action_btn.setText("▶ START TRACKING")

    # ── Signal slots''',
    '''        self._action_btn.setEnabled(True)
        if self._runtime_requested and self._tracker_failure_reason:
            reason = self._tracker_failure_reason
            self._tracker_failure_reason = None
            if reason == "manual recovery":
                generation = self._recovery_generation
                QTimer.singleShot(
                    0,
                    lambda token=generation: self._execute_tracker_recovery(token),
                )
            else:
                self._queue_tracker_recovery(reason)
        elif not self._runtime_requested:
            self._action_btn.setText("▶ START TRACKING")

    # ── Signal slots''',
    "post-retirement recovery",
)

replace_once(
    '''        if status == "error":
            if self._thread:
''',
    '''        if status == "tracking":
            self._recovery.mark_healthy("tracker")
            self._tracker_failure_reason = None
        if status == "error":
            if self._runtime_requested and self._tracker_failure_reason is None:
                self._tracker_failure_reason = "tracker process error"
            if self._thread:
''',
    "tracker health and failure capture",
)

replace_once(
    '''            self._action_btn.setText(
                "Stopping…" if self._tracker_stop_pending else "▶ START TRACKING"
            )
''',
    '''            self._action_btn.setText(
                "Stopping…"
                if self._tracker_stop_pending
                else ("■ CANCEL RECOVERY" if self._runtime_requested else "▶ START TRACKING")
            )
''',
    "error action state",
)

replace_once(
    '''        self._maybe_recover_overlay(summary)

        target_path = self._active_profile.executable_path.strip()
''',
    '''        self._maybe_recover_overlay(summary)
        if (
            summary.has_frame
            and summary.capture_state == "running"
            and summary.depth_hz > 0
        ):
            self._recovery.mark_healthy("overlay")

        target_path = self._active_profile.executable_path.strip()
''',
    "overlay stable reset",
)

regex_once(
    r'''    def _restart_overlay_from_health\(self, reason: str\) -> None:\n.*?\n        _log.warning\("overlay restart queued after runtime health failure: %s", reason\)\n''',
    '''    def _restart_overlay_from_health(self, reason: str) -> None:
        if self._overlay_recovery_pending or not self._runtime_requested:
            return
        self._capture_loss_count = 0
        decision = self._recovery.record_failure("overlay", reason)
        if not decision.allowed:
            self._pause_recovery("overlay", decision.reason, decision.delay_s)
            return
        if decision.delay_s <= 0.0:
            self._execute_overlay_recovery(self._recovery_generation, reason)
            return
        self._overlay_recovery_pending = True
        generation = self._recovery_generation
        if hasattr(self, "_overlay_tile"):
            self._overlay_tile.setText(
                f"Overlay\\nRetry {decision.delay_s:.0f}s"
            )
        QTimer.singleShot(
            max(1, int(round(decision.delay_s * 1000.0))),
            lambda token=generation, why=reason: self._execute_overlay_recovery(
                token, why
            ),
        )
        _log.warning(
            "overlay recovery delayed %.1fs after %s",
            decision.delay_s,
            reason,
        )

    def _execute_overlay_recovery(self, generation: int, reason: str) -> None:
        if generation != self._recovery_generation or not self._runtime_requested:
            return
        self._overlay_recovery_pending = False
        if not self._tracker_is_running():
            return
        self._overlay.restart_async(
            self._active_profile.executable_path,
            **self._overlay_target_kwargs(),
        )
        self._overlay_started = True
        if hasattr(self, "_overlay_tile"):
            self._overlay_tile.setText("Overlay\\nRestarting")
        _log.warning("overlay restart queued after runtime health failure: %s", reason)

    def _queue_tracker_recovery(self, reason: str) -> None:
        if self._tracker_recovery_pending or not self._runtime_requested:
            return
        decision = self._recovery.record_failure("tracker", reason)
        if not decision.allowed:
            self._pause_recovery("tracker", decision.reason, decision.delay_s)
            return
        self._tracker_recovery_pending = True
        generation = self._recovery_generation
        self._on_status("restarting")
        self._action_btn.setText("■ CANCEL RECOVERY")
        self._action_btn.setEnabled(True)
        if decision.delay_s <= 0.0:
            QTimer.singleShot(
                0,
                lambda token=generation: self._execute_tracker_recovery(token),
            )
        else:
            self._status_label.setToolTip(
                f"Tracker recovery after {reason}; retry in {decision.delay_s:.1f}s"
            )
            QTimer.singleShot(
                max(1, int(round(decision.delay_s * 1000.0))),
                lambda token=generation: self._execute_tracker_recovery(token),
            )
        _log.warning(
            "tracker recovery scheduled in %.1fs after %s",
            decision.delay_s,
            reason,
        )

    def _execute_tracker_recovery(self, generation: int) -> None:
        if generation != self._recovery_generation or not self._runtime_requested:
            return
        if self._tracker_stop_pending:
            QTimer.singleShot(
                100,
                lambda token=generation: self._execute_tracker_recovery(token),
            )
            return
        self._tracker_recovery_pending = False
        if self._thread is not None and self._thread.isRunning():
            return
        self._start_tracking(recovery=True)

    def _pause_recovery(
        self,
        component: str,
        reason: str,
        retry_after_s: float,
    ) -> None:
        self._runtime_requested = False
        self._recovery_generation += 1
        self._tracker_recovery_pending = False
        self._overlay_recovery_pending = False
        self._overlay.stop_async()
        self._overlay_started = False
        if self._hidden_for_overlay:
            self._hidden_for_overlay = False
            self.showNormal()
        self._tracking_status = "error"
        self._status_label.setText("✕ RECOVERY PAUSED")
        self._status_label.setStyleSheet(
            "color:#e84040;font-size:10px;font-weight:bold;"
        )
        self._status_label.setToolTip(
            f"{component} failed repeatedly: {reason}. "
            f"Automatic retry paused for {retry_after_s:.0f}s."
        )
        self._action_btn.setText("↻ RETRY RUNTIME")
        self._action_btn.setEnabled(True)
        if hasattr(self, "_overlay_tile"):
            self._overlay_tile.setText("Overlay\\nPaused")
        _log.error(
            "%s recovery circuit opened after repeated failure: %s",
            component,
            reason,
        )

    def _manual_recover_runtime(self) -> None:
        self._recovery.reset()
        self._recovery_generation += 1
        self._runtime_requested = True
        self._tracker_recovery_pending = False
        self._overlay_recovery_pending = False
        self._capture_loss_count = 0
        self._tracker_failure_reason = "manual recovery"
        self._overlay.stop_async()
        self._overlay_started = False
        if self._thread is not None:
            self._tracker_stop_pending = True
            self._thread.stop()
            self._action_btn.setText("Stopping…")
            self._action_btn.setEnabled(False)
            return
        generation = self._recovery_generation
        QTimer.singleShot(
            0,
            lambda token=generation: self._execute_tracker_recovery(token),
        )

''',
    "bounded overlay and tracker recovery methods",
)

PATH.write_text(text, encoding="utf-8", newline="\n")
