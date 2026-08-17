# tracker/tilt.py
"""Tilt-calibration helpers — pure math + stdlib only; no cv2 or mediapipe."""
from __future__ import annotations

import math
import os
from pathlib import Path
import statistics
import tempfile

import yaml

# Continuous tilt calibration parameters (shared with tracker_thread and main)
_TILT_WINDOW = 300  # rolling buffer size (~30 s at 10 Hz)
_TILT_MIN    = 30   # minimum samples before applying any correction
_TILT_EVERY  = 100  # re-estimate tilt every N face detections


def _calibrate_tilt(
    y_samples: list[float],
    z_samples: list[float],
    min_samples: int = 10,
) -> float | None:
    """Return auto-detected tilt_deg from a batch of samples, or None.

    Uses the median of (y, z) to compute arctan2 — the median is robust to
    temporary head leans which would skew a simple mean.
    """
    if len(y_samples) < min_samples or len(z_samples) < min_samples:
        return None
    med_z = statistics.median(z_samples)
    if med_z <= 0:
        return None
    med_y = statistics.median(y_samples)
    return math.degrees(math.atan2(med_y, med_z))


def _apply_camera_tilt(x: float, y: float, z: float, tilt_deg: float) -> tuple[float, float, float]:
    """Rotate head pose from camera space to screen space.

    tilt_deg > 0 means camera points downward (typical monitor-top mount).
    Rotating by +tilt_deg corrects for the camera tilt.
    """
    if tilt_deg == 0.0:
        return x, y, z
    rad = math.radians(tilt_deg)
    cos_t = math.cos(rad)
    sin_t = math.sin(rad)
    y_screen = y * cos_t - z * sin_t
    z_screen = y * sin_t + z * cos_t
    return x, y_screen, z_screen


def _save_tilt_to_config(config_path: str, tilt_deg: float) -> bool:
    """Persist tilt atomically, refusing to replace malformed configuration."""
    try:
        path = Path(config_path)
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError("configuration root must be a mapping")
        tracking = data.setdefault("tracking", {})
        if not isinstance(tracking, dict):
            raise ValueError("tracking configuration must be a mapping")
        tracking["camera_tilt_deg"] = round(tilt_deg, 2)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return True
    except (OSError, ValueError, yaml.YAMLError) as e:
        print(f"[tracker] Warning: could not save tilt to config: {e}")
        return False
