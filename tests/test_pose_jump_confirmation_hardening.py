from __future__ import annotations

from dataclasses import replace

import pytest

from tracker.pose import HeadPosition
from tracker.pose_jump_confirmation import (
    PoseJumpConfirmationGate,
    PoseJumpConfirmationPolicy,
    parse_pose_jump_confirmation_policy,
)


def _pose(
    timestamp_ms: int,
    *,
    x: float = 0.0,
    z: float = 60.0,
) -> HeadPosition:
    return HeadPosition(
        x_cm=x,
        y_cm=0.0,
        z_cm=z,
        confidence=0.9,
        capture_timestamp_ms=timestamp_ms,
    )


def test_three_sample_confirmation_cannot_drift_across_tolerance_steps():
    policy = replace(
        PoseJumpConfirmationPolicy(),
        confirmation_samples=3,
        candidate_xy_tolerance_cm=12.0,
    )
    gate = PoseJumpConfirmationGate(policy)
    gate.filter(_pose(1000))

    assert gate.filter(_pose(1033, x=60.0, z=125.0)) is None
    assert gate.filter(_pose(1066, x=71.0, z=125.0)) is None
    # This is within 12 cm of the second sample but 22 cm from the fixed first
    # candidate. A rolling candidate would incorrectly confirm cumulative drift.
    assert gate.filter(_pose(1099, x=82.0, z=125.0)) is None

    snapshot = gate.snapshot()
    assert snapshot.confirmed_jump_count == 0
    assert snapshot.candidate_sample_count == 1
    assert snapshot.candidate_timestamp_ms == 1099
    assert snapshot.candidate_latest_timestamp_ms == 1099


def test_candidate_window_is_measured_from_the_first_candidate():
    policy = replace(
        PoseJumpConfirmationPolicy(),
        confirmation_samples=4,
        candidate_timeout_ms=100,
    )
    gate = PoseJumpConfirmationGate(policy)
    gate.filter(_pose(1000))

    assert gate.filter(_pose(1033, x=200.0, z=300.0)) is None
    assert gate.filter(_pose(1066, x=201.0, z=299.0)) is None
    assert gate.filter(_pose(1099, x=202.0, z=298.0)) is None
    # Every adjacent step is timely, but 132 ms has elapsed from the origin.
    assert gate.filter(_pose(1165, x=203.0, z=297.0)) is None

    snapshot = gate.snapshot()
    assert snapshot.confirmed_jump_count == 0
    assert snapshot.candidate_sample_count == 1
    assert snapshot.candidate_timestamp_ms == 1165


@pytest.mark.parametrize(
    "values",
    [
        {"confirmation_samples": 2.5},
        {"confirmation_samples": True},
        {"candidate_timeout_ms": 250.5},
        {"reset_after_ms": False},
        {"candidate_timeout_ms": "250.0"},
    ],
)
def test_fractional_or_boolean_integer_settings_fall_back_atomically(values):
    logs: list[str] = []

    policy = parse_pose_jump_confirmation_policy(
        {"pose_jump_confirmation": values},
        logger=logs.append,
    )

    assert policy == PoseJumpConfirmationPolicy()
    assert len(logs) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"confirmation_samples": 2.5},
        {"confirmation_samples": True},
        {"candidate_timeout_ms": 250.5},
        {"reset_after_ms": False},
    ],
)
def test_direct_policy_requires_real_integer_timing_fields(kwargs):
    with pytest.raises(ValueError):
        PoseJumpConfirmationPolicy(**kwargs)
