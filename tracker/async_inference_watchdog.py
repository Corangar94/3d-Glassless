"""Thread-safe health tracking for asynchronous inference callbacks."""
from __future__ import annotations

from dataclasses import dataclass
import math
import threading


class AsyncInferenceFailure(RuntimeError):
    """Asynchronous inference is no longer making trustworthy progress."""


@dataclass(frozen=True)
class AsyncInferenceSnapshot:
    first_submission_ms: int | None
    last_submission_ms: int | None
    last_callback_ms: int | None
    consecutive_submission_errors: int
    consecutive_callback_errors: int
    callback_lag_ms: int
    last_error: str


class AsyncInferenceWatchdog:
    """Detect repeated submission faults, callback faults, and callback stalls.

    Timestamps are MediaPipe's expanded monotonic 64-bit millisecond timeline,
    so ordinary subtraction is valid and no uint32 wrap handling is needed here.
    """

    def __init__(
        self,
        *,
        max_consecutive_errors: int = 3,
        stall_timeout_ms: int = 5000,
    ) -> None:
        if max_consecutive_errors < 1:
            raise ValueError("max_consecutive_errors must be at least one")
        if stall_timeout_ms < 1:
            raise ValueError("stall_timeout_ms must be positive")
        self._max_consecutive_errors = int(max_consecutive_errors)
        self._stall_timeout_ms = int(stall_timeout_ms)
        self._lock = threading.Lock()
        self._first_submission_ms: int | None = None
        self._last_submission_ms: int | None = None
        self._last_callback_ms: int | None = None
        self._consecutive_submission_errors = 0
        self._consecutive_callback_errors = 0
        self._last_error = ""

    @property
    def max_consecutive_errors(self) -> int:
        return self._max_consecutive_errors

    @property
    def stall_timeout_ms(self) -> int:
        return self._stall_timeout_ms

    @staticmethod
    def _timestamp(value: int) -> int:
        parsed = int(value)
        if parsed < 0:
            raise ValueError("async inference timestamps cannot be negative")
        return parsed

    @staticmethod
    def _error_text(stage: str, error: BaseException) -> str:
        return f"{stage}:{type(error).__name__}"

    def reset_session(self) -> None:
        """Start a fresh callback-health episode after camera reconnection."""
        with self._lock:
            self._first_submission_ms = None
            self._last_submission_ms = None
            self._last_callback_ms = None
            self._consecutive_submission_errors = 0
            self._consecutive_callback_errors = 0
            self._last_error = ""

    def record_submission(self, timestamp_ms: int) -> None:
        timestamp = self._timestamp(timestamp_ms)
        with self._lock:
            if self._first_submission_ms is None:
                self._first_submission_ms = timestamp
            self._last_submission_ms = timestamp
            self._consecutive_submission_errors = 0

    def record_submission_error(self, error: BaseException) -> None:
        with self._lock:
            self._consecutive_submission_errors += 1
            self._last_error = self._error_text("submission", error)

    def record_callback(
        self,
        timestamp_ms: int,
        *,
        error: BaseException | None = None,
    ) -> None:
        timestamp = self._timestamp(timestamp_ms)
        with self._lock:
            if (
                self._last_callback_ms is None
                or timestamp > self._last_callback_ms
            ):
                self._last_callback_ms = timestamp
            if error is None:
                self._consecutive_callback_errors = 0
            else:
                self._consecutive_callback_errors += 1
                self._last_error = self._error_text("callback", error)

    def snapshot(self) -> AsyncInferenceSnapshot:
        with self._lock:
            anchor = (
                self._last_callback_ms
                if self._last_callback_ms is not None
                else self._first_submission_ms
            )
            callback_lag = (
                0
                if anchor is None or self._last_submission_ms is None
                else max(0, self._last_submission_ms - anchor)
            )
            return AsyncInferenceSnapshot(
                first_submission_ms=self._first_submission_ms,
                last_submission_ms=self._last_submission_ms,
                last_callback_ms=self._last_callback_ms,
                consecutive_submission_errors=(
                    self._consecutive_submission_errors
                ),
                consecutive_callback_errors=self._consecutive_callback_errors,
                callback_lag_ms=callback_lag,
                last_error=self._last_error,
            )

    def raise_if_unhealthy(self) -> None:
        snapshot = self.snapshot()
        if (
            snapshot.consecutive_submission_errors
            >= self._max_consecutive_errors
        ):
            raise AsyncInferenceFailure(
                "MediaPipe async submission failed "
                f"{snapshot.consecutive_submission_errors} consecutive times "
                f"({snapshot.last_error})"
            )
        if (
            snapshot.consecutive_callback_errors
            >= self._max_consecutive_errors
        ):
            raise AsyncInferenceFailure(
                "MediaPipe async callback processing failed "
                f"{snapshot.consecutive_callback_errors} consecutive times "
                f"({snapshot.last_error})"
            )
        if snapshot.callback_lag_ms >= self._stall_timeout_ms:
            raise AsyncInferenceFailure(
                "MediaPipe async callbacks stalled for at least "
                f"{snapshot.callback_lag_ms} ms"
            )
