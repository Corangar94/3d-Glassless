# tests/test_mainwindow.py
import sys
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from launcher import diagnostics
from launcher.mainwindow import MainWindow

CONFIG = {
    "camera": {"index": 0},
    "screen": {"width_cm": 59.8, "height_cm": 33.6},
    "tracking": {
        "ipd_cm": 6.3,
        "smoothing_q": 0.01,
        "smoothing_r": 0.1,
        "hold_ms": 500,
    },
    "gui": {"compact_mode": False},
}


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def window(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)
    return win


def test_mainwindow_starts_in_expanded_mode(window):
    assert not window._compact
    assert window.width() >= 700
    assert window.height() >= 520


def test_mainwindow_toggle_switches_to_compact(window):
    window._toggle_mode()
    assert window._compact
    assert window.width() >= 700
    assert window.height() <= 140


def test_mainwindow_toggle_back_to_expanded(window):
    window._compact = True
    window._apply_mode()
    window._toggle_mode()
    assert not window._compact
    assert window.width() >= 700
    assert window.height() >= 520


def test_mainwindow_shows_overlay_first_runtime_cockpit(window):
    assert window._tabs.tabText(0) == "Runtime"
    assert "Overlay-first runtime" in window._hero_label.text()
    assert "Backend" in window._backend_tile.text()
    assert "Camera 0" in window._camera_tile.text()
    assert "Overlay" in window._overlay_tile.text()
    assert "SHM" in window._shm_tile.text()
    assert "Depth" in window._depth_tile.text()
    assert "Capture" in window._capture_tile.text()


def test_mainwindow_exposes_operator_action_buttons(window):
    buttons = {button.text() for button in window.findChildren(type(window._action_btn))}

    assert "Run diagnostics" in buttons
    assert "Collect support bundle" in buttons
    assert "Open quality monitor" in buttons


def test_mainwindow_exposes_builtin_comfort_presets(window):
    buttons = {button.text() for button in window.findChildren(type(window._action_btn))}

    assert "Safe comfort" in buttons
    assert "Balanced depth" in buttons
    assert "Strong depth" in buttons


def test_safe_comfort_preset_reduces_vertical_parallax_and_persists(qapp, tmp_path):
    import yaml

    cfg_path = str(tmp_path / "config.yaml")
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)

    win._apply_comfort_preset("safe")

    assert win._settings.strength_x == pytest.approx(0.75)
    assert win._settings.strength_y == pytest.approx(0.30)
    assert win._settings.virtual_depth_cm == pytest.approx(24.0)
    assert "Safe" in win._comfort_status.text()
    with open(cfg_path, encoding="utf-8") as f:
        saved = yaml.safe_load(f)
    assert saved["overlay"]["strength_y"] == pytest.approx(0.30)


def test_balanced_depth_preset_keeps_vertical_parallax_conservative(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)

    win._apply_comfort_preset("balanced")

    assert win._settings.strength_x == pytest.approx(1.00)
    assert win._settings.strength_y == pytest.approx(0.40)
    assert win._settings.virtual_depth_cm == pytest.approx(30.0)


def test_mainwindow_publishes_configured_depth_performance_mode(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    config = {**CONFIG, "overlay": {"depth_performance_mode": "fast"}}

    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=config, config_path=cfg_path)

    assert win._settings.depth_mode == 2
    assert win._depth_mode_combo.currentText() == "Fast depth"


def test_depth_performance_mode_change_persists_name(qapp, tmp_path):
    import yaml

    cfg_path = str(tmp_path / "config.yaml")
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)

    win._depth_mode_combo.setCurrentIndex(win._depth_mode_combo.findData(2))

    assert win._settings.depth_mode == 2
    with open(cfg_path, encoding="utf-8") as f:
        saved = yaml.safe_load(f)
    assert saved["overlay"]["depth_performance_mode"] == "fast"


def test_unknown_comfort_preset_is_ignored(window):
    before = window._settings

    window._apply_comfort_preset("missing")

    assert window._settings == before


def test_runtime_health_updates_from_overlay_summary(window):
    summary = diagnostics.OverlayRuntimeSummary(
        frame_count=120,
        acq_ok=118,
        acq_timeout=2,
        acq_lost=0,
        acq_other=0,
        shm_status="LIVE",
        shm_changes_per_sec=7,
        depth_total=28,
        depth_hz=8,
        head_z_cm=58.5,
        has_frame=True,
    )

    window._apply_runtime_health(summary)

    assert "LIVE 7/s" in window._shm_tile.text()
    assert "8 Hz" in window._depth_tile.text()
    assert "Frame OK" in window._capture_tile.text()
    assert "Depth OK" in window._comfort_status.text()


def test_runtime_health_keyboard_interrupt_requests_shutdown(window, monkeypatch):
    calls = []

    class FakeApp:
        def closeAllWindows(self):
            calls.append("close")

        def quit(self):
            calls.append("quit")

    def raise_keyboard_interrupt():
        raise KeyboardInterrupt

    monkeypatch.setattr("launcher.mainwindow.QApplication.instance", lambda: FakeApp())
    monkeypatch.setattr(window, "_read_overlay_summary", raise_keyboard_interrupt)

    window._safe_refresh_runtime_health()

    assert calls == ["close", "quit"]


def test_runtime_health_warns_and_applies_safe_when_depth_rate_low(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    summary = diagnostics.OverlayRuntimeSummary(
        frame_count=120,
        acq_ok=118,
        acq_timeout=2,
        acq_lost=0,
        acq_other=0,
        shm_status="LIVE",
        shm_changes_per_sec=7,
        depth_total=28,
        depth_hz=4,
        head_z_cm=58.5,
        has_frame=True,
    )
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)
    fake_thread = MagicMock()
    fake_thread.isRunning.return_value = True
    win._thread = fake_thread

    win._apply_runtime_health(summary)

    assert "LOW" in win._depth_tile.text()
    assert win._settings.strength_y == pytest.approx(0.30)
    assert "Safe preset applied automatically" in win._comfort_status.text()


def test_low_depth_safe_fallback_runs_once(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    summary = diagnostics.OverlayRuntimeSummary(
        frame_count=120,
        acq_ok=118,
        acq_timeout=2,
        acq_lost=0,
        acq_other=0,
        shm_status="LIVE",
        shm_changes_per_sec=7,
        depth_total=28,
        depth_hz=4,
        head_z_cm=58.5,
        has_frame=True,
    )
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)
    fake_thread = MagicMock()
    fake_thread.isRunning.return_value = True
    win._thread = fake_thread
    win._auto_safe_applied = False
    with patch.object(win, "_apply_comfort_preset") as apply:
        win._apply_runtime_health(summary)
        win._apply_runtime_health(summary)

    apply.assert_called_once_with("safe", reason="low_depth")


def test_startup_runtime_health_does_not_auto_persist_safe_from_old_log(qapp, tmp_path):
    import yaml

    cfg_path = str(tmp_path / "config.yaml")
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"overlay": {"head_dist_cm": 81.0}}, f)
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)

    win._apply_runtime_health(
        diagnostics.OverlayRuntimeSummary(
            frame_count=120,
            acq_ok=118,
            acq_timeout=2,
            acq_lost=0,
            acq_other=0,
            shm_status="LIVE",
            shm_changes_per_sec=7,
            depth_total=28,
            depth_hz=4,
            head_z_cm=58.5,
            has_frame=True,
        )
    )

    with open(cfg_path, encoding="utf-8") as f:
        saved = yaml.safe_load(f)
    assert saved["overlay"]["head_dist_cm"] == pytest.approx(81.0)
    assert win._auto_safe_applied is False


def test_mainwindow_xyz_labels_update_on_signal(window):
    window._on_position(2.5, -1.0, 57.3)
    assert "2.5" in window._label_x.text()
    assert "-1.0" in window._label_y.text()
    assert "57.3" in window._label_z.text()


def test_mainwindow_status_badge_tracking(window):
    window._on_status("tracking")
    assert "TRACKING" in window._status_label.text().upper()


def test_mainwindow_status_badge_error(window):
    window._on_status("error")
    assert "CAMERA" in window._status_label.text().upper() or \
           "ERROR" in window._status_label.text().upper()


def test_mainwindow_is_always_on_top(window):
    flags = window.windowFlags()
    assert flags & Qt.WindowType.WindowStaysOnTopHint


def test_mainwindow_publishes_configured_display_backend(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    config = {**CONFIG, "overlay": {"display_backend": "stereo_autostereo"}}

    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=config, config_path=cfg_path)

    assert win._settings.display_backend == 1


def test_mainwindow_accepts_legacy_numeric_display_backend(qapp, tmp_path):
    import yaml

    cfg_path = str(tmp_path / "config.yaml")
    config = {**CONFIG, "overlay": {"display_backend": 0}}

    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=config, config_path=cfg_path)
        win._on_save_config()

    assert win._display_backend_id == "desktop_overlay"
    assert win._settings.display_backend == 0
    with open(cfg_path, encoding="utf-8") as f:
        saved = yaml.safe_load(f)
    assert saved["overlay"]["display_backend"] == "desktop_overlay"


def test_mainwindow_settings_changes_preserve_configured_display_backend(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    config = {**CONFIG, "overlay": {"display_backend": "lightfield_quilt"}}

    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=config, config_path=cfg_path)
        win._on_settings_change()

    assert win._settings.display_backend == 2


def test_mainwindow_toggle_saves_compact_pref_when_config_absent(qapp, tmp_path):
    """Toggling mode writes compact_mode even when config file doesn't yet exist."""
    import yaml
    cfg_path = str(tmp_path / "config.yaml")
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)
    win._toggle_mode()  # triggers _save_compact_pref
    with open(cfg_path) as f:
        saved = yaml.safe_load(f)
    assert saved["gui"]["compact_mode"] is True


def test_start_tracking_rolls_back_when_tracker_process_fails(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    fake_tracker = MagicMock()
    fake_tracker.start.return_value = False
    fake_tracker.isRunning.return_value = False

    with patch("launcher.mainwindow.TrackerProcess", return_value=fake_tracker):
        win = MainWindow(config=CONFIG, config_path=cfg_path)
        win._start_tracking()

    assert win._thread is None
    assert "START TRACKING" in win._action_btn.text()
    assert "ERROR" in win._status_label.text().upper()


def test_open_debug_monitor_starts_diagnostics_module(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None

    with (
        patch("launcher.mainwindow.TrackerProcess"),
        patch("launcher.mainwindow.subprocess.Popen", return_value=fake_proc) as popen,
    ):
        win = MainWindow(config=CONFIG, config_path=cfg_path)
        win._open_debug_monitor()

    args = popen.call_args.args[0]
    assert args[1:] == ["-m", "tracker.debug_monitor"]
    assert popen.call_args.kwargs["cwd"].endswith("Glassless 3d")
    assert win._debug_monitor_proc is fake_proc


def test_run_diagnostics_starts_diagnostics_command(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")

    with (
        patch("launcher.mainwindow.TrackerProcess"),
        patch("launcher.mainwindow.subprocess.Popen") as popen,
    ):
        win = MainWindow(config=CONFIG, config_path=cfg_path)
        win._run_diagnostics()

    args = popen.call_args.args[0]
    assert args[:3] == [sys.executable, "-m", "launcher.diagnostics"]
    assert args[-1] == cfg_path


def test_collect_support_bundle_starts_support_command(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")

    with (
        patch("launcher.mainwindow.TrackerProcess"),
        patch("launcher.mainwindow.subprocess.Popen") as popen,
    ):
        win = MainWindow(config=CONFIG, config_path=cfg_path)
        win._collect_support_bundle()

    args = popen.call_args.args[0]
    assert args[:3] == [sys.executable, "-m", "scripts.collect_support"]
    assert "--output-dir" in args
    assert "support_bundle" in args
    assert args[-1] == cfg_path


def test_open_debug_monitor_is_idempotent_when_already_running(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None

    with (
        patch("launcher.mainwindow.TrackerProcess"),
        patch("launcher.mainwindow.subprocess.Popen") as popen,
    ):
        win = MainWindow(config=CONFIG, config_path=cfg_path)
        win._debug_monitor_proc = fake_proc
        win._open_debug_monitor()

    popen.assert_not_called()


def test_close_event_terminates_running_debug_monitor(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    fake_proc = MagicMock()
    fake_proc.poll.return_value = None
    fake_event = MagicMock()

    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)
        win._debug_monitor_proc = fake_proc
        win.closeEvent(fake_event)

    fake_proc.terminate.assert_called_once()
    fake_event.accept.assert_called_once()


def test_detect_screen_persists_overlay_screen_size(qapp, tmp_path):
    import yaml
    cfg_path = str(tmp_path / "config.yaml")
    with (
        patch("launcher.mainwindow.TrackerProcess"),
        patch("launcher.mainwindow.detect_screen_cm", return_value=(70.0, 40.0)),
    ):
        win = MainWindow(config=CONFIG, config_path=cfg_path)
        win._on_detect_screen()

    with open(cfg_path) as f:
        saved = yaml.safe_load(f)
    assert saved["overlay"]["screen_w_cm"] == pytest.approx(70.0)
    assert saved["overlay"]["screen_h_cm"] == pytest.approx(40.0)


def test_detect_screen_publishes_paired_size_once(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    with (
        patch("launcher.mainwindow.TrackerProcess"),
        patch("launcher.mainwindow.detect_screen_cm", return_value=(70.0, 40.0)),
    ):
        win = MainWindow(config=CONFIG, config_path=cfg_path)
        writer = win._settings_writer = MagicMock()
        win._on_detect_screen()

    assert writer.write.call_count == 1
    settings = writer.write.call_args.args[0]
    assert settings.screen_w_cm == pytest.approx(70.0)
    assert settings.screen_h_cm == pytest.approx(40.0)


def test_measure_head_persists_overlay_head_distance(qapp, tmp_path):
    import yaml
    cfg_path = str(tmp_path / "config.yaml")
    with (
        patch("launcher.mainwindow.TrackerProcess"),
        patch("launcher.mainwindow.measure_head_distance_or_none", return_value=72.5),
    ):
        win = MainWindow(config=CONFIG, config_path=cfg_path)
        win._on_measure_head()

    with open(cfg_path) as f:
        saved = yaml.safe_load(f)
    assert saved["overlay"]["head_dist_cm"] == pytest.approx(72.5)


def test_measure_head_failure_does_not_persist_fallback(qapp, tmp_path):
    import yaml
    cfg_path = str(tmp_path / "config.yaml")
    with open(cfg_path, "w") as f:
        yaml.safe_dump({"overlay": {"head_dist_cm": 81.0}}, f)

    with (
        patch("launcher.mainwindow.TrackerProcess"),
        patch("launcher.mainwindow.measure_head_distance_or_none", return_value=None),
    ):
        win = MainWindow(config=CONFIG, config_path=cfg_path)
        win._on_measure_head()

    with open(cfg_path) as f:
        saved = yaml.safe_load(f)
    assert saved["overlay"]["head_dist_cm"] == pytest.approx(81.0)
