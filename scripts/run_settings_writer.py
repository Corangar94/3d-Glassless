"""Publish overlay settings from config.yaml and keep the SHM segment alive."""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker.shared_settings import OverlaySettings, SharedSettingsWriter
from tracker.display_backends import backend_code, normalize_backend_id


CONFIG_PATH = Path(os.environ.get("APPDATA", ".")) / "Glassless3D" / "config.yaml"
DEPTH_MODES = {"quality": 0, "balanced": 1, "fast": 2}
STEREO_LAYOUTS = {"full_sbs": 0, "half_sbs": 1}
EYE_ORDERS = {"left_right": 0, "right_left": 1}
TRACKING_MODES = {"glassless3d_managed": 0, "vendor_managed": 1}


def _float_value(data: dict[str, object], key: str, default: float) -> float:
    raw = data.get(key, default)
    if not isinstance(raw, (int, float, str)):
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _int_value(data: dict[str, object], key: str, default: int) -> int:
    raw = data.get(key, default)
    if not isinstance(raw, (int, float, str)):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _enum_value(data: dict[str, object], key: str, choices: dict[str, int], default: int) -> int:
    value = data.get(key)
    if isinstance(value, str):
        return choices.get(value.strip().lower(), default)
    try:
        code = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return code if code in choices.values() else default


def _depth_mode(data: dict[str, object]) -> int:
    value = data.get("depth_performance_mode", data.get("depth_mode", "balanced"))
    return DEPTH_MODES.get(str(value).strip().lower(), DEPTH_MODES["balanced"])


def _calibration_value(
    overlay: dict[str, object],
    calibration: dict[str, object],
    calibration_key: str,
    overlay_key: str,
    default: float,
) -> float:
    value = _float_value(calibration, calibration_key, 0.0)
    if value > 0.0:
        return value
    return _float_value(overlay, overlay_key, default)


def _positive_int_value(data: dict[str, object], key: str, default: int = 0) -> int:
    value = _int_value(data, key, default)
    return value if value > 0 else default


def _load_settings(config_path: Path) -> OverlaySettings:
    with config_path.open(encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    config = loaded if isinstance(loaded, dict) else {}
    overlay = config.get("overlay", {})
    if not isinstance(overlay, dict):
        overlay = {}
    calibration = overlay.get("display_calibration", {})
    if not isinstance(calibration, dict):
        calibration = {}
    try:
        display_backend = backend_code(normalize_backend_id(overlay.get("display_backend", "desktop_overlay")))
    except ValueError:
        display_backend = 0

    return OverlaySettings(
        strength_x=_float_value(overlay, "strength_x", 1.0),
        strength_y=_float_value(overlay, "strength_y", 1.0),
        virtual_depth_cm=_float_value(overlay, "virtual_depth_cm", 30.0),
        screen_w_cm=_calibration_value(overlay, calibration, "panel_width_cm", "screen_w_cm", 0.0),
        screen_h_cm=_calibration_value(overlay, calibration, "panel_height_cm", "screen_h_cm", 0.0),
        depth_curve=_int_value(overlay, "depth_curve", 1),
        depth_gamma=_float_value(overlay, "depth_gamma", 1.0),
        focus_radius=_float_value(overlay, "focus_radius", 0.1),
        head_dist_cm=_calibration_value(overlay, calibration, "viewer_distance_cm", "head_dist_cm", 60.0),
        camera_fov_deg=_float_value(overlay, "camera_fov_deg", 90.0),
        ipd_mm=_calibration_value(overlay, calibration, "ipd_mm", "ipd_mm", 64.0),
        smoothing_alpha=_float_value(overlay, "smoothing_alpha", 0.1),
        deadzone_mm=_float_value(overlay, "deadzone_mm", 5.0),
        display_backend=display_backend,
        depth_mode=_depth_mode(overlay),
        stereo_layout=_enum_value(calibration, "stereo_layout", STEREO_LAYOUTS, 0),
        eye_order=_enum_value(calibration, "eye_order", EYE_ORDERS, 0),
        panel_width_px=_positive_int_value(calibration, "panel_width_px"),
        panel_height_px=_positive_int_value(calibration, "panel_height_px"),
        focus_plane_cm=_float_value(calibration, "focus_plane_cm", 0.0),
        tracking_mode=_enum_value(calibration, "tracking_mode", TRACKING_MODES, 0),
    )


def write_settings_once(config_path: Path, writer: SharedSettingsWriter) -> OverlaySettings:
    """Load config and publish one settings snapshot."""
    settings = _load_settings(config_path)
    writer.write(settings)
    return settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args(argv)

    running = True

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    with SharedSettingsWriter() as writer:
        settings = write_settings_once(args.config, writer)
        print(
            "Glassless3D settings writer active "
            f"(depth_mode={settings.depth_mode}, strength={settings.strength_x:.2f})",
            flush=True,
        )
        while running:
            write_settings_once(args.config, writer)
            time.sleep(1.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
