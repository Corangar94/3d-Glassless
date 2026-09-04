"""Strict scalar validation for the versioned shared-settings ABI."""
from __future__ import annotations

import math
import numbers


UINT32_MAX = 0xFFFF_FFFF


def finite_float(value: object, field_name: str) -> float:
    """Return one finite real value without coercing booleans or strings."""
    if (
        isinstance(value, bool)
        or not isinstance(value, numbers.Real)
    ):
        raise ValueError(f"{field_name} must be a finite real number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite real number")
    return parsed


def uint32(value: object, field_name: str) -> int:
    """Return one true integral uint32 without truncation or text parsing."""
    if (
        isinstance(value, bool)
        or not isinstance(value, numbers.Integral)
    ):
        raise ValueError(
            f"{field_name} must be an unsigned 32-bit integer"
        )
    parsed = int(value)
    if not 0 <= parsed <= UINT32_MAX:
        raise ValueError(
            f"{field_name} must be an unsigned 32-bit integer"
        )
    return parsed
