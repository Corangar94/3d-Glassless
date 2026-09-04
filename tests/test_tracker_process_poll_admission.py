from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _method(source: str, name: str, next_name: str) -> str:
    return source.split(f"    def {name}(", 1)[1].split(
        f"    def {next_name}(",
        1,
    )[0]


def test_launch_boundary_is_captured_before_child_creation():
    source = _source("launcher/tracker_process.py")
    launch = _method(source, "_launch_process", "isRunning")

    launch_time = launch.index("launch_started_s = time.monotonic()")
    popen = launch.index("subprocess.Popen(")
    readers = launch.index('SharedMemoryReader("G3D")')
    reset_flag = launch.index("self._session_pose_published = False")
    reset = launch.index("self._poll_admission.reset_session(")

    assert launch_time < popen < readers < reset_flag < reset
    assert "wire_timestamp_ms(launch_started_s)" in launch
    assert "self._start_time = launch_started_s" in launch


def test_startup_timeout_is_distinct_from_live_stale_restart():
    source = _source("launcher/tracker_process.py")
    handler = _method(source, "_handle_no_fresh_pose", "_poll")

    startup = handler.index("if not self._session_pose_published:")
    init_timeout = handler.index("_INIT_TIMEOUT_S")
    startup_return = handler.index("return")
    live_stale = handler.index("stale_ms =")
    restart = handler.index("self._restart_after_stale()")
    paused = handler.index('self._emit_status("paused")')

    assert startup < init_timeout < startup_return < live_stale
    assert live_stale < restart < paused
    assert "unresolved initial" in handler


def test_pose_admission_precedes_state_read_and_every_signal():
    source = _source("launcher/tracker_process.py")
    poll = source.split("    def _poll(self)", 1)[1]

    pose_admission = poll.index("self._poll_admission.evaluate_pose(")
    state_read = poll.index("self._state_shm.read()")
    state_resolution = poll.index("self._poll_admission.resolve_state(")
    status = poll.index("self._emit_status(state_decision.status)")
    timestamp = poll.index("self._last_ts = accepted_timestamp_ms")
    publish_gate = poll.index("if not state_decision.publish_pose:")
    usable = poll.index("self._session_pose_published = True")
    legacy_pose = poll.index("self.position_updated.emit(")
    sampled_pose = poll.index("self.position_sampled.emit(")

    assert pose_admission < state_read < state_resolution < status
    assert status < timestamp < publish_gate < usable
    assert usable < legacy_pose < sampled_pose


def test_rejected_or_absent_pose_uses_one_timeout_boundary():
    source = _source("launcher/tracker_process.py")
    poll = source.split("    def _poll(self)", 1)[1]

    assert poll.count("self._handle_no_fresh_pose(now)") == 3
    rejected = poll.split("if not pose_decision.accepted:", 1)[1].split(
        "try:",
        1,
    )[0]
    assert "self._handle_no_fresh_pose(now)" in rejected
    assert "return" in rejected


def test_incoherent_startup_pose_is_consumed_but_not_marked_usable():
    source = _source("launcher/tracker_process.py")
    poll = source.split("    def _poll(self)", 1)[1]

    timestamp = poll.index("self._last_ts = accepted_timestamp_ms")
    timestamp_clock = poll.index("self._last_ts_time = now")
    publish_gate = poll.index("if not state_decision.publish_pose:")
    usable = poll.index("self._session_pose_published = True")

    assert timestamp < timestamp_clock < publish_gate < usable
    assert "initial neutral frame" in poll


def test_poll_passes_complete_state_snapshot_and_current_status():
    source = _source("launcher/tracker_process.py")
    poll = source.split("    def _poll(self)", 1)[1]
    resolution = poll.split(
        "state_decision = self._poll_admission.resolve_state(",
        1,
    )[1].split(")\n", 1)[0]

    assert "state_data" in resolution
    assert "current_status=current_status" in resolution
    assert "session_elapsed_ms=max(" in resolution
    assert 'state_data[0] if state_data is not None else "tracking"' not in poll


def test_tracker_writes_state_before_pose_at_the_producer_boundary():
    source = _source("tracker/main.py")
    publish = _method(source, "_publish", "_publish_camera_unavailable")

    state = publish.index("write_state(status)")
    pose = publish.index("self._writer.write_pose(")
    callback = publish.index("self._on_position(")

    assert state < pose < callback


def test_frozen_package_includes_poll_admission_module():
    spec = _source("Glassless3D.spec")

    assert '"launcher.tracker_poll_admission"' in spec


def test_documentation_records_session_correlation_and_timeout_contracts():
    docs = _source("docs/LAUNCHER_POSE_STATE_ADMISSION.md")

    assert "immediately **before** `Popen`" in docs
    assert "strictly later than that launch boundary" in docs
    assert "trails the pose by no more than 100 ms" in docs
    assert "consumed but not emitted" in docs
    assert "first usable pose is exposed" in docs
    assert "full 45-second" in docs
    assert "approximately 49.7-day" in docs
