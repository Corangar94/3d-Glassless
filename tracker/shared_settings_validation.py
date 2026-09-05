"""Strict scalar and enum validation for the shared-settings ABI."""
from __future__ import annotations

import math
import numbers
from collections.abc import Collection


UINT32_MAX = 0xFFFF_FFFF


def finite_float(value: object, field_name: str) -> float:
    """Return one finite real value without coercing bools or text."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"{field_name} must be a finite float")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{field_name} must be a finite float") from error
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite float")
    return parsed


def uint32(value: object, field_name: str) -> int:
    """Return one true integral uint32 without truncation or text parsing."""
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise ValueError(
            f"{field_name} must be an unsigned 32-bit integer"
        )
    parsed = int(value)
    if not 0 <= parsed <= UINT32_MAX:
        raise ValueError(
            f"{field_name} must be an unsigned 32-bit integer"
        )
    return parsed


def enum_uint32(
    value: object,
    field_name: str,
    allowed_values: Collection[int],
) -> int:
    """Return a uint32 that belongs to one explicit ABI enum domain."""
    parsed = uint32(value, field_name)
    if parsed not in allowed_values:
        allowed = ", ".join(str(item) for item in sorted(allowed_values))
        raise ValueError(f"{field_name} must be one of: {allowed}")
    return parsed
