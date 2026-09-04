"""Emit launcher tracker status only when its observable state changes."""
from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Callable


StatusEmitter = Callable[[str], object]


@dataclass(frozen=True)
class StatusEmissionDecision:
    """Outcome of one requested status publication."""

    emitted: bool
    status: str
    reason: str
    forced: bool = False
    fail_open: bool = False


@dataclass(frozen=True)
class StatusEmissionSnapshot:
    """Lifetime counters plus the current deduplication baseline."""

    emitted_count: int
    suppressed_count: int
    forced_count: int
    fail_open_count: int
    failed_emission_count: int
    reset_count: int
    last_status: str | None
    last_decision_reason: str


def _status_text(value: object) -> tuple[str, bool]:
    """Return text plus whether it is a valid deduplication baseline."""
    if isinstance(value, str) and bool(value):
        return value, True
    try:
        return str(value), False
    except Exception:
        return "<invalid tracker status>", False


class StatusEmissionGate:
    """Serialize status transitions and suppress exact consecutive duplicates.

    The baseline is provisionally installed before calling the emitter. This
    prevents a direct/re-entrant signal handler from recursively publishing the
    same status. If emission raises and no nested transition replaced it, the
    previous baseline is restored so a later call can retry the transition.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._generation = 0
        self._last_status: str | None = None
        self._emitted_count = 0
        self._suppressed_count = 0
        self._forced_count = 0
        self._fail_open_count = 0
        self._failed_emission_count = 0
        self._reset_count = 0
        self._last_decision_reason = ""

    def emit(
        self,
        status: object,
        emitter: StatusEmitter,
        *,
        force: bool = False,
    ) -> StatusEmissionDecision:
        if not callable(emitter):
            raise TypeError("status emitter must be callable")
        if not isinstance(force, bool):
            raise TypeError("force must be a boolean")

        text, valid = _status_text(status)
        with self._lock:
            if not valid:
                try:
                    emitter(text)
                except Exception:
                    self._failed_emission_count += 1
                    self._last_decision_reason = (
                        "invalid status emission failed"
                    )
                    raise
                self._emitted_count += 1
                self._fail_open_count += 1
                self._last_decision_reason = (
                    "invalid status emitted fail-open without baseline"
                )
                return StatusEmissionDecision(
                    emitted=True,
                    status=text,
                    reason=self._last_decision_reason,
                    fail_open=True,
                )

            if not force and text == self._last_status:
                self._suppressed_count += 1
                self._last_decision_reason = (
                    "consecutive duplicate status suppressed"
                )
                return StatusEmissionDecision(
                    emitted=False,
                    status=text,
                    reason=self._last_decision_reason,
                )

            previous_status = self._last_status
            self._generation += 1
            token = self._generation
            self._last_status = text
            try:
                emitter(text)
            except Exception:
                self._failed_emission_count += 1
                if self._generation == token and self._last_status == text:
                    self._last_status = previous_status
                    self._generation += 1
                self._last_decision_reason = (
                    "status emission failed; transition baseline rolled back"
                )
                raise

            self._emitted_count += 1
            self._forced_count += int(force)
            self._last_decision_reason = (
                "status force-emitted"
                if force
                else "status transition emitted"
            )
            return StatusEmissionDecision(
                emitted=True,
                status=text,
                reason=self._last_decision_reason,
                forced=force,
            )

    def reset(self) -> None:
        """Start a new lifecycle episode while retaining lifetime counters."""
        with self._lock:
            self._generation += 1
            self._last_status = None
            self._reset_count += 1
            self._last_decision_reason = "status emission episode reset"

    def snapshot(self) -> StatusEmissionSnapshot:
        with self._lock:
            return StatusEmissionSnapshot(
                emitted_count=self._emitted_count,
                suppressed_count=self._suppressed_count,
                forced_count=self._forced_count,
                fail_open_count=self._fail_open_count,
                failed_emission_count=self._failed_emission_count,
                reset_count=self._reset_count,
                last_status=self._last_status,
                last_decision_reason=self._last_decision_reason,
            )
