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
        if self._hidden_for_overlay:
''',
    '''        self._tracker_recovery_pending = False
        self._overlay_recovery_pending = False
        self._overlay.stop_async()
        self._overlay_started = False
        if self._thread is not None:
            self._tracker_stop_pending = True
            self._thread.stop()
        if self._hidden_for_overlay:
''',
    "circuit retires tracker too",
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
