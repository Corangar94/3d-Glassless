"""Audit-2 regression cases. Execution is deferred for this source-only change."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import os
import sys
import uuid
import pytest
import yaml

from launcher.overlay_health import OverlayProgressMonitor
from launcher.utility_commands import build_utility_command
from tracker.config_store import ConfigStoreError, merge_config, read_config, update_config
from tracker.runtime_channels import SESSION_ENV, channel_name, child_environment, validate_session


def summary(**changes):
    data = dict(frame_count=1, depth_total=1, depth_hz=0, depth_age_ms=9000,
                depth_published=1, instance_id="owned", has_frame=True,
                capture_state="running", capture_reason="bound", depth_failures=0,
                capture_revision=42, depth_revision=42, capture_poll_age_ms=10, depth_pending=False)
    data.update(changes)
    return SimpleNamespace(**data)


def test_static_scene_with_advancing_capture_heartbeat_is_not_a_depth_failure():
    now = [0.0]
    monitor = OverlayProgressMonitor(clock=lambda: now[0])
    for step in range(60):
        now[0] = step * 2.0
        result = monitor.observe(summary(frame_count=step + 1), "owned")
        assert result.healthy and result.restart_reason is None


@pytest.mark.parametrize("invalid", [
    {"depth_revision": 41}, {"depth_pending": True}, {"capture_poll_age_ms": 1001},
    {"depth_failures": 3}, {"capture_revision": 0}, {"depth_published": 0},
])
def test_idle_reuse_requires_all_scene_liveness_and_work_conditions(invalid):
    now = [0.0]
    monitor = OverlayProgressMonitor(clock=lambda: now[0])
    for step in range(7):
        now[0] = float(step)
        result = monitor.observe(summary(frame_count=step + 1, **invalid), "owned")
    assert not result.healthy and result.restart_reason


def test_even_coherent_idle_depth_cannot_replace_the_native_heartbeat():
    now = [0.0]
    monitor = OverlayProgressMonitor(clock=lambda: now[0])
    assert monitor.observe(summary(), "owned").healthy
    now[0] = 6.0
    assert monitor.observe(summary(), "owned").restart_reason == "overlay heartbeat stalled"


def test_capture_failure_does_not_gain_idle_reuse():
    from launcher.overlay_health import coherent_idle_scene
    assert not coherent_idle_scene(summary(capture_state="unavailable"))
    assert not coherent_idle_scene(summary(has_frame=False))


def test_configuration_writers_serialize_read_modify_write(tmp_path):
    config = tmp_path / "settings.yaml"
    merge_config(config, {"counter": 0, "presets": {"keep": {"strength_x": 1.0}}})
    def increment(_):
        def change(root):
            root["counter"] += 1
        update_config(config, change)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(increment, range(40)))
    root = read_config(config)
    assert root["counter"] == 40
    assert root["presets"]["keep"] == {"strength_x": 1.0}


def test_configuration_serialization_or_replace_failure_preserves_original(tmp_path, monkeypatch):
    import tracker.file_transactions as files
    config = tmp_path / "settings.yaml"
    merge_config(config, {"tracking": {"auto_tune": False}})
    before = config.read_bytes()
    with pytest.raises(yaml.YAMLError):
        update_config(config, lambda root: root.update(unserializable=object()))
    assert config.read_bytes() == before
    def fail_replace(*_args):
        raise OSError("injected atomic replace failure")
    monkeypatch.setattr(files.os, "replace", fail_replace)
    with pytest.raises(OSError, match="atomic replace"):
        merge_config(config, {"tracking": {"auto_tune": True}})
    assert config.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_malformed_yaml_is_never_replaced_by_defaults(tmp_path):
    config = tmp_path / "settings.yaml"
    config.write_text("tracking: [broken", encoding="utf-8")
    with pytest.raises(ConfigStoreError):
        merge_config(config, {"tracking": {"auto_tune": True}})
    assert config.read_text(encoding="utf-8") == "tracking: [broken"


def test_calibration_cancel_is_checked_before_config_commit(tmp_path, monkeypatch):
    from tracker.calibration_cancel import CANCEL_ENV, CalibrationCancelled
    config = tmp_path / "settings.yaml"
    merge_config(config, {"tracking": {"camera_tilt_deg": 1}})
    marker = tmp_path / "cancel"
    monkeypatch.setenv(CANCEL_ENV, str(marker))
    before = config.read_bytes()
    def mutate(root):
        root["tracking"]["camera_tilt_deg"] = 2
        marker.write_bytes(b"cancel")
    with pytest.raises(CalibrationCancelled):
        update_config(config, mutate)
    assert config.read_bytes() == before


@pytest.mark.parametrize("value", ["", "short", "A" * 32, "../" + "a" * 29, 123])
def test_malformed_tracking_namespace_fails_closed(value):
    with pytest.raises(ValueError):
        validate_session(value)


def test_tracking_names_are_isolated_and_explicit_custom_names_unchanged(monkeypatch):
    monkeypatch.delenv(SESSION_ENV, raising=False)
    assert channel_name("G3D") == "G3D"
    first, second = "a" * 32, "b" * 32
    assert channel_name("G3D", first) != channel_name("G3D", second)
    monkeypatch.setenv(SESSION_ENV, first)
    assert channel_name("G3D") == "Local\\Glassless3D_" + first + "_G3D"
    assert channel_name("Local\\explicit-test") == "Local\\explicit-test"
    assert SESSION_ENV not in child_environment(None)
    assert child_environment(second)[SESSION_ENV] == second


@pytest.mark.skipif(sys.platform != "win32", reason="Windows producer object lifetime")
def test_second_production_writer_is_refused_and_crash_style_handle_release_allows_new_owner(monkeypatch):
    from tracker.runtime_channels import ProducerLease
    monkeypatch.setenv(SESSION_ENV, uuid.uuid4().hex)
    with ProducerLease():
        with pytest.raises(RuntimeError, match="already owns"):
            ProducerLease()
    with ProducerLease():
        pass


def test_two_owned_tracker_objects_never_read_the_same_legacy_mapping(qapp, monkeypatch):
    from launcher import tracker_process as module
    opened = []
    launched = []
    def spawn(*args, **kwargs):
        launched.append(kwargs["env"][SESSION_ENV])
        proc = MagicMock(); proc.poll.return_value = None
        return proc
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    monkeypatch.setattr(module, "SharedMemoryReader", lambda name: opened.append(name) or MagicMock())
    monkeypatch.setattr(module, "TrackingStateReader", lambda name: MagicMock())
    first, second = module.TrackerProcess("a.yaml"), module.TrackerProcess("b.yaml")
    try:
        assert first._launch_process() and second._launch_process()
        assert launched[0] != launched[1]
        assert opened[0] != opened[1] and all(name != "G3D" for name in opened)
    finally:
        first._close_readers(); second._close_readers()
        first._proc = second._proc = None


@pytest.mark.parametrize("kind", ["debug-monitor", "diagnostics", "support-bundle"])
def test_frozen_utilities_use_explicit_dispatch_not_python_module_flags(kind):
    command = build_utility_command(kind, [], executable="Glassless3D.exe", frozen=True)
    assert command == ["Glassless3D.exe", "--utility-child", kind]
    source = build_utility_command(kind, [], executable="python.exe", frozen=False)
    assert source == ["python.exe", "-m", "launcher", "--utility-child", kind]


def test_native_invalid_snapshots_do_not_skip_frame_safety_work():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")
    frame = source.split("static void Frame() {", 1)[1].split("static void Cleanup()", 1)[0]
    assert "TryAttachPoseV2();" in frame
    assert "if (!ReadStablePose(&p)) return;" not in frame
    assert "if (!ReadStableSnapshot(g_stateView, &stateSnapshot)) return;" not in frame
    reader = source.split("static bool ReadStablePoseV2(", 1)[1].split("// Optional face-validity", 1)[0]
    assert "!acquired || !ValidatePoseV2(snapshot)" in reader
    assert "if (!g_poseV2SeqView) return ReadStableSnapshot" not in reader


@pytest.mark.parametrize("bad", ["wrong", float("nan"), float("inf"), True])
def test_invalid_preset_is_rejected_before_ui_or_transport_mutation(bad):
    from launcher.preset_validation import validated_preset
    from tracker.shared_settings import OverlaySettings
    with pytest.raises(ValueError):
        validated_preset({"strength_x": bad}, OverlaySettings())


def test_preset_rejection_does_not_leave_gui_controls_signal_blocked(tmp_path, monkeypatch, qapp):
    import launcher.mainwindow as module
    writer = MagicMock()
    monkeypatch.setattr(module, "load_preset", lambda *_: {"strength_x": "invalid"})
    window = module.MainWindow({"tracking": {"auto_tune": False}}, str(tmp_path / "settings.yaml"),
                               settings_writer=writer)
    try:
        calls = writer.write.call_count
        old = window._strength_x_slider.value()
        window._on_preset_load()
        assert not window._strength_x_slider.signalsBlocked()
        assert window._strength_x_slider.value() == old
        assert writer.write.call_count == calls
    finally:
        window.close()


def test_closing_calibration_requests_cancel_before_releasing_controller(tmp_path, monkeypatch, qapp):
    import launcher.camera_calibration_wizard as module
    dialog = module.CameraCalibrationDialog(str(tmp_path / "settings.yaml"))
    owner = MagicMock()
    dialog._process = owner
    dialog._cancel_path = tmp_path / "cancel"
    timers = []
    monkeypatch.setattr(module.QTimer, "singleShot", lambda delay, callback: timers.append((delay, callback)))
    event = MagicMock()
    dialog.closeEvent(event)
    event.ignore.assert_called_once()
    assert dialog._cancel_path.exists()
    assert dialog._process is owner and dialog._closing
    timers.pop(0)[1]()
    owner.terminate.assert_called_once()
    timers.pop(0)[1]()
    owner.kill.assert_called_once()
    dialog._process = None
    dialog.close()
