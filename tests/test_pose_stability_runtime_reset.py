from __future__ import annotations

import pytest

from tracker.pose import HeadPosition
from tracker.pose_jump_confirmation import PoseJumpConfirmationGate
from tracker.pose_stability_runtime import _ConfirmedMeasurementAdmission


class FailingAdmission:
    def accept(self, value, _timestamp_ms=None):
        return value

    def reset(self):
        raise RuntimeError("reset failed")


def test_confirmation_reset_runs_even_when_delegate_reset_fails():
    confirmation = PoseJumpConfirmationGate()
    composite = _ConfirmedMeasurementAdmission(
        FailingAdmission(),
        confirmation,
    )
    pose = HeadPosition(
        x_cm=0.0,
        y_cm=0.0,
        z_cm=60.0,
        confidence=0.9,
        capture_timestamp_ms=1000,
    )
    assert composite.accept(pose, 1000) is pose
    assert confirmation.snapshot().anchor_timestamp_ms == 1000

    with pytest.raises(RuntimeError, match="reset failed"):
        composite.reset()

    snapshot = confirmation.snapshot()
    assert snapshot.anchor_timestamp_ms is None
    assert snapshot.candidate_sample_count == 0
