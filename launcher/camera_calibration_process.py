"""Command construction for source and frozen camera-calibration children."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import sys


@dataclass(frozen=True)
class CameraCaptureConfig:
    index: int = 0
    width: int = 1280
    height: int = 720
    fps: float = 30.0

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("camera index cannot be negative")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("camera dimensions must be positive")
        if not math.isfinite(self.fps) or self.fps <= 0.0:
            raise ValueError("camera fps must be finite and positive")


def _launcher_prefix(
    *,
    executable: str | None = None,
    frozen: bool | None = None,
) -> list[str]:
    exe = executable or sys.executable
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    if is_frozen:
        return [exe, "--camera-calibration-child"]
    return [exe, "-m", "launcher", "--camera-calibration-child"]


def build_intrinsics_command(
    *,
    config_path: str | Path,
    camera: CameraCaptureConfig,
    square_mm: float,
    executable: str | None = None,
    frozen: bool | None = None,
) -> list[str]:
    square = float(square_mm)
    if not math.isfinite(square) or square <= 0.0:
        raise ValueError("square_mm must be finite and positive")
    return [
        *_launcher_prefix(executable=executable, frozen=frozen),
        "intrinsics",
        "--config",
        str(config_path),
        "--camera-index",
        str(camera.index),
        "--width",
        str(camera.width),
        "--height",
        str(camera.height),
        "--fps",
        f"{camera.fps:g}",
        "--samples",
        "18",
        "--pattern",
        "9x6",
        "--square-mm",
        f"{square:g}",
    ]


def build_center_command(
    *,
    config_path: str | Path,
    camera: CameraCaptureConfig,
    viewer_distance_cm: float,
    ipd_cm: float,
    executable: str | None = None,
    frozen: bool | None = None,
) -> list[str]:
    distance = float(viewer_distance_cm)
    ipd = float(ipd_cm)
    if not math.isfinite(distance) or distance <= 0.0:
        raise ValueError("viewer_distance_cm must be finite and positive")
    if not math.isfinite(ipd) or ipd <= 0.0:
        raise ValueError("ipd_cm must be finite and positive")
    return [
        *_launcher_prefix(executable=executable, frozen=frozen),
        "center",
        "--config",
        str(config_path),
        "--camera-index",
        str(camera.index),
        "--width",
        str(camera.width),
        "--height",
        str(camera.height),
        "--fps",
        f"{camera.fps:g}",
        "--viewer-distance-cm",
        f"{distance:g}",
        "--ipd-cm",
        f"{ipd:g}",
    ]


def build_board_command(
    output_path: str | Path,
    *,
    executable: str | None = None,
    frozen: bool | None = None,
) -> list[str]:
    return [
        *_launcher_prefix(executable=executable, frozen=frozen),
        "board",
        str(output_path),
        "--pattern",
        "9x6",
    ]
