"""Expand the 32-bit shared wire clock into a monotonic local timeline."""
from __future__ import annotations

_UINT32_MASK = 0xFFFF_FFFF
_HALF_RANGE = 0x8000_0000


def expand_u32_timestamp(
    timestamp_ms: int,
    previous_extended_ms: int | None,
) -> int:
    """Return a strictly increasing 64-bit representation of a wrapping u32 clock.

    The wire protocol intentionally keeps Windows uptime in 32 bits so the
    native overlay can use wrap-safe subtraction. MediaPipe LIVE_STREAM, on the
    other hand, requires monotonically increasing timestamps. This helper keeps
    those two requirements separate without changing the wire ABI.

    Small forward wrap is treated normally. A duplicate timestamp advances by
    one millisecond because MediaPipe requires strict monotonicity. A timestamp
    that appears to jump backward by less than half the uint32 range is treated
    as an out-of-order sample and also advances by one millisecond rather than
    pretending nearly 49.7 days elapsed.
    """
    wire = int(timestamp_ms) & _UINT32_MASK
    if previous_extended_ms is None:
        return wire

    previous = int(previous_extended_ms)
    previous_wire = previous & _UINT32_MASK
    delta = (wire - previous_wire) & _UINT32_MASK
    if delta == 0:
        return previous + 1
    if delta >= _HALF_RANGE:
        return previous + 1
    return previous + delta
