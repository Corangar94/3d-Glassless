from __future__ import annotations

import pytest

from tracker.async_inference_watchdog import AsyncInferenceFailure
from tracker.async_result_freshness import (
    AsyncResultFreshnessGate,
    AsyncResultFreshnessPolicy,
)


def test_pose_at_exact_age_limit_is_accepted():
    gate = AsyncResultFreshnessGate(
        AsyncResultFreshnessPolicy(max_result_age_ms=250)
    )

    assert gate.accept_result(1000, 1250)
    assert gate.snapshot().total_stale_results == 0


def test_first_two_stale_poses_are_dropped_then_third_escalates():
    gate = AsyncResultFreshnessGate(
        AsyncResultFreshnessPolicy(
            max_result_age_ms=250,
            max_consecutive_stale_results=3,
            stale_result_window_ms=1000,
        )
    )

    assert not gate.accept_result(1000, 1300)
    assert not gate.accept_result(1033, 1333)
    with pytest.raises(
        AsyncInferenceFailure,
        match="3 stale pose results",
    ):
        gate.accept_result(1066, 1366)

    snapshot = gate.snapshot()
    assert snapshot.total_stale_results == 3
    assert snapshot.consecutive_stale_results == 3
    assert snapshot.last_stale_age_ms == 300
    assert snapshot.last_stale_result_timestamp_ms == 1066


def test_fresh_pose_breaks_stale_burst():
    gate = AsyncResultFreshnessGate()
    assert not gate.accept_result(1000, 1300)
    assert not gate.accept_result(1033, 1333)

    assert gate.accept_result(1300, 1400)
    assert gate.snapshot().consecutive_stale_results == 0

    assert not gate.accept_result(1400, 1700)
    assert gate.snapshot().consecutive_stale_results == 1


def test_no_face_callback_breaks_stale_burst():
    gate = AsyncResultFreshnessGate()
    assert not gate.accept_result(1000, 1300)
    assert not gate.accept_result(1033, 1333)

    gate.record_result_without_pose()

    assert gate.snapshot().consecutive_stale_results == 0
    assert not gate.accept_result(1066, 1366)
    assert gate.snapshot().consecutive_stale_results == 1


def test_stale_poses_outside_window_start_new_episode():
    gate = AsyncResultFreshnessGate(
        AsyncResultFreshnessPolicy(
            max_result_age_ms=250,
            max_consecutive_stale_results=3,
            stale_result_window_ms=1000,
        )
    )
    assert not gate.accept_result(1000, 1300)
    assert not gate.accept_result(1033, 1333)

    assert not gate.accept_result(2100, 2401)

    snapshot = gate.snapshot()
    assert snapshot.total_stale_results == 3
    assert snapshot.consecutive_stale_results == 1
    assert snapshot.stale_burst_started_ms == 2401


def test_window_boundary_is_inclusive():
    gate = AsyncResultFreshnessGate(
        AsyncResultFreshnessPolicy(
            max_result_age_ms=250,
            max_consecutive_stale_results=2,
            stale_result_window_ms=1000,
        )
    )
    assert not gate.accept_result(1000, 1300)

    with pytest.raises(AsyncInferenceFailure):
        gate.accept_result(2000, 2300)


def test_future_timestamp_is_not_misclassified_as_ancient():
    gate = AsyncResultFreshnessGate()
    assert not gate.accept_result(1000, 1300)

    assert gate.accept_result(1400, 1350)

    assert gate.snapshot().consecutive_stale_results == 0


def test_result_age_and_burst_window_are_wrap_safe():
    gate = AsyncResultFreshnessGate(
        AsyncResultFreshnessPolicy(
            max_result_age_ms=40,
            max_consecutive_stale_results=2,
            stale_result_window_ms=1000,
        )
    )

    assert not gate.accept_result(0xFFFF_FFE0, 0x20)  # 64 ms old
    with pytest.raises(AsyncInferenceFailure):
        gate.accept_result(0xFFFF_FFF0, 0x40)  # 80 ms old

    snapshot = gate.snapshot()
    assert snapshot.consecutive_stale_results == 2
    assert snapshot.last_stale_age_ms == 80


def test_missing_timestamp_retains_legacy_delivery_and_resets_burst():
    gate = AsyncResultFreshnessGate()
    assert not gate.accept_result(1000, 1300)

    assert gate.accept_result(0, 2000)

    assert gate.snapshot().consecutive_stale_results == 0


def test_zero_age_limit_disables_drop_and_escalation():
    gate = AsyncResultFreshnessGate(
        AsyncResultFreshnessPolicy(max_result_age_ms=0)
    )

    assert gate.accept_result(1000, 100_000)
    assert gate.snapshot().total_stale_results == 0


def test_zero_stale_threshold_drops_without_escalating():
    gate = AsyncResultFreshnessGate(
        AsyncResultFreshnessPolicy(
            max_result_age_ms=250,
            max_consecutive_stale_results=0,
        )
    )

    for index in range(20):
        assert not gate.accept_result(1000 + index, 1300 + index)

    snapshot = gate.snapshot()
    assert snapshot.total_stale_results == 20
    assert snapshot.consecutive_stale_results == 20


def test_reset_clears_session_counters_and_burst():
    gate = AsyncResultFreshnessGate()
    assert not gate.accept_result(1000, 1300)

    gate.reset()

    snapshot = gate.snapshot()
    assert snapshot.total_stale_results == 0
    assert snapshot.consecutive_stale_results == 0
    assert snapshot.stale_burst_started_ms is None
    assert snapshot.last_stale_observed_ms is None
    assert snapshot.last_stale_result_timestamp_ms is None
    assert snapshot.last_stale_age_ms is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_result_age_ms": -1},
        {"max_consecutive_stale_results": -1},
        {"stale_result_window_ms": 0},
    ],
)
def test_invalid_policy_fails_closed(kwargs):
    with pytest.raises(ValueError):
        AsyncResultFreshnessPolicy(**kwargs)
