"""Packaged tracker runtime with latest-frame and pose-jump protection."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tracker import main as tracker_main
from tracker.latest_frame_runtime import LatestFrameTrackingLoop
from tracker.pose_jump_confirmation import (
    PoseJumpConfirmationGate,
    PoseJumpConfirmationPolicy,
    PoseJumpConfirmationSnapshot,
    parse_pose_jump_confirmation_policy,
)


class _ConfirmedMeasurementAdmission:
    """Apply jump confirmation after the existing freshness/confidence gate."""

    def __init__(
        self,
        admission: object,
        confirmation: PoseJumpConfirmationGate,
    ) -> None:
        self._admission = admission
        self._confirmation = confirmation

    @property
    def confirmation(self) -> PoseJumpConfirmationGate:
        return self._confirmation

    def accept(self, *args: object, **kwargs: object) -> Any:
        accepted = self._admission.accept(*args, **kwargs)
        return self._confirmation.filter(accepted)

    def admit(self, *args: object, **kwargs: object) -> Any:
        admitted = self._admission.admit(*args, **kwargs)
        return self._confirmation.filter(admitted)

    def reset(self, *args: object, **kwargs: object) -> Any:
        result = self._admission.reset(*args, **kwargs)
        self._confirmation.reset()
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._admission, name)


def _policy_from_config_path(
    config_path: object,
) -> PoseJumpConfirmationPolicy:
    if not config_path:
        return PoseJumpConfirmationPolicy()
    try:
        with Path(str(config_path)).open(encoding="utf-8") as config_file:
            loaded = yaml.safe_load(config_file)
        root = loaded if isinstance(loaded, dict) else {}
        return parse_pose_jump_confirmation_policy(root.get("tracking", {}))
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        print(
            "[G3D] Could not read pose-jump confirmation settings; "
            "using safe defaults"
        )
        return PoseJumpConfirmationPolicy()


class StableLatestFrameTrackingLoop(LatestFrameTrackingLoop):
    """Use the existing admission boundary, then confirm extreme jumps."""

    def __init__(
        self,
        *args: object,
        pose_jump_confirmation_policy: (
            PoseJumpConfirmationPolicy | None
        ) = None,
        **kwargs: object,
    ) -> None:
        config_path = kwargs.get("config_path")
        policy = (
            pose_jump_confirmation_policy
            if pose_jump_confirmation_policy is not None
            else _policy_from_config_path(config_path)
        )
        self._pose_jump_confirmation = PoseJumpConfirmationGate(policy)
        super().__init__(*args, **kwargs)
        admission = getattr(self, "_measurement_admission", None)
        if admission is None:
            raise RuntimeError(
                "TrackingLoop measurement-admission boundary is unavailable"
            )
        self._measurement_admission = _ConfirmedMeasurementAdmission(
            admission,
            self._pose_jump_confirmation,
        )

    @property
    def pose_jump_confirmation_policy(self) -> PoseJumpConfirmationPolicy:
        return self._pose_jump_confirmation.policy

    def pose_jump_confirmation_snapshot(
        self,
    ) -> PoseJumpConfirmationSnapshot:
        return self._pose_jump_confirmation.snapshot()


def main() -> None:
    """Run the established tracker bootstrap with both runtime protections."""
    original_loop = tracker_main.TrackingLoop
    tracker_main.TrackingLoop = StableLatestFrameTrackingLoop
    try:
        tracker_main.main()
    finally:
        tracker_main.TrackingLoop = original_loop
