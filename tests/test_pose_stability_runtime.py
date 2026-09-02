from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from tracker import main as tracker_main, pose_stability_runtime
from tracker.pose import HeadPosition
from tracker.pose_jump_confirmation import (
    PoseJumpConfirmationGate,
    PoseJumpConfirmationPolicy,
)
from tracker.pose_stability_runtime import (
    StableLatestFrameTrackingLoop,
    _ConfirmedMeasurementAdmission,
    _policy_from_config_path,
)


def _pose(timestamp_ms: int, *, x: float = 0.0) -> HeadPosition:
    return HeadPosition(
        x_cm=x,
        y_cm=0.0,
        z_cm=60.0,
        confidence=0.9,
        capture_timestamp_ms=timestamp_ms,
    )


class _Admission:
    def __init__(self) -> None:
        self.reset_count = 0
        self.accepted_inputs: list[object] = []

    def accept(self, value, _timestamp_ms=None):
        self.accepted_inputs.append(value)
        return value

    def reset(self):
        self.reset_count += 1
        return "reset"

    def snapshot(self):
        return "admission snapshot"


class _RejectingAdmission(_Admission):
    def accept(self, value, _timestamp_ms=None):
        self.accepted_inputs.append(value)
        return None


def test_composite_runs_existing_admission_before_jump_confirmation():
    admission = _Admission()
    confirmation = PoseJumpConfirmationGate()
    composite = _ConfirmedMeasurementAdmission(admission, confirmation)
    anchor = _pose(1000)
    jump = _pose(1033, x=60.0)

    assert composite.accept(anchor, 1000) is anchor
    assert composite.accept(jump, 1033) is None

    assert admission.accepted_inputs == [anchor, jump]
    snapshot = confirmation.snapshot()
    assert snapshot.suspected_jump_count == 1
    assert snapshot.rejected_candidate_count == 1


def test_existing_admission_rejection_never_reaches_confirmation():
    admission = _RejectingAdmission()
    confirmation = MagicMock()
    composite = _ConfirmedMeasurementAdmission(admission, confirmation)

    assert composite.accept(_pose(1000), 1000) is None
    confirmation.filter.assert_called_once_with(None)


def test_composite_reset_resets_both_boundaries_and_forwards_result():
    admission = _Admission()
    confirmation = PoseJumpConfirmationGate()
    composite = _ConfirmedMeasurementAdmission(admission, confirmation)
    composite.accept(_pose(1000), 1000)

    result = composite.reset()

    assert result == "reset"
    assert admission.reset_count == 1
    assert confirmation.snapshot().anchor_timestamp_ms is None
    assert composite.snapshot() == "admission snapshot"


def test_composite_supports_admit_filter_and_callable_variants():
    class MultiAdmission:
        def admit(self, value):
            return value

        def filter(self, value):
            return value

        def __call__(self, value):
            return value

        def reset(self):
            pass

    composite = _ConfirmedMeasurementAdmission(
        MultiAdmission(),
        PoseJumpConfirmationGate(
            PoseJumpConfirmationPolicy(enabled=False)
        ),
    )
    value = object()

    assert composite.admit(value) is value
    assert composite.filter(value) is value
    assert composite(value) is value


def test_runtime_policy_reads_optional_tracking_configuration(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "tracking": {
                    "pose_jump_confirmation": {
                        "enabled": False,
                        "minimum_xy_jump_cm": 30.0,
                        "confirmation_samples": 3,
                        "candidate_timeout_ms": 300,
                        "reset_after_ms": 900,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    policy = _policy_from_config_path(config_path)

    assert not policy.enabled
    assert policy.minimum_xy_jump_cm == pytest.approx(30.0)
    assert policy.confirmation_samples == 3
    assert policy.candidate_timeout_ms == 300
    assert policy.reset_after_ms == 900


def test_missing_config_uses_safe_defaults(tmp_path, monkeypatch):
    logs: list[str] = []
    monkeypatch.setattr(
        pose_stability_runtime,
        "print",
        logs.append,
        raising=False,
    )

    policy = _policy_from_config_path(tmp_path / "missing.yaml")

    assert policy == PoseJumpConfirmationPolicy()
    assert any("using safe defaults" in message for message in logs)


def test_stable_runtime_wraps_measurement_admission_after_super_init(monkeypatch):
    admission = _Admission()

    def fake_latest_init(self, *args, **kwargs):
        self._measurement_admission = admission

    monkeypatch.setattr(
        pose_stability_runtime.LatestFrameTrackingLoop,
        "__init__",
        fake_latest_init,
    )

    loop = StableLatestFrameTrackingLoop(
        pose_jump_confirmation_policy=PoseJumpConfirmationPolicy(),
    )

    assert isinstance(
        loop._measurement_admission,
        _ConfirmedMeasurementAdmission,
    )
    assert loop._measurement_admission._admission is admission
    assert loop.pose_jump_confirmation_policy == PoseJumpConfirmationPolicy()


def test_missing_measurement_admission_boundary_fails_closed(monkeypatch):
    monkeypatch.setattr(
        pose_stability_runtime.LatestFrameTrackingLoop,
        "__init__",
        lambda self, *args, **kwargs: None,
    )

    with pytest.raises(RuntimeError, match="admission boundary"):
        StableLatestFrameTrackingLoop()


def test_runtime_main_substitutes_stable_loop_only_during_bootstrap(
    monkeypatch,
):
    original = tracker_main.TrackingLoop
    observed: list[object] = []
    monkeypatch.setattr(
        tracker_main,
        "main",
        lambda: observed.append(tracker_main.TrackingLoop),
    )

    pose_stability_runtime.main()

    assert observed == [StableLatestFrameTrackingLoop]
    assert tracker_main.TrackingLoop is original


def test_source_and_frozen_tracker_entrypoints_use_stable_runtime():
    source = Path("tracker/__main__.py").read_text(encoding="utf-8")
    launcher = Path("launcher/__main__.py").read_text(encoding="utf-8")

    assert "from tracker.pose_stability_runtime import main" in source
    assert "from tracker.pose_stability_runtime import main" in launcher
    assert "from tracker.latest_frame_runtime import main" not in source


def test_runtime_inherits_latest_frame_behavior():
    from tracker.latest_frame_runtime import LatestFrameTrackingLoop

    assert issubclass(StableLatestFrameTrackingLoop, LatestFrameTrackingLoop)
