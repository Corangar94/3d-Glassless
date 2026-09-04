from __future__ import annotations

import math

import pytest

from launcher.tracker_poll_admission import (
    PoseAdmissionDecision,
    StateAdmissionDecision,
    TrackerPollAdmission,
    TrackerPollAdmissionPolicy,
    UINT32_MASK,
    wire_timestamp_ms,
)


def _gate(**policy_values) -> TrackerPollAdmission:
    gate = TrackerPollAdmission(
        TrackerPollAdmissionPolicy(**policy_values)
    )
    gate.reset_session(1000)
    return gate


def test_wire_timestamp_conversion_matches_shared_uint32_clock():
    assert wire_timestamp_ms(1.2349) == 1234
    assert wire_timestamp_ms((UINT32_MASK + 17) / 1000.0) == 16


@pytest.mark.parametrize(
    "value",
    [True, -1.0, math.nan, math.inf, "1.0", object()],
)
def test_invalid_monotonic_time_is_rejected(value):
    with pytest.raises(ValueError):
        wire_timestamp_ms(value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"maximum_pose_age_ms": True},
        {"maximum_pose_age_ms": -1},
        {"maximum_pose_age_ms": 60_001},
        {"maximum_pose_future_skew_ms": 1.5},
        {"maximum_state_lag_ms": False},
        {"legacy_state_grace_ms": "500"},
    ],
)
def test_policy_requires_bounded_real_integer_milliseconds(kwargs):
    with pytest.raises(ValueError):
        TrackerPollAdmissionPolicy(**kwargs)


def test_current_session_fresh_pose_is_accepted():
    gate = _gate()

    decision = gate.evaluate_pose(1033, 1040)

    assert decision == PoseAdmissionDecision(
        accepted=True,
        timestamp_ms=1033,
        age_ms=7,
        future_skew_ms=None,
        reason="current-session pose accepted",
    )
    snapshot = gate.snapshot()
    assert snapshot.accepted_pose_count == 1
    assert snapshot.last_accepted_pose_timestamp_ms == 1033


@pytest.mark.parametrize("pose_timestamp", [1000, 999])
def test_equal_or_older_pose_cannot_belong_to_new_child_session(pose_timestamp):
    gate = _gate()

    decision = gate.evaluate_pose(pose_timestamp, 1010)

    assert not decision.accepted
    assert "current child session" in decision.reason
    snapshot = gate.snapshot()
    assert snapshot.pre_session_pose_count == 1
    assert snapshot.rejected_pose_count == 1


def test_pose_age_accepts_exact_boundary_and_rejects_one_ms_older():
    accepted = _gate(maximum_pose_age_ms=800)
    rejected = _gate(maximum_pose_age_ms=800)

    assert accepted.evaluate_pose(1100, 1900).accepted
    decision = rejected.evaluate_pose(1100, 1901)

    assert not decision.accepted
    assert decision.age_ms == 801
    assert rejected.snapshot().stale_pose_count == 1


def test_small_future_skew_is_accepted_and_larger_skew_is_rejected():
    accepted = _gate(maximum_pose_future_skew_ms=25)
    rejected = _gate(maximum_pose_future_skew_ms=25)

    decision = accepted.evaluate_pose(1125, 1100)
    assert decision.accepted
    assert decision.age_ms == 0
    assert decision.future_skew_ms == 25

    decision = rejected.evaluate_pose(1126, 1100)
    assert not decision.accepted
    assert decision.future_skew_ms == 26
    assert rejected.snapshot().future_pose_count == 1


def test_pose_admission_is_wrap_safe_across_uint32_rollover():
    gate = TrackerPollAdmission()
    gate.reset_session(0xFFFF_FFF0)

    decision = gate.evaluate_pose(0x0000_0010, 0x0000_0020)

    assert decision.accepted
    assert decision.age_ms == 16
    assert decision.timestamp_ms == 0x10


@pytest.mark.parametrize(
    "pose, now",
    [
        (True, 1100),
        (1033.0, 1100),
        (-1, 1100),
        (UINT32_MASK + 1, 1100),
        (1033, False),
        (1033, 1100.0),
    ],
)
def test_malformed_pose_or_launcher_timestamp_fails_closed(pose, now):
    gate = _gate()

    decision = gate.evaluate_pose(pose, now)

    assert not decision.accepted
    assert gate.snapshot().malformed_pose_count == 1


def test_reset_session_requires_uint32_and_retains_lifetime_counters():
    gate = _gate()
    gate.evaluate_pose(1033, 1040)

    gate.reset_session(2000)

    snapshot = gate.snapshot()
    assert snapshot.reset_count == 2
    assert snapshot.accepted_pose_count == 1
    assert snapshot.session_start_timestamp_ms == 2000
    assert snapshot.last_accepted_pose_timestamp_ms is None
    with pytest.raises(ValueError):
        gate.reset_session(-1)


def test_correlated_state_with_exact_or_bounded_lag_is_accepted():
    gate = _gate(maximum_state_lag_ms=100)

    exact = gate.resolve_state(
        1200,
        ("TRACKING", 1200),
        current_status="initializing",
        session_elapsed_ms=10,
    )
    bounded = gate.resolve_state(
        1300,
        ("hold", 1200),
        current_status="tracking",
        session_elapsed_ms=20,
    )

    assert exact == StateAdmissionDecision(
        status="tracking",
        publish_pose=True,
        correlated=True,
        legacy_fallback=False,
        reason="pose and tracking state correlated",
    )
    assert bounded.status == "hold"
    assert bounded.correlated
    assert gate.snapshot().correlated_state_count == 2


def test_state_older_than_lag_budget_waits_during_startup():
    gate = _gate(maximum_state_lag_ms=100, legacy_state_grace_ms=500)

    decision = gate.resolve_state(
        1200,
        ("tracking", 1099),
        current_status="initializing",
        session_elapsed_ms=499,
    )

    assert not decision.publish_pose
    assert decision.status is None
    assert "too old" in decision.reason
    snapshot = gate.snapshot()
    assert snapshot.stale_state_count == 1
    assert snapshot.waiting_state_count == 1


def test_state_ahead_of_pose_is_not_attached_early():
    gate = _gate()

    decision = gate.resolve_state(
        1200,
        ("paused", 1201),
        current_status="initializing",
        session_elapsed_ms=10,
    )

    assert not decision.publish_pose
    assert decision.status is None
    assert gate.snapshot().future_state_count == 1


@pytest.mark.parametrize("state_timestamp", [1000, 999])
def test_equal_or_older_state_is_rejected_as_pre_session(state_timestamp):
    gate = _gate()

    decision = gate.resolve_state(
        1200,
        ("tracking", state_timestamp),
        current_status="initializing",
        session_elapsed_ms=10,
    )

    assert not decision.publish_pose
    assert gate.snapshot().pre_session_state_count == 1


@pytest.mark.parametrize(
    "state_data",
    [
        (),
        ("tracking",),
        ("unknown", 1100),
        (1, 1100),
        ("tracking", True),
        ("tracking", 1100.0),
        ("tracking", -1),
    ],
)
def test_malformed_state_waits_during_compatibility_grace(state_data):
    gate = _gate(legacy_state_grace_ms=500)

    decision = gate.resolve_state(
        1200,
        state_data,
        current_status="initializing",
        session_elapsed_ms=499,
    )

    assert not decision.publish_pose
    assert decision.status is None
    snapshot = gate.snapshot()
    assert snapshot.malformed_state_count == 1
    assert snapshot.waiting_state_count == 1


def test_missing_state_mapping_waits_then_uses_bounded_legacy_fallback():
    gate = _gate(legacy_state_grace_ms=500)

    waiting = gate.resolve_state(
        1200,
        None,
        current_status="initializing",
        session_elapsed_ms=499.999,
    )
    fallback = gate.resolve_state(
        1233,
        None,
        current_status="initializing",
        session_elapsed_ms=500,
    )

    assert not waiting.publish_pose
    assert fallback == StateAdmissionDecision(
        status="tracking",
        publish_pose=True,
        correlated=False,
        legacy_fallback=True,
        reason=(
            "tracking state mapping is unavailable; "
            "legacy state grace expired"
        ),
    )
    snapshot = gate.snapshot()
    assert snapshot.missing_state_count == 2
    assert snapshot.legacy_fallback_count == 1


@pytest.mark.parametrize("current_status", ["tracking", "hold", "paused"])
def test_uncorrelated_state_preserves_an_established_live_status(current_status):
    gate = _gate()

    decision = gate.resolve_state(
        1200,
        None,
        current_status=current_status,
        session_elapsed_ms=0,
    )

    assert decision.status is None
    assert decision.publish_pose
    assert not decision.legacy_fallback
    assert "preserved established" in decision.reason
    assert gate.snapshot().preserved_state_count == 1


def test_state_correlation_is_wrap_safe():
    gate = TrackerPollAdmission()
    gate.reset_session(0xFFFF_FFF0)

    decision = gate.resolve_state(
        0x0000_0010,
        ("tracking", 0x0000_0005),
        current_status="initializing",
        session_elapsed_ms=20,
    )

    assert decision.correlated
    assert decision.status == "tracking"
    assert decision.publish_pose


def test_invalid_elapsed_time_does_not_trigger_legacy_fallback():
    gate = _gate(legacy_state_grace_ms=0)

    decision = gate.resolve_state(
        1200,
        None,
        current_status="initializing",
        session_elapsed_ms=math.nan,
    )

    assert not decision.publish_pose
    assert decision.status is None
    assert gate.snapshot().waiting_state_count == 1
