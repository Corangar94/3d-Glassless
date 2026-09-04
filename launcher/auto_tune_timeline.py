"""Map legacy uint32 pose timestamps onto an auto-tuning sample timeline."""
from __future__ import annotations

from dataclasses import dataclass
import numbers

from tracker.timestamp_expansion import expand_u32_timestamp


_UINT32_MASK = 0xFFFF_FFFF


@dataclass(frozen=True)
class AutoTuneSampleTimelineSnapshot:
    accepted_count: int
    rejected_count: int
    reset_count: int
    last_wire_timestamp_ms: int | None
    last_extended_timestamp_ms: int | None


class AutoTuneSampleTimeline:
    """Expand producer timestamps without substituting UI receipt time.

    The legacy pose mapping uses a uint32 Windows-uptime clock. Only differences
    matter to the tuner, so the first wire value can anchor an arbitrary local
    epoch. Subsequent values are expanded across rollover. Duplicate, backward,
    malformed, or out-of-range samples are rejected rather than retimed at the
    launcher's Qt callback instant.
    """

    def __init__(self) -> None:
        self._last_wire_timestamp_ms: int | None = None
        self._last_extended_timestamp_ms: int | None = None
        self._accepted_count = 0
        self._rejected_count = 0
        self._reset_count = 0

    @staticmethod
    def _wire_timestamp(value: object) -> int | None:
        if isinstance(value, bool) or not isinstance(value, numbers.Integral):
            return None
        parsed = int(value)
        return parsed if 0 <= parsed <= _UINT32_MASK else None

    def accept(self, timestamp_ms: object) -> float | None:
        """Return expanded producer seconds, or ``None`` for invalid ordering."""
        wire = self._wire_timestamp(timestamp_ms)
        if wire is None:
            self._rejected_count += 1
            return None
        expanded = expand_u32_timestamp(
            wire,
            self._last_wire_timestamp_ms,
            self._last_extended_timestamp_ms,
        )
        if expanded is None:
            self._rejected_count += 1
            return None
        self._last_wire_timestamp_ms = wire
        self._last_extended_timestamp_ms = expanded
        self._accepted_count += 1
        return expanded / 1000.0

    def reset(self) -> None:
        """Start a new producer episode while retaining lifetime counters."""
        self._last_wire_timestamp_ms = None
        self._last_extended_timestamp_ms = None
        self._reset_count += 1

    def snapshot(self) -> AutoTuneSampleTimelineSnapshot:
        return AutoTuneSampleTimelineSnapshot(
            accepted_count=self._accepted_count,
            rejected_count=self._rejected_count,
            reset_count=self._reset_count,
            last_wire_timestamp_ms=self._last_wire_timestamp_ms,
            last_extended_timestamp_ms=self._last_extended_timestamp_ms,
        )
