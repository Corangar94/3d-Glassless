# tracker/debug_monitor.py
"""Live read-only debug monitor for Glassless3D parallax state.

Pure functions (compute_shift_pct, _shift_tag, _is_stale) are importable
without triggering PySide6; the Qt window is created only when main() runs.
"""
from __future__ import annotations


def compute_shift_pct(
    head_x: float,
    head_y: float,
    head_z: float,
    strength_x: float,
    strength_y: float,
    virtual_depth_cm: float,
    screen_w_cm: float,
    screen_h_cm: float,
) -> tuple[float, float]:
    """Return (shift_x_pct, shift_y_pct) matching the overlay HLSL parallax formula.

    Formula: f = oz / (hz + oz);  shift_pct = abs(head / screen) * f * strength * 100
    """
    denom = head_z + virtual_depth_cm
    f = virtual_depth_cm / max(denom, 0.001)
    shift_x = abs(head_x / max(screen_w_cm, 0.001)) * f * strength_x * 100.0
    shift_y = abs(head_y / max(screen_h_cm, 0.001)) * f * strength_y * 100.0
    return shift_x, shift_y


def _shift_tag(shift_x_pct: float, shift_y_pct: float) -> str:
    """Return 'GOOD' / 'HIGH' / 'DANGER' based on worst-axis shift."""
    worst = max(shift_x_pct, shift_y_pct)
    if worst < 2.0:
        return "GOOD"
    if worst < 4.0:
        return "HIGH"
    return "DANGER"


def _is_stale(timestamp_ms: int, now_ms: int, threshold_ms: int = 500) -> bool:
    """Return True if timestamp_ms is older than threshold_ms. Handles 32-bit wrap."""
    age = (now_ms - timestamp_ms) & 0xFFFF_FFFF
    return age > threshold_ms
