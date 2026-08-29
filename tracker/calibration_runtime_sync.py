"""Synchronize full camera calibration with tracker and shader projection settings."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import yaml

from tracker.camera_geometry import CameraGeometry


def horizontal_fov_deg(geometry: CameraGeometry) -> float | None:
    """Return calibrated horizontal FOV, if lens intrinsics are available."""
    intrinsics = geometry.intrinsics
    if intrinsics is None or intrinsics.fx <= 0.0 or intrinsics.width <= 0:
        return None
    fov = math.degrees(2.0 * math.atan(intrinsics.width / (2.0 * intrinsics.fx)))
    return fov if math.isfinite(fov) and 1.0 < fov < 179.0 else None


def _mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    child = parent.get(key)
    if isinstance(child, dict):
        return child
    child = {}
    parent[key] = child
    return child


def synchronize_runtime_projection(
    config_path: str | Path,
    geometry: CameraGeometry,
    *,
    viewer_distance_cm: float | None = None,
) -> None:
    """Keep calibrated camera geometry and runtime projection on one basis.

    CameraGeometry is authoritative for the tracker. The native overlay still
    receives horizontal camera FOV and nominal viewer distance through the live
    settings block, so copy calibrated values into those compatibility fields
    instead of letting the two projection models silently diverge.
    """
    path = Path(config_path)
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ValueError("config top level must be a mapping")
    else:
        loaded = {}

    tracking = _mapping(loaded, "tracking")
    overlay = _mapping(loaded, "overlay")
    fov = horizontal_fov_deg(geometry)
    if fov is not None:
        tracking["camera_fov_deg"] = round(fov, 6)
        overlay["camera_fov_deg"] = round(fov, 6)

    if viewer_distance_cm is not None:
        distance = float(viewer_distance_cm)
        if not math.isfinite(distance) or distance <= 0.0:
            raise ValueError("viewer_distance_cm must be finite and positive")
        calibration = _mapping(overlay, "display_calibration")
        calibration["viewer_distance_cm"] = distance
        # Keep the legacy field synchronized for older launchers/config readers.
        overlay["head_dist_cm"] = distance

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(
        yaml.safe_dump(loaded, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)
