# tests/test_mainwindow.py
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

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
    # Both modes share width=430; expanded is taller than compact
    assert window.width() == 430
    assert window.height() > 200


def test_mainwindow_toggle_switches_to_compact(window):
    window._toggle_mode()
    assert window._compact
    assert window.width() == 430
    assert window.height() == 100


def test_mainwindow_toggle_back_to_expanded(window):
    window._compact = True
    window._apply_mode()
    window._toggle_mode()
    assert not window._compact
    assert window.width() == 430
    assert window.height() > 200


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
