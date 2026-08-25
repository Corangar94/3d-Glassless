"""Shared tracker pose types used by every backend and transport."""
from __future__ import annotations

from dataclasses import dataclass
import math
import time


def monotonic_ms() -> int:
    """Return a wrapping uint32-compatible monotonic millisecond timestamp."""
    return int(time.monotonic_ns() // 1_000_000) & 0xFFFF_FFFF


def finite_or(value: object, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return parsed if math.isfinite(parsed) else fallback


@dataclass(frozen=True)
class HeadPosition:
    """One camera-time head-pose measurement.

    Translation uses centimeters in the existing Glassless3D convention.
    Orientation uses degrees: yaw left/right, pitch up/down, roll clockwise.
    """

    x_cm: float
    y_cm: float
    z_cm: float
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    confidence: float = 1.0
    capture_timestamp_ms: int = 0

    @property
    def xyz(self) -> tuple[float, float, float]:
        return self.x_cm, self.y_cm, self.z_cm

    def with_timestamp_if_missing(self, timestamp_ms: int | None = None) -> "HeadPosition":
        if self.capture_timestamp_ms:
            return self
        return HeadPosition(
            x_cm=self.x_cm,
            y_cm=self.y_cm,
            z_cm=self.z_cm,
            yaw_deg=self.yaw_deg,
            pitch_deg=self.pitch_deg,
            roll_deg=self.roll_deg,
            confidence=self.confidence,
            capture_timestamp_ms=monotonic_ms() if timestamp_ms is None else timestamp_ms,
        )


@dataclass(frozen=True)
class FilteredPose:
    """Filtered and display-time-predicted pose published to the overlay."""

    x_cm: float
    y_cm: float
    z_cm: float
    vx_cm_s: float = 0.0
    vy_cm_s: float = 0.0
    vz_cm_s: float = 0.0
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    confidence: float = 0.0
    capture_timestamp_ms: int = 0
    publish_timestamp_ms: int = 0
    predicted: bool = False

    @property
    def xyz(self) -> tuple[float, float, float]:
        return self.x_cm, self.y_cm, self.z_cm
