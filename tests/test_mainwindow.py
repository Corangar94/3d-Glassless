# tests/test_mainwindow.py
import sys
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QGroupBox, QScrollArea

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
    assert window.maximumWidth() > window.minimumWidth()
    assert window.maximumHeight() > window.minimumHeight()


def test_mainwindow_tabs_scroll_when_content_exceeds_viewport(window):
    assert isinstance(window._tabs.widget(0), QScrollArea)
    assert isinstance(window._tabs.widget(1), QScrollArea)
    assert window._tabs.widget(0).widgetResizable()
    assert window._tabs.widget(1).widgetResizable()


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


def test_mainwindow_defaults_new_profile_to_non_injecting_desktop(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)

    assert win._active_profile.play_context.value == "online_multiplayer"
    assert win._policy_decision.active_mode.value == "non_injecting_desktop"
    assert "Non-injecting desktop" in win._profile_mode_label.text()


def test_mainwindow_shows_online_compatibility_disclaimer(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)

    assert (
        win._profile_disclaimer_label.text()
        == "Online compatibility is title-specific and subject to the game publisher and anti-cheat policy."
    )


def test_mainwindow_online_context_disables_and_clears_advanced_mode(qapp, tmp_path):
    import yaml

    cfg_path = str(tmp_path / "config.yaml")
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)

    win._play_context_combo.setCurrentIndex(
        win._play_context_combo.findData("offline_singleplayer")
    )
    win._requested_mode_combo.setCurrentIndex(
        win._requested_mode_combo.findData("offline_advanced")
    )
    win._advanced_ack_checkbox.setChecked(True)
    win._play_context_combo.setCurrentIndex(
        win._play_context_combo.findData("online_multiplayer")
    )

    with open(cfg_path, encoding="utf-8") as config_file:
        saved = yaml.safe_load(config_file)
    profile = saved["game_profiles"]["default"]
    assert win._requested_mode_combo.isEnabled() is False
    assert profile["requested_mode"] == "non_injecting_desktop"
    assert profile["advanced_acknowledged"] is False


def test_mainwindow_game_profile_panel_has_well_formed_stylesheet(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)

    panel = next(
        panel for panel in win.findChildren(QGroupBox) if panel.title() == "Game profile"
    )
    assert "}}QGroupBox::title" not in panel.styleSheet()


def test_mainwindow_online_profile_starts_non_injecting_runtime(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    config = {
        **CONFIG,
        "game_profiles": {
            "online": {
                "display_name": "Online",
                "executable_path": "C:/Games/Online.exe",
                "play_context": "online_multiplayer",
                "requested_mode": "offline_advanced",
                "advanced_acknowledged": True,
            }
        },
        "active_game_profile": "online",
    }
    with patch("launcher.mainwindow.TrackerProcess") as tracker_cls:
        win = MainWindow(config=config, config_path=cfg_path)
        win._start_tracking()

    tracker_cls.assert_called_once_with(config_path=cfg_path)
    assert win._policy_decision.active_mode.value == "non_injecting_desktop"
    assert "Non-injecting desktop" in win._profile_mode_label.text()


def test_mainwindow_persists_acknowledged_offline_advanced_profile(qapp, tmp_path):
    import yaml

    cfg_path = str(tmp_path / "config.yaml")
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)

    win._play_context_combo.setCurrentIndex(
        win._play_context_combo.findData("offline_singleplayer")
    )
    win._requested_mode_combo.setCurrentIndex(
        win._requested_mode_combo.findData("offline_advanced")
    )
    win._advanced_ack_checkbox.setChecked(True)

    with open(cfg_path, encoding="utf-8") as config_file:
        saved = yaml.safe_load(config_file)
    profile = saved["game_profiles"]["default"]
    assert profile["play_context"] == "offline_singleplayer"
    assert profile["requested_mode"] == "offline_advanced"
    assert profile["advanced_acknowledged"] is True
    assert win._policy_decision.active_mode.value == "offline_advanced"


def test_mainwindow_persists_active_profile_executable_path(qapp, tmp_path):
    import yaml

    cfg_path = str(tmp_path / "config.yaml")
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)

    win._profile_executable_edit.setText("C:/Games/Story/Story.exe")
    win._profile_executable_edit.editingFinished.emit()

    with open(cfg_path, encoding="utf-8") as config_file:
        saved = yaml.safe_load(config_file)
    assert saved["game_profiles"]["default"]["executable_path"] == "C:/Games/Story/Story.exe"


def test_mainwindow_can_add_a_distinct_game_profile(qapp, tmp_path):
    import yaml

    cfg_path = str(tmp_path / "config.yaml")
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)

    with patch("launcher.mainwindow.QInputDialog.getText", return_value=("Arena", True)):
        win._add_game_profile()

    assert win._active_profile_id == "arena"
    assert win._active_profile.display_name == "Arena"
    with open(cfg_path, encoding="utf-8") as config_file:
        saved = yaml.safe_load(config_file)
    assert saved["active_game_profile"] == "arena"
    assert saved["game_profiles"]["arena"]["requested_mode"] == "non_injecting_desktop"


def test_mainwindow_persists_selected_game_profile(qapp, tmp_path):
    import yaml

    cfg_path = str(tmp_path / "config.yaml")
    config = {
        **CONFIG,
        "game_profiles": {
            "default": {
                "display_name": "Default profile",
                "executable_path": "",
                "play_context": "online_multiplayer",
                "requested_mode": "non_injecting_desktop",
                "advanced_acknowledged": False,
            },
            "story": {
                "display_name": "Story",
                "executable_path": "C:/Games/Story.exe",
                "play_context": "offline_singleplayer",
                "requested_mode": "non_injecting_desktop",
                "advanced_acknowledged": False,
            },
        },
        "active_game_profile": "default",
    }
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=config, config_path=cfg_path)

    win._profile_combo.setCurrentIndex(win._profile_combo.findData("story"))

    with open(cfg_path, encoding="utf-8") as config_file:
        saved = yaml.safe_load(config_file)
    assert win._active_profile_id == "story"
    assert saved["active_game_profile"] == "story"


def test_mainwindow_surfaces_malformed_profile_config_without_replacing_it(qapp, tmp_path):
    config_path = tmp_path / "config.yaml"
    original = "game_profiles: [unterminated\n"
    config_path.write_text(original, encoding="utf-8")

    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=str(config_path))

    assert config_path.read_text(encoding="utf-8") == original
    assert "Profile configuration error" in win._profile_mode_label.text()


def test_mainwindow_preset_save_does_not_overwrite_malformed_config(qapp, tmp_path):
    config_path = tmp_path / "config.yaml"
    original = "presets: [unterminated\n"
    config_path.write_text(original, encoding="utf-8")

    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=str(config_path))

    win._preset_combo.setCurrentText("safe")
    win._on_preset_save()

    assert config_path.read_text(encoding="utf-8") == original
    assert "configuration error" in win._comfort_status.text().lower()


def test_mainwindow_compact_preference_does_not_overwrite_newly_malformed_config(qapp, tmp_path):
    config_path = tmp_path / "config.yaml"
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=str(config_path))
    original = "gui: [unterminated\n"
    config_path.write_text(original, encoding="utf-8")

    win._save_compact_pref()

    assert config_path.read_text(encoding="utf-8") == original


def test_mainwindow_recalibration_does_not_overwrite_newly_malformed_config(qapp, tmp_path):
    config_path = tmp_path / "config.yaml"
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=str(config_path))
    original = "tracking: [unterminated\n"
    config_path.write_text(original, encoding="utf-8")

    win._on_recalibrate_tilt()

    assert config_path.read_text(encoding="utf-8") == original
    assert "error" in win._tilt_status.text().lower()


def test_mainwindow_compact_preference_does_not_overwrite_non_mapping_config(qapp, tmp_path):
    config_path = tmp_path / "config.yaml"
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=str(config_path))
    original = "- not-a-config-mapping\n"
    config_path.write_text(original, encoding="utf-8")

    win._save_compact_pref()

    assert config_path.read_text(encoding="utf-8") == original


def test_mainwindow_recalibration_does_not_overwrite_non_mapping_config(qapp, tmp_path):
    config_path = tmp_path / "config.yaml"
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=str(config_path))
    original = "- not-a-config-mapping\n"
    config_path.write_text(original, encoding="utf-8")

    win._on_recalibrate_tilt()

    assert config_path.read_text(encoding="utf-8") == original
    assert "error" in win._tilt_status.text().lower()


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
    assert win._settings.depth_curve == 2
    assert win._settings.depth_gamma == pytest.approx(2.0)


def test_strong_depth_preset_uses_far_scene_gamma_curve(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)

    win._apply_comfort_preset("strong")

    assert win._settings.depth_curve == 2
    assert win._settings.depth_gamma == pytest.approx(2.2)


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
            calls.append("global-close")

        def quit(self):
            calls.append("global-quit")

    with monkeypatch.context() as patch_context:
        patch_context.setattr("launcher.mainwindow.QApplication.instance", lambda: FakeApp())
        patch_context.setattr(
            window,
            "_shutdown_application",
            lambda: calls.append("seam"),
            raising=False,
        )
        patch_context.setattr(
            window,
            "_read_overlay_summary",
            lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        window._safe_refresh_runtime_health()

    assert calls == ["seam"]


def test_runtime_health_warns_without_overriding_depth_preset(qapp, tmp_path):
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
    win._apply_runtime_health(summary)
    win._apply_runtime_health(summary)

    assert "LOW" in win._depth_tile.text()
    assert win._settings.strength_y == pytest.approx(1.0)
    assert "Safe preset applied automatically" not in win._comfort_status.text()
    assert "keeping current preset" in win._comfort_status.text()


def test_runtime_health_restarts_overlay_when_process_exited(window):
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
    fake_thread = MagicMock()
    fake_thread.isRunning.return_value = True
    window._thread = fake_thread
    window._overlay_started = True
    window._overlay = MagicMock()
    window._overlay.is_running.return_value = False

    window._apply_runtime_health(summary)

    window._overlay.start.assert_called_once()
    assert "Restarted" in window._overlay_tile.text()


def test_runtime_health_restarts_overlay_after_repeated_capture_loss(window):
    summary = diagnostics.OverlayRuntimeSummary(
        frame_count=120,
        acq_ok=118,
        acq_timeout=2,
        acq_lost=3,
        acq_other=0,
        shm_status="LIVE",
        shm_changes_per_sec=7,
        depth_total=28,
        depth_hz=8,
        head_z_cm=58.5,
        has_frame=False,
    )
    fake_thread = MagicMock()
    fake_thread.isRunning.return_value = True
    window._thread = fake_thread
    window._overlay_started = True
    window._overlay = MagicMock()
    window._overlay.is_running.return_value = True

    window._apply_runtime_health(summary)
    window._apply_runtime_health(summary)
    window._apply_runtime_health(summary)

    window._overlay.stop.assert_called_once()
    window._overlay.start.assert_called_once()
    assert "Restarted" in window._overlay_tile.text()


def test_runtime_health_does_not_restart_an_intentionally_unavailable_capture(window):
    summary = diagnostics.OverlayRuntimeSummary(
        frame_count=120,
        acq_ok=118,
        acq_timeout=2,
        acq_lost=3,
        acq_other=0,
        shm_status="LIVE",
        shm_changes_per_sec=7,
        depth_total=28,
        depth_hz=8,
        head_z_cm=58.5,
        has_frame=False,
        capture_state="unavailable",
        capture_reason="target_spans_output",
    )
    fake_thread = MagicMock()
    fake_thread.isRunning.return_value = True
    window._thread = fake_thread
    window._overlay_started = True
    window._overlay = MagicMock()
    window._overlay.is_running.return_value = True

    window._apply_runtime_health(summary)
    window._apply_runtime_health(summary)
    window._apply_runtime_health(summary)

    window._overlay.stop.assert_not_called()
    window._overlay.start.assert_not_called()
    assert "Unavailable" in window._capture_tile.text()


def test_low_depth_runtime_health_does_not_apply_safe_preset(qapp, tmp_path):
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
    with patch.object(win, "_apply_comfort_preset") as apply:
        win._apply_runtime_health(summary)
        win._apply_runtime_health(summary)
        win._apply_runtime_health(summary)
        win._apply_runtime_health(summary)

    apply.assert_not_called()


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
    assert "keeping current preset" in win._comfort_status.text()


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


def test_tracker_error_stops_overlay_and_restores_auto_hidden_window(window):
    fake_thread = MagicMock()
    window._thread = fake_thread
    window._overlay = MagicMock()
    window._overlay_started = True
    window._hidden_for_overlay = True
    window.showNormal = MagicMock()

    window._on_status("error")

    fake_thread.stop.assert_called_once()
    window._overlay.stop.assert_called_once()
    window.showNormal.assert_called_once()
    assert window._thread is None
    assert window._hidden_for_overlay is False
    assert "START TRACKING" in window._action_btn.text()


def test_mainwindow_status_badge_restarting(window):
    window._on_status("restarting")

    assert "RESTART" in window._status_label.text().upper()
    assert "RESTART" in window._tracker_tile.text().upper()


def test_mainwindow_is_always_on_top(window):
    flags = window.windowFlags()
    assert flags & Qt.WindowType.WindowStaysOnTopHint


def test_mainwindow_publishes_configured_display_backend(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    config = {**CONFIG, "overlay": {"display_backend": "stereo_autostereo"}}

    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=config, config_path=cfg_path)

    assert win._settings.display_backend == 1


def test_mainwindow_uses_display_calibration_for_panel_and_ipd(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    config = {
        **CONFIG,
        "overlay": {
            "screen_w_cm": 0.0,
            "screen_h_cm": 0.0,
            "ipd_mm": 64.0,
            "display_calibration": {
                "panel_width_px": 3840,
                "panel_height_px": 1080,
                "panel_width_cm": 34.4,
                "panel_height_cm": 19.3,
                "ipd_mm": 63.5,
                "stereo_layout": "half_sbs",
                "eye_order": "right_left",
                "focus_plane_cm": 12.0,
                "tracking_mode": "vendor_managed",
                "viewer_distance_cm": 65.0,
            },
        },
    }

    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=config, config_path=cfg_path)

    assert win._settings.screen_w_cm == 34.4
    assert win._settings.screen_h_cm == 19.3
    assert win._settings.ipd_mm == 63.5
    assert win._settings.stereo_layout == 1
    assert win._settings.eye_order == 1
    assert win._settings.panel_width_px == 3840
    assert win._settings.panel_height_px == 1080
    assert win._settings.focus_plane_cm == 12.0
    assert win._settings.tracking_mode == 1
    assert win._settings.head_dist_cm == 65.0


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


def test_mainwindow_settings_changes_preserve_display_calibration(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    config = {
        **CONFIG,
        "overlay": {
            "display_backend": "stereo_autostereo",
            "display_calibration": {
                "panel_width_px": 3840,
                "panel_height_px": 1080,
                "panel_width_cm": 34.4,
                "panel_height_cm": 19.3,
                "ipd_mm": 63.5,
                "stereo_layout": "half_sbs",
                "eye_order": "right_left",
                "focus_plane_cm": 12.0,
                "tracking_mode": "vendor_managed",
                "viewer_distance_cm": 65.0,
            },
        },
    }

    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=config, config_path=cfg_path)
        win._on_settings_change()

    assert win._settings.display_backend == 1
    assert win._settings.stereo_layout == 1
    assert win._settings.eye_order == 1
    assert win._settings.panel_width_px == 3840
    assert win._settings.panel_height_px == 1080
    assert win._settings.focus_plane_cm == 12.0
    assert win._settings.tracking_mode == 1


def test_mainwindow_save_preserves_nested_display_calibration(qapp, tmp_path):
    import yaml

    cfg_path = str(tmp_path / "config.yaml")
    config = {
        **CONFIG,
        "overlay": {
            "display_backend": "stereo_autostereo",
            "display_calibration": {
                "panel_width_px": 3840,
                "panel_height_px": 1080,
                "panel_width_cm": 34.4,
                "panel_height_cm": 19.3,
                "ipd_mm": 63.5,
                "stereo_layout": "half_sbs",
                "eye_order": "right_left",
                "focus_plane_cm": 12.0,
                "tracking_mode": "vendor_managed",
                "viewer_distance_cm": 65.0,
            },
        },
    }

    with patch("launcher.mainwindow.TrackerProcess"):
        win = MainWindow(config=config, config_path=cfg_path)
        win._on_save_config()

    with open(cfg_path, encoding="utf-8") as f:
        saved = yaml.safe_load(f)

    overlay = saved["overlay"]
    assert overlay["display_backend"] == "stereo_autostereo"
    assert overlay["display_calibration"]["stereo_layout"] == "half_sbs"
    assert overlay["display_calibration"]["eye_order"] == "right_left"
    assert overlay["display_calibration"]["panel_width_px"] == 3840
    assert "stereo_layout" not in overlay
    assert "eye_order" not in overlay
    assert "panel_width_px" not in overlay
    assert "focus_plane_cm" not in overlay


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


def test_mainwindow_minimizes_after_overlay_starts_to_avoid_capture_contamination(window):
    window._thread = MagicMock()
    window._overlay = MagicMock()
    window.showMinimized = MagicMock()

    window._on_tracker_status_for_overlay("tracking")

    window._overlay.start.assert_called_once()
    window.showMinimized.assert_called_once()
    assert window._hidden_for_overlay is True


def test_stop_tracking_restores_window_after_overlay_auto_hide(window):
    fake_thread = MagicMock()
    window._thread = fake_thread
    window._overlay = MagicMock()
    window._hidden_for_overlay = True
    window.showNormal = MagicMock()

    window._stop_tracking()

    window.showNormal.assert_called_once()
    assert window._hidden_for_overlay is False


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
    assert "--require-live-runtime" in args
    assert args[args.index("--config") + 1] == cfg_path


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
