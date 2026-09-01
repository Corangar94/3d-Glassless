"""Monotonic publication control for asynchronous tracker callbacks."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AsyncCallbackOrderSnapshot:
    latest_published_timestamp_ms: int | None
    accepted_publication_count: int
    duplicate_drop_count: int
    out_of_order_drop_count: int
    pre_conversion_drop_count: int
    post_conversion_drop_count: int
    last_dropped_timestamp_ms: int | None

    @property
    def total_drop_count(self) -> int:
        return self.duplicate_drop_count + self.out_of_order_drop_count


class LatestCallbackPublicationGate:
    """Keep asynchronous result publication latest-only.

    MediaPipe uses an expanded, monotonically increasing millisecond timeline,
    so ordinary integer comparison is correct here. The owner must serialize
    calls with the same lock used to publish its latest result; this makes the
    final ordering check and result assignment one atomic operation.
    """

    def __init__(self) -> None:
        self._latest_published_timestamp_ms: int | None = None
        self._accepted_publication_count = 0
        self._duplicate_drop_count = 0
        self._out_of_order_drop_count = 0
        self._pre_conversion_drop_count = 0
        self._post_conversion_drop_count = 0
        self._last_dropped_timestamp_ms: int | None = None

    @staticmethod
    def _timestamp(value: int) -> int:
        timestamp = int(value)
        if timestamp < 0:
            raise ValueError("callback timestamps cannot be negative")
        return timestamp

    def is_newer(self, timestamp_ms: int) -> bool:
        timestamp = self._timestamp(timestamp_ms)
        latest = self._latest_published_timestamp_ms
        return latest is None or timestamp > latest

    def _record_drop(self, timestamp_ms: int, *, after_conversion: bool) -> None:
        timestamp = self._timestamp(timestamp_ms)
        latest = self._latest_published_timestamp_ms
        if latest is None or timestamp > latest:
            raise ValueError("cannot record a drop for a newer callback")
        if timestamp == latest:
            self._duplicate_drop_count += 1
        else:
            self._out_of_order_drop_count += 1
        if after_conversion:
            self._post_conversion_drop_count += 1
        else:
            self._pre_conversion_drop_count += 1
        self._last_dropped_timestamp_ms = timestamp

    def begin_processing(self, timestamp_ms: int) -> bool:
        """Reject callbacks already obsolete before expensive pose conversion."""
        if self.is_newer(timestamp_ms):
            return True
        self._record_drop(timestamp_ms, after_conversion=False)
        return False

    def accept_publication(self, timestamp_ms: int) -> bool:
        """Atomically claim publication after conversion completes.

        A callback can pass ``begin_processing`` and still become obsolete while
        it is converting if a newer callback finishes first. This second check
        closes that race.
        """
        timestamp = self._timestamp(timestamp_ms)
        if not self.is_newer(timestamp):
            self._record_drop(timestamp, after_conversion=True)
            return False
        self._latest_published_timestamp_ms = timestamp
        self._accepted_publication_count += 1
        return True

    def snapshot(self) -> AsyncCallbackOrderSnapshot:
        return AsyncCallbackOrderSnapshot(
            latest_published_timestamp_ms=(
                self._latest_published_timestamp_ms
            ),
            accepted_publication_count=self._accepted_publication_count,
            duplicate_drop_count=self._duplicate_drop_count,
            out_of_order_drop_count=self._out_of_order_drop_count,
            pre_conversion_drop_count=self._pre_conversion_drop_count,
            post_conversion_drop_count=self._post_conversion_drop_count,
            last_dropped_timestamp_ms=self._last_dropped_timestamp_ms,
        )
