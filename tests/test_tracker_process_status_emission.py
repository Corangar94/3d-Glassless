from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _method(source: str, name: str, next_name: str) -> str:
    return source.split(f"    def {name}(", 1)[1].split(
        f"    def {next_name}(",
        1,
    )[0]


def test_tracker_process_routes_every_status_through_transition_gate():
    source = _source("launcher/tracker_process.py")

    assert "self._status_emission = StatusEmissionGate()" in source
    assert "self.status_changed.emit," in source
    assert "self.status_changed.emit(" not in source
    for status in (
        "initializing",
        "tracking",
        "paused",
        "restarting",
        "error",
    ):
        assert f'"{status}"' in source


def test_new_external_start_resets_after_running_guard_before_initial_status():
    source = _source("launcher/tracker_process.py")
    start = _method(source, "start", "_tracker_command")

    running_guard = start.index("if self.isRunning():")
    reset = start.index("self._status_emission.reset()")
    retiring = start.index("if self._retiring_proc is not None:")
    initial = start.index('self._emit_status("initializing")')

    assert running_guard < reset < retiring < initial
    assert "if not was_desired_running:" in start


def test_internal_relaunch_keeps_transition_history():
    source = _source("launcher/tracker_process.py")
    relaunch = _method(
        source,
        "_launch_after_retirement",
        "_restart_after_stale",
    )
    restart = _method(source, "_restart_after_stale", "_poll")

    assert "self._status_emission.reset()" not in relaunch
    assert "self._status_emission.reset()" not in restart
    assert relaunch.index('self._emit_status("initializing")') < (
        relaunch.index('self._emit_status("error")')
    )
    assert 'self._emit_status("restarting")' in restart
    assert 'self._emit_status("error")' in restart


def test_fresh_pose_status_leads_timestamp_commit_and_both_pose_signals():
    source = _source("launcher/tracker_process.py")
    poll = source.split("    def _poll(self)", 1)[1]
    fresh = poll.split("        if ts != self._last_ts:", 1)[1].split(
        "        else:",
        1,
    )[0]

    status = fresh.index("self._emit_status(")
    timestamp = fresh.index("self._last_ts = ts")
    timestamp_clock = fresh.index("self._last_ts_time = now")
    legacy_pose = fresh.index("self.position_updated.emit(")
    sampled_pose = fresh.index("self.position_sampled.emit(")

    assert status < timestamp < timestamp_clock < legacy_pose < sampled_pose


def test_repeated_stale_poll_uses_deduplicated_paused_path():
    source = _source("launcher/tracker_process.py")
    poll = source.split("    def _poll(self)", 1)[1]
    stale = poll.split("        else:", 1)[1]

    restart = stale.index("self._restart_after_stale()")
    paused = stale.index('self._emit_status("paused")')

    assert restart < paused
    assert "self.status_changed.emit" not in stale


def test_status_diagnostics_expose_gate_snapshot():
    source = _source("launcher/tracker_process.py")
    diagnostics = _method(
        source,
        "status_emission_snapshot",
        "start",
    )

    assert "-> StatusEmissionSnapshot" in source
    assert "return self._status_emission.snapshot()" in diagnostics


def test_frozen_package_includes_status_gate():
    spec = _source("Glassless3D.spec")

    assert '"launcher.status_emission"' in spec


def test_documentation_records_transition_and_ordering_contracts():
    docs = _source("docs/LAUNCHER_STATUS_TRANSITIONS.md")

    assert "one status signal and twenty pose signals" in docs
    assert "status transition publication or duplicate suppression" in docs
    assert "commit fresh pose timestamp" in docs
    assert "first status of a user-visible `start()` is never suppressed" in docs
    assert "failed signal emissions" in docs
