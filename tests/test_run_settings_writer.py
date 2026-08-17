from pathlib import Path

import yaml

from scripts import run_settings_writer


class RecordingWriter:
    def __init__(self):
        self.values = []

    def write(self, settings):
        self.values.append(settings)


def _write_config(path: Path, strength_x: float) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "overlay": {
                    "strength_x": strength_x,
                    "strength_y": 0.2,
                    "virtual_depth_cm": 24.0,
                    "depth_performance_mode": "fast",
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_write_settings_once_reloads_config_values(tmp_path):
    config_path = tmp_path / "config.yaml"
    writer = RecordingWriter()

    _write_config(config_path, 0.4)
    run_settings_writer.write_settings_once(config_path, writer)

    _write_config(config_path, 0.65)
    run_settings_writer.write_settings_once(config_path, writer)

    assert writer.values[0].strength_x == 0.4
    assert writer.values[1].strength_x == 0.65


def test_write_settings_once_publishes_configured_display_backend(tmp_path):
    config_path = tmp_path / "config.yaml"
    writer = RecordingWriter()
    config_path.write_text(
        yaml.safe_dump({"overlay": {"display_backend": "lightfield_quilt"}}, sort_keys=False),
        encoding="utf-8",
    )

    run_settings_writer.write_settings_once(config_path, writer)

    assert writer.values[-1].display_backend == 2


def test_write_settings_once_uses_display_calibration_panel_and_ipd(tmp_path):
    config_path = tmp_path / "config.yaml"
    writer = RecordingWriter()
    config_path.write_text(
        yaml.safe_dump(
            {
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
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    run_settings_writer.write_settings_once(config_path, writer)

    settings = writer.values[-1]
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
