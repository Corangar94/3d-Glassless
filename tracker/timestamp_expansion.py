"""Expand the 32-bit shared wire clock into a monotonic local timeline."""
from __future__ import annotations

_UINT32_MASK = 0xFFFF_FFFF
_HALF_RANGE = 0x8000_0000


def forward_u32_delta(timestamp_ms: int, previous_timestamp_ms: int) -> int | None:
    """Return a forward wrap-safe delta, or None for duplicate/backward input."""
    current = int(timestamp_ms) & _UINT32_MASK
    previous = int(previous_timestamp_ms) & _UINT32_MASK
    delta = (current - previous) & _UINT32_MASK
    if delta == 0 or delta >= _HALF_RANGE:
        return None
    return delta


def expand_u32_timestamp(
    timestamp_ms: int,
    previous_wire_timestamp_ms: int | None,
    previous_extended_ms: int | None,
) -> int | None:
    """Expand an accepted wrapping u32 timestamp onto a monotonic 64-bit clock.

    The wire protocol intentionally keeps Windows uptime in 32 bits so the
    native overlay can use wrap-safe subtraction. MediaPipe LIVE_STREAM, on the
    other hand, requires strictly increasing timestamps. A legitimate uint32
    rollover is expanded forward. Duplicate or small backward/out-of-order
    samples return ``None`` so callers can drop them instead of inventing a new
    camera timestamp.
    """
    wire = int(timestamp_ms) & _UINT32_MASK
    if previous_wire_timestamp_ms is None or previous_extended_ms is None:
        return wire
    delta = forward_u32_delta(wire, previous_wire_timestamp_ms)
    if delta is None:
        return None
    return int(previous_extended_ms) + delta
