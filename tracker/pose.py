"""Shared tracker pose types used by every backend and transport."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import math
import os
import time


_UINT32_MASK = 0xFFFF_FFFF

if os.name == "nt":
    _kernel32 = ctypes.windll.kernel32
    _kernel32.GetTickCount64.restype = ctypes.c_ulonglong

    def _wire_uptime_ms64() -> int:
        """Return the Windows system-uptime clock used by the native overlay."""
        return int(_kernel32.GetTickCount64())
else:
    def _wire_uptime_ms64() -> int:
        """Portable fallback for non-Windows tooling and source analysis."""
        return int(time.monotonic_ns() // 1_000_000)


def monotonic_ms() -> int:
    """Return the shared wire-clock timestamp as a wrapping uint32 value.

    The native overlay computes shared-memory age with ``GetTickCount()``. A
    generic monotonic clock is not guaranteed to share that epoch across APIs,
    even when both clocks advance monotonically. On Windows, publish the low 32
    bits of ``GetTickCount64()`` so Python writers and the native reader use the
    same uptime epoch and the existing wrap-safe uint32 subtraction remains
    valid for sessions spanning the 49.7-day GetTickCount rollover.
    """
    return _wire_uptime_ms64() & _UINT32_MASK


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
