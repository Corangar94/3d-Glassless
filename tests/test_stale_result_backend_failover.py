from __future__ import annotations

import pytest

from tracker.async_result_freshness import AsyncResultFreshnessGate
from tracker.backend_failover import (
    AutoFailoverFaceTracker,
    BackendFailoverPolicy,
)
from tracker.backend_transition_state import (
    reset_backend_transition_generation,
)
from tracker.pose import HeadPosition


class StalePrimary:
    def __init__(self) -> None:
        self._freshness = AsyncResultFreshnessGate()
        self.calls: list[int] = []
        self.close_count = 0

    def process_frame(self, _frame, capture_timestamp_ms=None):
        timestamp = int(capture_timestamp_ms)
        self.calls.append(timestamp)
        result_timestamp = timestamp - 300
        if not self._freshness.accept_result(
            result_timestamp,
            timestamp,
        ):
            return None
        return HeadPosition(
            x_cm=1.0,
            y_cm=2.0,
            z_cm=60.0,
            capture_timestamp_ms=result_timestamp,
        )

    def close(self) -> None:
        self.close_count += 1


class Fallback:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.close_count = 0

    def process_frame(self, _frame, capture_timestamp_ms=None):
        timestamp = int(capture_timestamp_ms)
        self.calls.append(timestamp)
        return HeadPosition(
            x_cm=5.0,
            y_cm=-1.0,
            z_cm=65.0,
            confidence=0.6,
            capture_timestamp_ms=timestamp,
        )

    def close(self) -> None:
        self.close_count += 1


@pytest.fixture(autouse=True)
def _clean_transition_state():
    reset_backend_transition_generation()
    yield
    reset_backend_transition_generation()


def test_third_stale_primary_result_switches_to_fallback_same_frame():
    primary = StalePrimary()
    fallback = Fallback()
    tracker = AutoFailoverFaceTracker(
        primary_factory=lambda: primary,
        fallback_factory=lambda: fallback,
        policy=BackendFailoverPolicy(max_primary_retries=0),
        logger=lambda _message: None,
    )

    assert tracker.process_frame(object(), 1300) is None
    assert tracker.process_frame(object(), 1333) is None
    recovered = tracker.process_frame(object(), 1366)

    assert recovered is not None
    assert recovered.xyz == (5.0, -1.0, 65.0)
    assert recovered.capture_timestamp_ms == 1366
    assert tracker.active_backend == "cv2"
    assert primary.calls == [1300, 1333, 1366]
    assert primary.close_count == 1
    assert fallback.calls == [1366]
    snapshot = tracker.snapshot(1366)
    assert snapshot.failover_count == 1
    assert "stale pose results" in snapshot.last_failure


def test_isolated_stale_result_does_not_switch_backend():
    primary = StalePrimary()
    fallback = Fallback()
    tracker = AutoFailoverFaceTracker(
        primary_factory=lambda: primary,
        fallback_factory=lambda: fallback,
        policy=BackendFailoverPolicy(max_primary_retries=0),
        logger=lambda _message: None,
    )

    assert tracker.process_frame(object(), 1300) is None

    assert tracker.active_backend == "mediapipe"
    assert primary.close_count == 0
    assert fallback.calls == []
