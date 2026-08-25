from pathlib import Path

path = Path("launcher/mainwindow.py")
text = path.read_text(encoding="utf-8")

# re.sub interprets backslash escapes in replacement strings. Normalize the
# generated UI labels back to valid Python source before compilation.
text = text.replace(
    'f"Overlay\nRetry {decision.delay_s:.0f}s"',
    'f"Overlay\\nRetry {decision.delay_s:.0f}s"',
)
text = text.replace('"Overlay\nRestarting"', '"Overlay\\nRestarting"')
text = text.replace('"Overlay\nPaused"', '"Overlay\\nPaused"')


def replace_once(old: str, new: str, label: str) -> None:
    global text
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    '''        self._tracker_recovery_pending = False
        self._overlay_recovery_pending = False
        recovery_config = config.get("recovery", {})
''',
    '''        self._tracker_recovery_pending = False
        self._overlay_recovery_pending = False
        self._recovery_paused = False
        recovery_config = config.get("recovery", {})
''',
    "paused state initialization",
)
replace_once(
    '''    def _toggle_tracking(self) -> None:
        if self._runtime_requested or (self._thread and self._thread.isRunning()):
            self._stop_tracking()
        else:
            self._start_tracking()
''',
    '''    def _toggle_tracking(self) -> None:
        if self._recovery_paused:
            self._manual_recover_runtime()
        elif self._runtime_requested or (self._thread and self._thread.isRunning()):
            self._stop_tracking()
        else:
            self._start_tracking()
''',
    "paused primary action",
)
replace_once(
    '''        self._runtime_requested = True
        self._tracker_recovery_pending = False
        self._tracker_failure_reason = None
''',
    '''        self._runtime_requested = True
        self._recovery_paused = False
        self._tracker_recovery_pending = False
        self._tracker_failure_reason = None
''',
    "start clears paused state",
)
replace_once(
    '''        if not tracker.start():
            self._overlay_started = False
            self._thread = None
            self._tracker_failure_reason = "tracker child failed to start"
            self._on_status("error")
            self._queue_tracker_recovery(self._tracker_failure_reason)
            return
''',
    '''        if not tracker.start():
            # A spawn failure is normally a missing/blocked executable or other
            # persistent setup problem. Do not burn the crash-loop budget on a
            # condition that cannot heal without user action.
            self._runtime_requested = False
            self._recovery_paused = False
            self._overlay_started = False
            self._thread = None
            self._tracker_failure_reason = None
            self._on_status("error")
            return
''',
    "nonretryable tracker launch failure",
)
replace_once(
    '''        except OverlayStartError as e:
            self._tracker_failure_reason = f"overlay launch failed: {e}"
            self._on_status("error")
''',
    '''        except OverlayStartError as e:
            # Missing runtime assets, an invalid executable, or policy/setup
            # errors cannot heal through repeated process restarts.
            self._runtime_requested = False
            self._recovery_paused = False
            self._tracker_failure_reason = None
            self._on_status("error")
''',
    "nonretryable overlay launch failure",
)
replace_once(
    '''    def _stop_tracking(self) -> None:
        self._runtime_requested = False
        self._recovery_generation += 1
''',
    '''    def _stop_tracking(self) -> None:
        self._runtime_requested = False
        self._recovery_paused = False
        self._recovery_generation += 1
''',
    "manual stop clears paused state",
)
replace_once(
    '''        if self._thread:
            self._tracker_stop_pending = True
            self._thread.stop()
        self._overlay.stop_async()
''',
    '''        if self._thread is not None and self._thread.isRunning():
            self._tracker_stop_pending = True
            self._thread.stop()
        else:
            self._thread = None
            self._tracker_stop_pending = False
        self._overlay.stop_async()
''',
    "manual stop handles already-finished tracker",
)
replace_once(
    '''    def _on_tracker_stopped(self, tracker: TrackerProcess | None = None) -> None:
        """Release lifecycle ownership only after the camera child has exited."""
        if tracker is None or self._thread is tracker:
            self._thread = None
        self._tracker_stop_pending = False
''',
    '''    def _on_tracker_stopped(self, tracker: TrackerProcess | None = None) -> None:
        """Release lifecycle ownership only after the camera child has exited."""
        if tracker is not None and self._thread is not tracker:
            # A retired process may deliver its queued stopped signal after a
            # replacement already owns the runtime. Never tear down the new one.
            return
        self._thread = None
        self._tracker_stop_pending = False
''',
    "ignore stale tracker stopped signal",
)
replace_once(
    '''        elif not self._runtime_requested:
            self._action_btn.setText("▶ START TRACKING")
''',
    '''        elif not self._runtime_requested:
            self._action_btn.setText(
                "↻ RETRY RUNTIME" if self._recovery_paused else "▶ START TRACKING"
            )
''',
    "retirement preserves retry action",
)
replace_once(
    '''        if (
            summary.has_frame
            and summary.capture_state == "running"
            and summary.depth_hz > 0
        ):
            self._recovery.mark_healthy("overlay")
''',
    '''        if (
            summary.has_frame
            and summary.capture_state == "running"
            and summary.depth_hz > 0
            and self._overlay.is_running()
            and self._overlay.is_transitioning() is not True
        ):
            # Native recovery succeeded before a delayed launcher restart fired.
            # Clearing the pending flag makes that stale timer a no-op.
            self._overlay_recovery_pending = False
            self._recovery.mark_healthy("overlay")
''',
    "healthy overlay cancels delayed restart",
)
replace_once(
    '''    def _maybe_recover_overlay(self, summary: OverlayRuntimeSummary) -> None:
        if not self._overlay_started or not self._tracker_is_running():
''',
    '''    def _maybe_recover_overlay(self, summary: OverlayRuntimeSummary) -> None:
        if (
            not self._runtime_requested
            and self._overlay_started
            and self._tracker_is_running()
        ):
            # Repair intent after legacy state restoration/tests or a launcher
            # transition that already owns both active children.
            self._runtime_requested = True
        if not self._overlay_started or not self._tracker_is_running():
''',
    "infer active runtime intent",
)
replace_once(
    '''        if decision.delay_s <= 0.0:
            self._execute_overlay_recovery(self._recovery_generation, reason)
            return
        self._overlay_recovery_pending = True
''',
    '''        self._overlay_recovery_pending = True
        if decision.delay_s <= 0.0:
            self._execute_overlay_recovery(self._recovery_generation, reason)
            return
''',
    "track immediate and delayed overlay attempts",
)
replace_once(
    '''    def _execute_overlay_recovery(self, generation: int, reason: str) -> None:
        if generation != self._recovery_generation or not self._runtime_requested:
            return
        self._overlay_recovery_pending = False
''',
    '''    def _execute_overlay_recovery(self, generation: int, reason: str) -> None:
        if (
            generation != self._recovery_generation
            or not self._runtime_requested
            or not self._overlay_recovery_pending
        ):
            return
        self._overlay_recovery_pending = False
''',
    "stale overlay timer guard",
)
replace_once(
    '''    def _pause_recovery(
        self,
        component: str,
        reason: str,
        retry_after_s: float,
    ) -> None:
        self._runtime_requested = False
        self._recovery_generation += 1
''',
    '''    def _pause_recovery(
        self,
        component: str,
        reason: str,
        retry_after_s: float,
    ) -> None:
        self._runtime_requested = False
        self._recovery_paused = True
        self._recovery_generation += 1
''',
    "circuit marks recovery paused",
)
replace_once(
    '''        self._tracker_recovery_pending = False
        self._overlay_recovery_pending = False
        self._overlay.stop_async()
        self._overlay_started = False
        if self._thread is not None:
            self._tracker_stop_pending = True
            self._thread.stop()
        if self._hidden_for_overlay:
''',
    '''        self._tracker_recovery_pending = False
        self._overlay_recovery_pending = False
        self._overlay.stop_async()
        self._overlay_started = False
        if self._thread is not None and self._thread.isRunning():
            self._tracker_stop_pending = True
            self._thread.stop()
        else:
            self._thread = None
            self._tracker_stop_pending = False
        if self._hidden_for_overlay:
''',
    "circuit handles already-finished tracker",
)
replace_once(
    '''        self._status_label.setToolTip(
            f"{component} failed repeatedly: {reason}. "
            f"Automatic retry paused for {retry_after_s:.0f}s."
        )
''',
    '''        self._status_label.setToolTip(
            f"{component} failed repeatedly: {reason}. "
            f"Automatic retry pauses for {retry_after_s:.0f}s; "
            "Retry Runtime overrides the cooldown."
        )
''',
    "cooldown tooltip",
)
replace_once(
    '''        _log.error(
            "%s recovery circuit opened after repeated failure: %s",
            component,
            reason,
        )

    def _manual_recover_runtime(self) -> None:
''',
    '''        generation = self._recovery_generation
        QTimer.singleShot(
            max(1, int(round(retry_after_s * 1000.0))),
            lambda token=generation, name=component: self._resume_recovery_after_cooldown(
                token, name
            ),
        )
        _log.error(
            "%s recovery circuit opened after repeated failure: %s",
            component,
            reason,
        )

    def _resume_recovery_after_cooldown(
        self,
        generation: int,
        component: str,
    ) -> None:
        if generation != self._recovery_generation or not self._recovery_paused:
            return
        snapshot = self._recovery.snapshot(component)
        if snapshot.circuit_open:
            QTimer.singleShot(
                max(1, int(round(snapshot.retry_after_s * 1000.0))),
                lambda token=generation, name=component: self._resume_recovery_after_cooldown(
                    token, name
                ),
            )
            return
        self._recovery_paused = False
        self._runtime_requested = True
        self._tracker_failure_reason = None
        self._on_status("restarting")
        self._action_btn.setText("■ CANCEL RECOVERY")
        self._action_btn.setEnabled(True)
        self._execute_tracker_recovery(generation)

    def _manual_recover_runtime(self) -> None:
''',
    "automatic cooldown resume",
)
replace_once(
    '''    def _manual_recover_runtime(self) -> None:
        self._recovery.reset()
        self._recovery_generation += 1
        self._runtime_requested = True
''',
    '''    def _manual_recover_runtime(self) -> None:
        self._recovery.reset()
        self._recovery_generation += 1
        self._runtime_requested = True
        self._recovery_paused = False
''',
    "manual recovery clears paused state",
)
replace_once(
    '''        if self._thread is not None:
            self._tracker_stop_pending = True
            self._thread.stop()
            self._action_btn.setText("Stopping…")
            self._action_btn.setEnabled(False)
            return
        generation = self._recovery_generation
''',
    '''        if self._thread is not None and self._thread.isRunning():
            self._tracker_stop_pending = True
            self._thread.stop()
            self._action_btn.setText("Stopping…")
            self._action_btn.setEnabled(False)
            return
        self._thread = None
        self._tracker_stop_pending = False
        generation = self._recovery_generation
''',
    "manual recovery handles already-finished tracker",
)

path.write_text(text, encoding="utf-8", newline="\n")

supervisor_path = Path("launcher/runtime_supervisor.py")
supervisor = supervisor_path.read_text(encoding="utf-8")
old_prune = '''        while state.failures and state.failures[0] < cutoff:
            state.failures.popleft()
        if state.open_until_s and now_s >= state.open_until_s:
'''
new_prune = '''        while state.failures and state.failures[0] < cutoff:
            state.failures.popleft()
        if not state.failures and state.open_until_s <= now_s:
            # A sparse failure after the rolling window begins a new episode
            # instead of inheriting an arbitrarily large exponential delay.
            state.consecutive_failures = 0
        else:
            state.consecutive_failures = min(
                state.consecutive_failures,
                len(state.failures),
            )
        if state.open_until_s and now_s >= state.open_until_s:
'''
if new_prune not in supervisor:
    if supervisor.count(old_prune) != 1:
        raise RuntimeError("runtime supervisor prune block changed unexpectedly")
    supervisor = supervisor.replace(old_prune, new_prune, 1)
supervisor_path.write_text(supervisor, encoding="utf-8", newline="\n")
