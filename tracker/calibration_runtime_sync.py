"""Synchronize full camera calibration with tracker and shader projection settings."""
from __future__ import annotations
from tracker.calibration_cancel import CalibrationCancelled, check_cancelled
from tracker.config_store import ConfigStoreError, read_config, update_config, merge_config

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


def synchronize_runtime_projection(config_path: str | Path, geometry: CameraGeometry,
                                   *, viewer_distance_cm: float | None = None) -> None:
    patch = {"tracking": {}, "overlay": {}}
    fov = horizontal_fov_deg(geometry)
    if fov is not None:
        patch["tracking"]["camera_fov_deg"] = round(fov, 6)
        patch["overlay"]["camera_fov_deg"] = round(fov, 6)
    if viewer_distance_cm is not None:
        distance = float(viewer_distance_cm)
        if not math.isfinite(distance) or distance <= 0:
            raise ValueError("viewer_distance_cm must be finite and positive")
        patch["overlay"]["head_dist_cm"] = distance
        patch["overlay"]["display_calibration"] = {"viewer_distance_cm": distance}
    check_cancelled()
    merge_config(config_path, patch)
