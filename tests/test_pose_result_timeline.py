from __future__ import annotations

from dataclasses import dataclass

import pytest

from tracker.backend_transition_state import (
    mark_backend_transition,
    reset_backend_transition_generation,
)
from tracker.pose import HeadPosition
from tracker.pose_result_timeline import PoseResultTimelineGate


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


def test_timestamped_results_must_advance():
    gate = PoseResultTimelineGate()
    first = _pose(1000)
    newer = _pose(1033)

    assert gate.filter(first) is first
    assert gate.filter(newer) is newer

    snapshot = gate.snapshot()
    assert snapshot.last_timestamp_ms == 1033
    assert snapshot.accepted_timestamped_count == 2
    assert snapshot.rejected_count == 0


def test_duplicate_result_is_dropped_without_moving_anchor():
    gate = PoseResultTimelineGate()
    accepted = _pose(1000, yaw_deg=5.0)
    duplicate = _pose(1000, yaw_deg=95.0)

    assert gate.filter(accepted) is accepted
    assert gate.filter(duplicate) is None

    snapshot = gate.snapshot()
    assert snapshot.last_timestamp_ms == 1000
    assert snapshot.duplicate_count == 1
    assert snapshot.out_of_order_count == 0
    assert snapshot.last_rejection_reason == "duplicate capture timestamp"


def test_out_of_order_result_is_dropped_and_next_newer_result_recovers():
    gate = PoseResultTimelineGate()
    gate.filter(_pose(1000))
    gate.filter(_pose(1033))

    assert gate.filter(_pose(1016, yaw_deg=120.0)) is None
    recovered = _pose(1066, yaw_deg=4.0)
    assert gate.filter(recovered) is recovered

    snapshot = gate.snapshot()
    assert snapshot.last_timestamp_ms == 1066
    assert snapshot.out_of_order_count == 1
    assert snapshot.accepted_timestamped_count == 3
    assert snapshot.last_rejection_reason == ""


def test_wire_timestamp_rollover_is_monotonic():
    gate = PoseResultTimelineGate()
    before = _pose(0xFFFF_FFF0)
    after = _pose(0x20)

    assert gate.filter(before) is before
    assert gate.filter(after) is after
    assert gate.snapshot().last_timestamp_ms == 0x20


def test_exact_half_range_is_rejected_as_ambiguous_old_data():
    gate = PoseResultTimelineGate()
    gate.filter(_pose(1))

    assert gate.filter(_pose(0x8000_0001)) is None
    assert gate.snapshot().out_of_order_count == 1


def test_missing_and_zero_legacy_timestamps_pass_without_becoming_anchor():
    gate = PoseResultTimelineGate()

    class OpaqueResult:
        pass

    opaque = OpaqueResult()
    zero = _pose(0)
    first_timestamped = _pose(1000)

    assert gate.filter(opaque) is opaque
    assert gate.filter(zero) is zero
    assert gate.snapshot().last_timestamp_ms is None
    assert gate.filter(first_timestamped) is first_timestamped

    snapshot = gate.snapshot()
    assert snapshot.accepted_untimestamped_count == 2
    assert snapshot.accepted_timestamped_count == 1


@dataclass
class _Result:
    capture_timestamp_ms: object


@pytest.mark.parametrize(
    "value",
    [
        True,
        1.5,
        float("nan"),
        float("inf"),
        "not-a-timestamp",
        object(),
    ],
)
def test_malformed_timestamped_result_is_dropped(value):
    gate = PoseResultTimelineGate()

    assert gate.filter(_Result(value)) is None

    snapshot = gate.snapshot()
    assert snapshot.malformed_timestamp_count == 1
    assert snapshot.last_timestamp_ms is None
    assert snapshot.last_rejection_reason == "malformed capture timestamp"


def test_property_error_is_contained_as_malformed_timestamp():
    gate = PoseResultTimelineGate()

    class BrokenResult:
        @property
        def capture_timestamp_ms(self):
            raise RuntimeError("bad property")

    assert gate.filter(BrokenResult()) is None
    assert gate.snapshot().malformed_timestamp_count == 1


def test_backend_transition_starts_a_new_result_timeline():
    gate = PoseResultTimelineGate()
    gate.filter(_pose(5000))
    generation = mark_backend_transition(preserve_position=True)
    replacement = _pose(1000)

    assert gate.filter(replacement) is replacement

    snapshot = gate.snapshot()
    assert snapshot.last_timestamp_ms == 1000
    assert snapshot.backend_transition_generation == generation
    assert snapshot.out_of_order_count == 0


def test_reset_forgets_anchor_but_preserves_lifetime_counts():
    gate = PoseResultTimelineGate()
    gate.filter(_pose(1000))
    gate.filter(_pose(1000))

    gate.reset()
    replacement = _pose(100)

    assert gate.filter(replacement) is replacement
    snapshot = gate.snapshot()
    assert snapshot.last_timestamp_ms == 100
    assert snapshot.duplicate_count == 1
    assert snapshot.accepted_timestamped_count == 2
