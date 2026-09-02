from __future__ import annotations

from dataclasses import replace

import pytest

from tracker.backend_transition_state import (
    mark_backend_transition,
    reset_backend_transition_generation,
)
from tracker.pose import HeadPosition
from tracker.pose_jump_confirmation import (
    PoseJumpConfirmationGate,
    PoseJumpConfirmationPolicy,
    parse_pose_jump_confirmation_policy,
)


@pytest.fixture(autouse=True)
def _clean_backend_transition_generation():
    reset_backend_transition_generation()
    yield
    reset_backend_transition_generation()


def _pose(
    timestamp_ms: int,
    *,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 60.0,
    yaw: float = 0.0,
    pitch: float = 0.0,
    roll: float = 0.0,
    confidence: float = 0.9,
) -> HeadPosition:
    return HeadPosition(
        x_cm=x,
        y_cm=y,
        z_cm=z,
        yaw_deg=yaw,
        pitch_deg=pitch,
        roll_deg=roll,
        confidence=confidence,
        capture_timestamp_ms=timestamp_ms,
    )


def test_first_pose_and_normal_motion_are_accepted_immediately():
    gate = PoseJumpConfirmationGate()
    first = _pose(1000)
    second = _pose(1033, x=8.0, y=2.0, z=65.0, yaw=20.0)

    assert gate.filter(first) is first
    assert gate.filter(second) is second

    snapshot = gate.snapshot()
    assert snapshot.accepted_count == 2
    assert snapshot.suspected_jump_count == 0
    assert snapshot.anchor_timestamp_ms == 1033


def test_one_frame_face_switch_is_rejected_then_original_viewer_recovers():
    gate = PoseJumpConfirmationGate()
    anchor = _pose(1000, x=0.0, z=60.0, yaw=0.0)
    switch = _pose(1033, x=55.0, z=125.0, yaw=100.0)
    recovered = _pose(1066, x=1.0, z=61.0, yaw=2.0)

    assert gate.filter(anchor) is anchor
    assert gate.filter(switch) is None
    assert gate.filter(recovered) is recovered

    snapshot = gate.snapshot()
    assert snapshot.suspected_jump_count == 1
    assert snapshot.rejected_candidate_count == 1
    assert snapshot.confirmed_jump_count == 0
    assert snapshot.candidate_sample_count == 0
    assert snapshot.anchor_timestamp_ms == 1066


def test_two_consistent_extreme_samples_confirm_a_persistent_switch():
    gate = PoseJumpConfirmationGate()
    anchor = _pose(1000)
    candidate_one = _pose(1033, x=55.0, z=125.0, yaw=100.0)
    candidate_two = _pose(1066, x=57.0, z=123.0, yaw=104.0)

    assert gate.filter(anchor) is anchor
    assert gate.filter(candidate_one) is None
    assert gate.filter(candidate_two) is candidate_two

    snapshot = gate.snapshot()
    assert snapshot.suspected_jump_count == 2
    assert snapshot.rejected_candidate_count == 1
    assert snapshot.confirmed_jump_count == 1
    assert snapshot.accepted_count == 2
    assert snapshot.anchor_timestamp_ms == 1066
    assert snapshot.candidate_sample_count == 0


def test_inconsistent_extreme_samples_do_not_confirm_each_other():
    gate = PoseJumpConfirmationGate()
    gate.filter(_pose(1000))

    first = _pose(1033, x=55.0, z=125.0, yaw=100.0)
    unrelated = _pose(1066, x=-70.0, z=35.0, yaw=-110.0)

    assert gate.filter(first) is None
    assert gate.filter(unrelated) is None

    snapshot = gate.snapshot()
    assert snapshot.confirmed_jump_count == 0
    assert snapshot.rejected_candidate_count == 2
    assert snapshot.candidate_sample_count == 1
    assert snapshot.candidate_timestamp_ms == 1066


def test_low_confidence_jump_never_becomes_confirmation_candidate():
    gate = PoseJumpConfirmationGate()
    gate.filter(_pose(1000))

    weak = _pose(1033, x=60.0, z=130.0, confidence=0.44)
    strong = _pose(1066, x=61.0, z=129.0, confidence=0.9)

    assert gate.filter(weak) is None
    assert gate.filter(strong) is None

    snapshot = gate.snapshot()
    assert snapshot.low_confidence_jump_count == 1
    assert snapshot.candidate_sample_count == 1
    assert snapshot.confirmed_jump_count == 0


def test_valid_degree_wrap_is_not_misclassified_as_large_rotation():
    gate = PoseJumpConfirmationGate()
    first = _pose(1000, yaw=179.0, pitch=-179.0, roll=178.0)
    wrapped = _pose(1033, yaw=-179.0, pitch=179.0, roll=-179.0)

    assert gate.filter(first) is first
    assert gate.filter(wrapped) is wrapped
    assert gate.snapshot().suspected_jump_count == 0


def test_orientation_only_jump_requires_confirmation():
    gate = PoseJumpConfirmationGate()
    gate.filter(_pose(1000, yaw=0.0))
    first = _pose(1033, yaw=90.0)
    second = _pose(1066, yaw=94.0)

    assert gate.filter(first) is None
    assert gate.filter(second) is second
    assert gate.snapshot().confirmed_jump_count == 1


def test_trigger_scales_with_actual_capture_interval():
    gate = PoseJumpConfirmationGate()
    gate.filter(_pose(1000, x=0.0))

    # 25 cm is extreme over 33 ms (20 cm floor), but not over 100 ms where the
    # 600 cm/s trigger permits 60 cm.
    assert gate.filter(_pose(1033, x=25.0)) is None

    gate.reset()
    gate.filter(_pose(1000, x=0.0))
    slow_cadence = _pose(1100, x=25.0)
    assert gate.filter(slow_cadence) is slow_cadence


def test_long_gap_starts_a_new_viewer_episode_without_confirmation():
    gate = PoseJumpConfirmationGate()
    gate.filter(_pose(1000))
    reacquired = _pose(1750, x=80.0, z=150.0, yaw=120.0)

    assert gate.filter(reacquired) is reacquired
    assert gate.snapshot().suspected_jump_count == 0


def test_backend_transition_starts_a_new_confirmation_episode():
    gate = PoseJumpConfirmationGate()
    gate.filter(_pose(5000, x=0.0))
    generation = mark_backend_transition(preserve_position=True)
    replacement = _pose(1000, x=70.0, z=130.0, yaw=100.0)

    assert gate.filter(replacement) is replacement

    snapshot = gate.snapshot()
    assert snapshot.backend_transition_generation == generation
    assert snapshot.suspected_jump_count == 0
    assert snapshot.anchor_timestamp_ms == 1000


def test_disabled_gate_is_a_true_passthrough():
    gate = PoseJumpConfirmationGate(
        PoseJumpConfirmationPolicy(enabled=False)
    )
    first = _pose(1000)
    jump = _pose(1033, x=100.0, z=200.0, yaw=150.0)

    assert gate.filter(first) is first
    assert gate.filter(jump) is jump
    assert gate.snapshot().accepted_count == 0


def test_opaque_and_zero_timestamp_direct_values_remain_compatible():
    gate = PoseJumpConfirmationGate()
    opaque = object()
    zero_timestamp = _pose(0, x=100.0)

    assert gate.filter(opaque) is opaque
    assert gate.filter(zero_timestamp) is zero_timestamp
    assert gate.snapshot().anchor_timestamp_ms is None


def test_candidate_timeout_requires_a_new_confirmation_sequence():
    gate = PoseJumpConfirmationGate()
    gate.filter(_pose(1000))
    first = _pose(1033, x=60.0, z=125.0)
    expired = _pose(1300, x=61.0, z=126.0)

    assert gate.filter(first) is None
    assert gate.filter(expired) is None
    assert gate.snapshot().candidate_sample_count == 1


def test_policy_parser_accepts_numeric_strings_and_boolean_text():
    policy = parse_pose_jump_confirmation_policy(
        {
            "pose_jump_confirmation": {
                "enabled": "false",
                "minimum_xy_jump_cm": "30",
                "confirmation_samples": "3",
                "candidate_timeout_ms": "300",
                "reset_after_ms": "900",
                "minimum_candidate_confidence": "0.6",
            }
        }
    )

    assert not policy.enabled
    assert policy.minimum_xy_jump_cm == pytest.approx(30.0)
    assert policy.confirmation_samples == 3
    assert policy.candidate_timeout_ms == 300
    assert policy.reset_after_ms == 900
    assert policy.minimum_candidate_confidence == pytest.approx(0.6)


@pytest.mark.parametrize(
    "values",
    [
        [],
        {"enabled": "maybe"},
        {"minimum_xy_jump_cm": -1},
        {"confirmation_samples": 1},
        {"candidate_timeout_ms": 0},
        {"candidate_timeout_ms": 500, "reset_after_ms": 499},
        {"minimum_candidate_confidence": 1.1},
    ],
)
def test_invalid_policy_falls_back_atomically(values):
    logs: list[str] = []

    policy = parse_pose_jump_confirmation_policy(
        {"pose_jump_confirmation": values},
        logger=logs.append,
    )

    assert policy == PoseJumpConfirmationPolicy()
    assert len(logs) == 1


def test_custom_three_sample_confirmation_requires_three_consistent_results():
    policy = replace(
        PoseJumpConfirmationPolicy(),
        confirmation_samples=3,
    )
    gate = PoseJumpConfirmationGate(policy)
    gate.filter(_pose(1000))

    assert gate.filter(_pose(1033, x=60.0, z=125.0)) is None
    assert gate.filter(_pose(1066, x=61.0, z=124.0)) is None
    third = _pose(1099, x=62.0, z=123.0)
    assert gate.filter(third) is third
    assert gate.snapshot().confirmed_jump_count == 1
