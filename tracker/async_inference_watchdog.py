"""Thread-safe health and backlog tracking for asynchronous inference."""
from __future__ import annotations

from dataclasses import dataclass
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
    consecutive_successful_callbacks: int
    callback_lag_ms: int
    callback_age_ms: int
    throttled_submission_count: int
    last_error: str


class AsyncInferenceWatchdog:
    """Detect async faults/stalls and prevent unbounded submission backlog.

    Timestamps use MediaPipe's expanded monotonic 64-bit millisecond timeline,
    so ordinary subtraction is valid and no uint32 wrap handling is needed here.
    ``callback_lag_ms`` describes accepted submissions versus callback progress.
    ``callback_age_ms`` can additionally advance against the current camera time,
    allowing a true callback stall to surface even while new inputs are being
    deliberately throttled.
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
        self._consecutive_successful_callbacks = 0
        self._throttled_submission_count = 0
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
            self._consecutive_successful_callbacks = 0
            self._throttled_submission_count = 0
            self._last_error = ""

    def record_submission(self, timestamp_ms: int) -> None:
        timestamp = self._timestamp(timestamp_ms)
        with self._lock:
            if self._first_submission_ms is None:
                self._first_submission_ms = timestamp
            self._last_submission_ms = timestamp
            self._consecutive_submission_errors = 0

    def record_throttled_submission(self) -> None:
        """Record a skipped input without treating it as an inference error."""
        with self._lock:
            self._throttled_submission_count += 1

    def record_submission_error(self, error: BaseException) -> None:
        with self._lock:
            self._consecutive_submission_errors += 1
            # A rejected submission breaks the candidate recovery streak even
            # when earlier callbacks were healthy.
            self._consecutive_successful_callbacks = 0
            self._last_error = self._error_text("submission", error)

    def record_callback(
        self,
        timestamp_ms: int,
        *,
        error: BaseException | None = None,
    ) -> None:
        timestamp = self._timestamp(timestamp_ms)
        with self._lock:
            progressed = (
                self._last_callback_ms is None
                or timestamp > self._last_callback_ms
            )
            if progressed:
                self._last_callback_ms = timestamp
            if error is None:
                self._consecutive_callback_errors = 0
                if progressed:
                    self._consecutive_successful_callbacks += 1
            else:
                self._consecutive_callback_errors += 1
                self._consecutive_successful_callbacks = 0
                self._last_error = self._error_text("callback", error)

    def should_submit(
        self,
        current_timestamp_ms: int,
        *,
        max_backlog_ms: int,
    ) -> bool:
        """Return whether another input should be prepared and submitted.

        A caught-up callback timeline always permits the next input, even after a
        long camera pause. While at least one submission remains unacknowledged,
        the prospective current-frame lag is bounded. This avoids paying image
        conversion/allocation costs for inputs that the live-stream task would
        likely discard while busy.
        """
        current = self._timestamp(current_timestamp_ms)
        limit = int(max_backlog_ms)
        if limit < 0:
            raise ValueError("max_backlog_ms cannot be negative")
        if limit == 0:
            return True
        with self._lock:
            first = self._first_submission_ms
            submitted = self._last_submission_ms
            callback = self._last_callback_ms
            if first is None or submitted is None:
                return True
            if callback is not None and callback >= submitted:
                return True
            anchor = callback if callback is not None else first
            prospective_lag = max(0, current - anchor)
            return prospective_lag < limit

    def snapshot(
        self,
        current_timestamp_ms: int | None = None,
    ) -> AsyncInferenceSnapshot:
        current = (
            None
            if current_timestamp_ms is None
            else self._timestamp(current_timestamp_ms)
        )
        with self._lock:
            submitted = self._last_submission_ms
            callback = self._last_callback_ms
            anchor = callback if callback is not None else self._first_submission_ms
            callback_lag = (
                0
                if anchor is None or submitted is None
                else max(0, submitted - anchor)
            )
            outstanding = submitted is not None and (
                callback is None or callback < submitted
            )
            age_endpoint = submitted if current is None else current
            callback_age = (
                0
                if not outstanding or anchor is None or age_endpoint is None
                else max(0, age_endpoint - anchor)
            )
            return AsyncInferenceSnapshot(
                first_submission_ms=self._first_submission_ms,
                last_submission_ms=submitted,
                last_callback_ms=callback,
                consecutive_submission_errors=(
                    self._consecutive_submission_errors
                ),
                consecutive_callback_errors=self._consecutive_callback_errors,
                consecutive_successful_callbacks=(
                    self._consecutive_successful_callbacks
                ),
                callback_lag_ms=callback_lag,
                callback_age_ms=callback_age,
                throttled_submission_count=(
                    self._throttled_submission_count
                ),
                last_error=self._last_error,
            )

    def raise_if_unhealthy(
        self,
        current_timestamp_ms: int | None = None,
    ) -> None:
        snapshot = self.snapshot(current_timestamp_ms)
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
        if snapshot.callback_age_ms >= self._stall_timeout_ms:
            raise AsyncInferenceFailure(
                "MediaPipe async callbacks stalled for at least "
                f"{snapshot.callback_age_ms} ms"
            )
