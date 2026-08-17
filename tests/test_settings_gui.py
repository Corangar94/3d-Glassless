from launcher import settings_gui
from tracker.shared_settings import OverlaySettings
import yaml


def test_overlay_settings_from_config_preserves_backend_and_calibration():
    settings = settings_gui._overlay_settings_from_config(
        {
            "display_backend": "lightfield_quilt",
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
        }
    )

    assert settings.display_backend == 2
    assert settings.screen_w_cm == 34.4
    assert settings.screen_h_cm == 19.3
    assert settings.ipd_mm == 63.5
    assert settings.stereo_layout == 1
    assert settings.eye_order == 1
    assert settings.panel_width_px == 3840
    assert settings.panel_height_px == 1080
    assert settings.focus_plane_cm == 12.0
    assert settings.tracking_mode == 1
    assert settings.head_dist_cm == 65.0


def test_save_overlay_settings_replaces_config_atomically(tmp_path, monkeypatch):
    config_path = tmp_path / "Glassless3D" / "config.yaml"
    config_path.parent.mkdir()
    config_path.write_text("tracking:\n  hold_ms: 500\n", encoding="utf-8")
    monkeypatch.setattr(settings_gui, "CONFIG_PATH", config_path)

    settings_gui._save_overlay_settings(OverlaySettings(strength_x=1.75))

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["tracking"]["hold_ms"] == 500
    assert saved["overlay"]["strength_x"] == 1.75
    assert not list(config_path.parent.glob("tmp*"))
