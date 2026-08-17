"""Display backend calibration metadata for stereo/quilt targets."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Sequence
import argparse

import yaml

from tracker.display_backends import build_display_layout

StereoLayout = Literal["full_sbs", "half_sbs"]
EyeOrder = Literal["left_right", "right_left"]
TrackingMode = Literal["glassless3d_managed", "vendor_managed"]

_STEREO_LAYOUTS = {"full_sbs", "half_sbs"}
_EYE_ORDERS = {"left_right", "right_left"}
_TRACKING_MODES = {"glassless3d_managed", "vendor_managed"}


@dataclass(frozen=True)
class DisplayCalibration:
    backend_id: str
    columns: int
    rows: int
    view_count: int
    viewer_distance_cm: float = 60.0
    view_cone_deg: float = 40.0
    panel_width_px: int = 0
    panel_height_px: int = 0
    panel_width_cm: float = 0.0
    panel_height_cm: float = 0.0
    ipd_mm: float = 64.0
    stereo_layout: StereoLayout = "full_sbs"
    eye_order: EyeOrder = "left_right"
    focus_plane_cm: float = 0.0
    tracking_mode: TrackingMode = "glassless3d_managed"


def default_calibration(backend_id: str) -> DisplayCalibration:
    layout = build_display_layout(backend_id)
    return DisplayCalibration(
        backend_id=backend_id,
        columns=layout.columns,
        rows=layout.rows,
        view_count=layout.view_count,
    )


def save_calibration(
    config_path: str | Path,
    backend_id: str,
    viewer_distance_cm: float = 60.0,
    view_cone_deg: float = 40.0,
    panel_width_px: int = 0,
    panel_height_px: int = 0,
    panel_width_cm: float = 0.0,
    panel_height_cm: float = 0.0,
    ipd_mm: float = 64.0,
    stereo_layout: str = "full_sbs",
    eye_order: str = "left_right",
    focus_plane_cm: float = 0.0,
    tracking_mode: str = "glassless3d_managed",
) -> DisplayCalibration:
    _validate_positive("viewer_distance_cm", viewer_distance_cm)
    _validate_positive("view_cone_deg", view_cone_deg)
    _validate_non_negative("panel_width_px", panel_width_px)
    _validate_non_negative("panel_height_px", panel_height_px)
    _validate_non_negative("panel_width_cm", panel_width_cm)
    _validate_non_negative("panel_height_cm", panel_height_cm)
    _validate_positive("ipd_mm", ipd_mm)
    _validate_non_negative("focus_plane_cm", focus_plane_cm)
    stereo_layout_value = _one_of("stereo_layout", stereo_layout, _STEREO_LAYOUTS)
    eye_order_value = _one_of("eye_order", eye_order, _EYE_ORDERS)
    tracking_mode_value = _one_of("tracking_mode", tracking_mode, _TRACKING_MODES)
    calibration = default_calibration(backend_id)
    calibration = DisplayCalibration(
        backend_id=calibration.backend_id,
        columns=calibration.columns,
        rows=calibration.rows,
        view_count=calibration.view_count,
        viewer_distance_cm=viewer_distance_cm,
        view_cone_deg=view_cone_deg,
        panel_width_px=int(panel_width_px),
        panel_height_px=int(panel_height_px),
        panel_width_cm=float(panel_width_cm),
        panel_height_cm=float(panel_height_cm),
        ipd_mm=float(ipd_mm),
        stereo_layout=stereo_layout_value,  # type: ignore[arg-type]
        eye_order=eye_order_value,  # type: ignore[arg-type]
        focus_plane_cm=float(focus_plane_cm),
        tracking_mode=tracking_mode_value,  # type: ignore[arg-type]
    )
    path = Path(config_path)
    cfg = _load_config(path)
    overlay = cfg.setdefault("overlay", {})
    if not isinstance(overlay, dict):
        overlay = {}
        cfg["overlay"] = overlay
    overlay["display_backend"] = backend_id
    overlay["display_calibration"] = asdict(calibration)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(yaml.safe_dump(cfg, sort_keys=False)), encoding="utf-8")
    return calibration


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write display backend calibration to config.yaml")
    parser.add_argument("backend_id")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--viewer-distance-cm", type=float, default=60.0)
    parser.add_argument("--view-cone-deg", type=float, default=40.0)
    parser.add_argument("--panel-resolution", default="0x0", help="Panel resolution as WIDTHxHEIGHT")
    parser.add_argument("--panel-width-cm", type=float, default=0.0)
    parser.add_argument("--panel-height-cm", type=float, default=0.0)
    parser.add_argument("--ipd-mm", type=float, default=64.0)
    parser.add_argument("--stereo-layout", choices=sorted(_STEREO_LAYOUTS), default="full_sbs")
    parser.add_argument("--eye-order", choices=sorted(_EYE_ORDERS), default="left_right")
    parser.add_argument("--focus-plane-cm", type=float, default=0.0)
    parser.add_argument("--tracking-mode", choices=sorted(_TRACKING_MODES), default="glassless3d_managed")
    args = parser.parse_args(argv)
    panel_width_px, panel_height_px = _parse_resolution(args.panel_resolution)

    calibration = save_calibration(
        args.config,
        backend_id=args.backend_id,
        viewer_distance_cm=args.viewer_distance_cm,
        view_cone_deg=args.view_cone_deg,
        panel_width_px=panel_width_px,
        panel_height_px=panel_height_px,
        panel_width_cm=args.panel_width_cm,
        panel_height_cm=args.panel_height_cm,
        ipd_mm=args.ipd_mm,
        stereo_layout=args.stereo_layout,
        eye_order=args.eye_order,
        focus_plane_cm=args.focus_plane_cm,
        tracking_mode=args.tracking_mode,
    )
    print(f"wrote {calibration.backend_id} calibration to {args.config}")
    return 0


def _load_config(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("config top-level YAML must be a mapping")
    return data


def _validate_positive(name: str, value: float) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _validate_non_negative(name: str, value: float) -> None:
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


def _one_of(name: str, value: str, allowed: set[str]) -> str:
    normalized = str(value).strip().lower()
    if normalized not in allowed:
        expected = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {expected}")
    return normalized


def _parse_resolution(value: str) -> tuple[int, int]:
    raw = str(value).strip().lower()
    if raw in {"", "0", "0x0"}:
        return 0, 0
    parts = raw.split("x", 1)
    if len(parts) != 2:
        raise ValueError("panel-resolution must use WIDTHxHEIGHT")
    width = int(parts[0])
    height = int(parts[1])
    _validate_non_negative("panel_width_px", width)
    _validate_non_negative("panel_height_px", height)
    return width, height


if __name__ == "__main__":
    raise SystemExit(main())
