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


def normalize_wire_timestamp(timestamp_ms: int) -> int:
    """Return a nonzero wrapping uint32 timestamp.

    ``0`` is the existing Python pose contract's "timestamp missing" sentinel.
    Windows uptime legitimately reaches zero at each 49.7-day rollover, so that
    one instant is encoded as ``0xFFFFFFFF`` instead. Relative to the native
    reader's raw ``GetTickCount() == 0`` value this is one millisecond old, and
    all existing wrap-safe subtraction continues to work. Reserving zero avoids
    silently replacing a real rollover sample with an unrelated later clock
    read in filters, validation, and shared-memory writers.
    """
    wire = int(timestamp_ms) & _UINT32_MASK
    return _UINT32_MASK if wire == 0 else wire


def elapsed_u32_ms(newer_ms: int, older_ms: int) -> int:
    """Return wrap-safe elapsed milliseconds on the shared uint32 clock."""
    return (int(newer_ms) - int(older_ms)) & _UINT32_MASK


def monotonic_ms() -> int:
    """Return the shared nonzero wire-clock timestamp as a wrapping uint32.

    The native overlay computes shared-memory age with ``GetTickCount()``. A
    generic monotonic clock is not guaranteed to share that epoch across APIs,
    even when both clocks advance monotonically. On Windows, publish the low 32
    bits of ``GetTickCount64()`` so Python writers and the native reader use the
    same uptime epoch and wrap-safe uint32 subtraction remains valid. Timestamp
    zero is reserved as "missing" and is encoded by ``normalize_wire_timestamp``.
    """
    return normalize_wire_timestamp(_wire_uptime_ms64())


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
    A zero capture timestamp means the producer did not supply camera time.
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
            capture_timestamp_ms=(
                monotonic_ms()
                if timestamp_ms is None
                else normalize_wire_timestamp(timestamp_ms)
            ),
        )


@dataclass(frozen=True)
class FilteredPose:
    """Filtered pose projected to a known producer target timestamp.

    ``publish_timestamp_ms`` is when the Python tracker publishes the packet.
    ``prediction_target_timestamp_ms`` is the wire-clock instant represented by
    the x/y/z values. They are normally equal, but an optional producer horizon
    may place the pose slightly into the future. The native renderer uses this
    distinction to compensate only the remaining publish-to-render latency,
    preventing accidental double prediction.
    """

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
    prediction_target_timestamp_ms: int = 0
    predicted: bool = False

    @property
    def xyz(self) -> tuple[float, float, float]:
        return self.x_cm, self.y_cm, self.z_cm
