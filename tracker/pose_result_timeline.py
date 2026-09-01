"""Drop duplicate and out-of-order pose results before tracking refresh."""
from __future__ import annotations

from dataclasses import dataclass
import inspect
import math
import numbers
from typing import Any

from tracker.backend_transition_state import (
    current_backend_transition_generation,
)
from tracker.pose import elapsed_u32_ms, normalize_wire_timestamp


_UINT32_HALF_RANGE = 0x8000_0000
_MISSING = object()


@dataclass(frozen=True)
class PoseResultTimelineSnapshot:
    last_timestamp_ms: int | None
    accepted_timestamped_count: int
    accepted_untimestamped_count: int
    duplicate_count: int
    out_of_order_count: int
    malformed_timestamp_count: int
    backend_transition_generation: int
    last_rejection_reason: str

    @property
    def rejected_count(self) -> int:
        return (
            self.duplicate_count
            + self.out_of_order_count
            + self.malformed_timestamp_count
        )


class PoseResultTimelineGate:
    """Keep a result stream monotonic without constraining legacy objects.

    A result without an explicitly declared ``capture_timestamp_ms`` (or with
    the historical value zero) is passed through without becoming the timestamp
    anchor. Timestamped results must advance on the project's wrap-safe uint32
    wire clock.

    Automatic MediaPipe/OpenCV backend transitions start a new result timeline.
    The backend controller advances its transition generation during the same
    ``process_frame`` call, so checking the generation after that call prevents
    one backend's private result ordering from rejecting the replacement.
    """

    def __init__(self) -> None:
        self._last_timestamp_ms: int | None = None
        self._accepted_timestamped_count = 0
        self._accepted_untimestamped_count = 0
        self._duplicate_count = 0
        self._out_of_order_count = 0
        self._malformed_timestamp_count = 0
        self._backend_transition_generation = (
            current_backend_transition_generation()
        )
        self._last_rejection_reason = ""

    def reset(self) -> None:
        """Forget only the ordering anchor; retain lifetime diagnostics."""
        self._last_timestamp_ms = None
        self._last_rejection_reason = ""
        self._backend_transition_generation = (
            current_backend_transition_generation()
        )

    def _synchronize_backend_transition(self) -> None:
        generation = current_backend_transition_generation()
        if generation == self._backend_transition_generation:
            return
        self._last_timestamp_ms = None
        self._last_rejection_reason = ""
        self._backend_transition_generation = generation

    @staticmethod
    def _timestamp_from_result(result: object) -> tuple[int | None, bool]:
        """Return (timestamp, malformed).

        Static attribute discovery avoids treating dynamic mocks or broad
        ``__getattr__`` wrappers as timestamped pose objects merely because they
        can manufacture an attribute on demand. Declared properties and slots
        are then read normally, with getter failures contained as malformed.
        """
        try:
            declared = inspect.getattr_static(
                result,
                "capture_timestamp_ms",
                _MISSING,
            )
        except Exception:
            return None, True
        if declared is _MISSING:
            return None, False
        try:
            raw = getattr(result, "capture_timestamp_ms")
        except Exception:
            return None, True
        if raw is None:
            return None, False
        if isinstance(raw, bool):
            return None, True
        if isinstance(raw, numbers.Real) and not isinstance(
            raw,
            numbers.Integral,
        ):
            real = float(raw)
            if not math.isfinite(real) or not real.is_integer():
                return None, True
        try:
            parsed = int(raw)
        except (TypeError, ValueError, OverflowError):
            return None, True
        if parsed < 0:
            return None, True
        if parsed == 0:
            return None, False
        return normalize_wire_timestamp(parsed), False

    def filter(self, result: Any) -> Any:
        """Return an accepted result, or ``None`` for a rejected pose result."""
        self._synchronize_backend_transition()
        if result is None:
            return None
        timestamp, malformed = self._timestamp_from_result(result)
        if malformed:
            self._malformed_timestamp_count += 1
            self._last_rejection_reason = "malformed capture timestamp"
            return None
        if timestamp is None:
            self._accepted_untimestamped_count += 1
            return result

        previous = self._last_timestamp_ms
        if previous is None:
            self._last_timestamp_ms = timestamp
            self._accepted_timestamped_count += 1
            self._last_rejection_reason = ""
            return result

        elapsed = elapsed_u32_ms(timestamp, previous)
        if elapsed == 0:
            self._duplicate_count += 1
            self._last_rejection_reason = "duplicate capture timestamp"
            return None
        if elapsed >= _UINT32_HALF_RANGE:
            self._out_of_order_count += 1
            self._last_rejection_reason = "out-of-order capture timestamp"
            return None

        self._last_timestamp_ms = timestamp
        self._accepted_timestamped_count += 1
        self._last_rejection_reason = ""
        return result

    def snapshot(self) -> PoseResultTimelineSnapshot:
        return PoseResultTimelineSnapshot(
            last_timestamp_ms=self._last_timestamp_ms,
            accepted_timestamped_count=self._accepted_timestamped_count,
            accepted_untimestamped_count=self._accepted_untimestamped_count,
            duplicate_count=self._duplicate_count,
            out_of_order_count=self._out_of_order_count,
            malformed_timestamp_count=self._malformed_timestamp_count,
            backend_transition_generation=(
                self._backend_transition_generation
            ),
            last_rejection_reason=self._last_rejection_reason,
        )
