from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_tracker_process_emits_status_before_legacy_and_timestamped_pose():
    source = _source("launcher/tracker_process.py")
    poll = source.split("    def _poll(self) -> None:", 1)[1]
    fresh = poll.split("        if ts != self._last_ts:", 1)[1].split(
        "        else:\n            stale_ms",
        1,
    )[0]

    status = fresh.index("self.status_changed.emit(")
    legacy = fresh.index("self.position_updated.emit(")
    sampled = fresh.index("self.position_sampled.emit(")

    assert status < legacy < sampled
    assert "position_updated = Signal(float, float, float)" in source
    assert "position_sampled = Signal(float, float, float, object)" in source


def test_runtime_replaces_only_its_legacy_pose_connection():
    source = _source("launcher/runtime_mainwindow.py")
    binding = source.split(
        "    def _bind_timestamped_pose_signal(",
        1,
    )[1].split("    def _start_tracking(", 1)[0]

    disconnect = binding.index("disconnect_legacy(self._on_position)")
    connect = binding.index(
        "connect_sampled(self._on_timestamped_position)"
    )

    assert disconnect < connect
    assert "if disconnected is False:" in binding
    assert "connect_legacy(self._on_position)" in binding
    assert binding.count("try:") >= 3
    assert "return False" in binding


def test_producer_time_enters_tuner_but_local_time_keeps_write_throttle():
    runtime = _source("launcher/runtime_mainwindow.py")
    base = _source("launcher/mainwindow.py")
    timestamped = runtime.split(
        "    def _on_timestamped_position(",
        1,
    )[1].split("    def _on_auto_tune_toggle(", 1)[0]
    base_position = base.split(
        "    def _on_position(self, x: float, y: float, z: float) -> None:",
        1,
    )[1].split("    def _on_frame(", 1)[0]

    accept = timestamped.index("_auto_tune_sample_timeline.accept(")
    arm = timestamped.index("arm(sample_time_s)")
    base_slot = timestamped.index(
        "super()._on_position(x_cm, y_cm, z_cm)"
    )
    assert accept < arm < base_slot

    assert "now_s = time.monotonic()" in base_position
    assert "self._auto_tuner.update(x, y, z, now_s)" in base_position
    assert "now_s - self._last_auto_tune_write_s < 0.25" in base_position
    assert "fallback_timestamp_s" in runtime
    assert "else float(pending)" in runtime


def test_invalid_producer_order_is_dropped_not_retimed():
    runtime = _source("launcher/runtime_mainwindow.py")
    timestamped = runtime.split(
        "    def _on_timestamped_position(",
        1,
    )[1].split("    def _on_auto_tune_toggle(", 1)[0]

    rejection = timestamped.index("if sample_time_s is None:")
    return_index = timestamped.index("return", rejection)
    base_slot = timestamped.index("super()._on_position(")

    assert rejection < return_index < base_slot
    assert "time.monotonic" not in timestamped


def test_tracking_boundaries_reset_both_tuner_and_producer_clock():
    runtime = _source("launcher/runtime_mainwindow.py")
    boundary = runtime.split(
        "    def _reset_auto_tuner_on_tracking_boundary(",
        1,
    )[1].split("    def _on_status(", 1)[0]

    timeline_reset = boundary.index("reset_timeline()")
    tuner_reset = boundary.index("reset()", timeline_reset + 1)
    write_reset = boundary.index("self._last_auto_tune_write_s = 0.0")
    assert timeline_reset < tuner_reset < write_reset


def test_frozen_package_includes_producer_timeline():
    spec = _source("Glassless3D.spec")

    assert '"launcher.auto_tune_timeline"' in spec
