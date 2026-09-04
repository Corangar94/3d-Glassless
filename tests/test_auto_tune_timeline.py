from __future__ import annotations

import pytest

from launcher.auto_tune_timeline import AutoTuneSampleTimeline


def test_normal_producer_samples_preserve_elapsed_time():
    timeline = AutoTuneSampleTimeline()

    first = timeline.accept(10_000)
    second = timeline.accept(10_033)

    assert first == pytest.approx(10.0)
    assert second == pytest.approx(10.033)
    assert second - first == pytest.approx(0.033)
    snapshot = timeline.snapshot()
    assert snapshot.accepted_count == 2
    assert snapshot.rejected_count == 0


def test_uint32_rollover_expands_forward():
    timeline = AutoTuneSampleTimeline()

    first = timeline.accept(0xFFFF_FFF0)
    second = timeline.accept(0x0000_0010)

    assert first is not None
    assert second is not None
    assert second > first
    assert second - first == pytest.approx(0.032)
    assert timeline.snapshot().last_extended_timestamp_ms == (
        0xFFFF_FFF0 + 32
    )


def test_zero_is_a_valid_first_uptime_value():
    timeline = AutoTuneSampleTimeline()

    assert timeline.accept(0) == 0.0
    assert timeline.accept(33) == pytest.approx(0.033)


def test_duplicate_and_backward_samples_are_not_retimed():
    timeline = AutoTuneSampleTimeline()
    assert timeline.accept(1000) == 1.0

    assert timeline.accept(1000) is None
    assert timeline.accept(999) is None
    assert timeline.accept(1033) == pytest.approx(1.033)

    snapshot = timeline.snapshot()
    assert snapshot.accepted_count == 2
    assert snapshot.rejected_count == 2
    assert snapshot.last_wire_timestamp_ms == 1033


@pytest.mark.parametrize(
    "value",
    [True, False, -1, 0x1_0000_0000, 1.5, "1000", None, object()],
)
def test_malformed_or_out_of_range_wire_values_fail_closed(value):
    timeline = AutoTuneSampleTimeline()

    assert timeline.accept(value) is None
    assert timeline.snapshot().rejected_count == 1


def test_reset_starts_a_new_clock_episode_without_erasing_counters():
    timeline = AutoTuneSampleTimeline()
    timeline.accept(50_000)
    timeline.accept(50_033)

    timeline.reset()

    # A restarted child can begin with a lower uptime modulo value. It becomes a
    # new anchor only because the owner explicitly reset at a tracking boundary.
    assert timeline.accept(100) == pytest.approx(0.1)
    snapshot = timeline.snapshot()
    assert snapshot.accepted_count == 3
    assert snapshot.rejected_count == 0
    assert snapshot.reset_count == 1
    assert snapshot.last_wire_timestamp_ms == 100
    assert snapshot.last_extended_timestamp_ms == 100
