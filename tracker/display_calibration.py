"""Display backend calibration metadata for stereo/quilt targets."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence
import argparse

import yaml

from tracker.display_backends import build_display_layout


@dataclass(frozen=True)
class DisplayCalibration:
    backend_id: str
    columns: int
    rows: int
    view_count: int
    viewer_distance_cm: float = 60.0
    view_cone_deg: float = 40.0


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
) -> DisplayCalibration:
    if viewer_distance_cm <= 0:
        raise ValueError("viewer_distance_cm must be positive")
    if view_cone_deg <= 0:
        raise ValueError("view_cone_deg must be positive")
    calibration = default_calibration(backend_id)
    calibration = DisplayCalibration(
        backend_id=calibration.backend_id,
        columns=calibration.columns,
        rows=calibration.rows,
        view_count=calibration.view_count,
        viewer_distance_cm=viewer_distance_cm,
        view_cone_deg=view_cone_deg,
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
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return calibration


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write display backend calibration to config.yaml")
    parser.add_argument("backend_id")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--viewer-distance-cm", type=float, default=60.0)
    parser.add_argument("--view-cone-deg", type=float, default=40.0)
    args = parser.parse_args(argv)

    calibration = save_calibration(
        args.config,
        backend_id=args.backend_id,
        viewer_distance_cm=args.viewer_distance_cm,
        view_cone_deg=args.view_cone_deg,
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


if __name__ == "__main__":
    raise SystemExit(main())
