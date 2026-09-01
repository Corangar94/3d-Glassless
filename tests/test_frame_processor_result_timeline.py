from __future__ import annotations

from collections import deque

import pytest

from tracker.backend_transition_state import (
    mark_backend_transition,
    reset_backend_transition_generation,
)
from tracker.frame_processor import FrameProcessorAdapter
from tracker.pose import HeadPosition


@pytest.fixture(autouse=True)
def _clean_backend_transition_generation():
    reset_backend_transition_generation()
    yield
    reset_backend_transition_generation()


def _pose(timestamp_ms: int, *, yaw_deg: float = 0.0) -> HeadPosition:
    return HeadPosition(
        x_cm=1.0,
        y_cm=2.0,
        z_cm=60.0,
        yaw_deg=yaw_deg,
        confidence=0.9,
        capture_timestamp_ms=timestamp_ms,
    )


def _adapter(monkeypatch, *results):
    values = deque(results)

    class Tracker:
        def process_frame(self, _frame, capture_timestamp_ms=None):
            return values.popleft()

    monkeypatch.setattr(
        "tracker.frame_processor.make_tracker_backend_status_publisher",
        lambda _tracker: None,
    )
    return FrameProcessorAdapter.from_tracker(Tracker())


def test_adapter_returns_none_for_duplicate_and_out_of_order_poses(monkeypatch):
    first = _pose(1000)
    duplicate = _pose(1000, yaw_deg=90.0)
    older = _pose(999, yaw_deg=-90.0)
    newer = _pose(1033)
    adapter = _adapter(monkeypatch, first, duplicate, older, newer)

    assert adapter(object(), 2000) is first
    assert adapter(object(), 2033) is None
    assert adapter(object(), 2066) is None
    assert adapter(object(), 2099) is newer

    snapshot = adapter.result_timeline_snapshot()
    assert snapshot.duplicate_count == 1
    assert snapshot.out_of_order_count == 1
    assert snapshot.last_timestamp_ms == 1033


def test_adapter_preserves_opaque_and_legacy_untimestamped_results(monkeypatch):
    opaque = ("legacy pose", 1)
    zero_timestamp = _pose(0)
    adapter = _adapter(monkeypatch, opaque, zero_timestamp)

    assert adapter(object(), 1000) is opaque
    assert adapter(object(), 1033) is zero_timestamp

    snapshot = adapter.result_timeline_snapshot()
    assert snapshot.accepted_untimestamped_count == 2
    assert snapshot.last_timestamp_ms is None


def test_adapter_resets_ordering_anchor_after_backend_transition(monkeypatch):
    old_backend = _pose(5000)
    new_backend = _pose(1000)
    adapter = _adapter(monkeypatch, old_backend, new_backend)

    assert adapter(object(), 5000) is old_backend
    mark_backend_transition(preserve_position=True)
    assert adapter(object(), 5033) is new_backend

    snapshot = adapter.result_timeline_snapshot()
    assert snapshot.out_of_order_count == 0
    assert snapshot.last_timestamp_ms == 1000


def test_malformed_timestamp_result_is_dropped_before_downstream_use(monkeypatch):
    class BrokenPose:
        capture_timestamp_ms = "bad"
        x_cm = 1.0
        y_cm = 2.0
        z_cm = 60.0

    adapter = _adapter(monkeypatch, BrokenPose())

    assert adapter(object(), 1000) is None
    assert adapter.result_timeline_snapshot().malformed_timestamp_count == 1


def test_backend_status_is_still_published_after_timeline_rejection(monkeypatch):
    class Publisher:
        def __init__(self) -> None:
            self.timestamps: list[int] = []

        def publish(self, timestamp_ms: int) -> None:
            self.timestamps.append(timestamp_ms)

        def close(self) -> None:
            pass

    publisher = Publisher()
    values = deque((_pose(1000), _pose(1000)))

    class Tracker:
        def process_frame(self, _frame, capture_timestamp_ms=None):
            return values.popleft()

    monkeypatch.setattr(
        "tracker.frame_processor.make_tracker_backend_status_publisher",
        lambda _tracker: publisher,
    )
    monkeypatch.setattr(
        "tracker.frame_processor.monotonic_ms",
        lambda: 99,
    )
    adapter = FrameProcessorAdapter.from_tracker(Tracker())

    assert adapter(object(), 2000) is not None
    assert adapter(object(), 2033) is None
    assert publisher.timestamps == [99, 2000, 2033]


def test_manual_timeline_reset_accepts_a_lower_timestamp(monkeypatch):
    first = _pose(5000)
    replacement = _pose(1000)
    adapter = _adapter(monkeypatch, first, replacement)

    assert adapter(object(), 5000) is first
    adapter.reset_result_timeline()
    assert adapter(object(), 6000) is replacement
