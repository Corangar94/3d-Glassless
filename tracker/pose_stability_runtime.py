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

    def _apply(
        self,
        method_name: str,
        *args: object,
        **kwargs: object,
    ) -> Any:
        method = getattr(self._admission, method_name)
        accepted = method(*args, **kwargs)
        return self._confirmation.filter(accepted)

    def accept(self, *args: object, **kwargs: object) -> Any:
        return self._apply("accept", *args, **kwargs)

    def admit(self, *args: object, **kwargs: object) -> Any:
        return self._apply("admit", *args, **kwargs)

    def filter(self, *args: object, **kwargs: object) -> Any:
        return self._apply("filter", *args, **kwargs)

    def __call__(self, *args: object, **kwargs: object) -> Any:
        admission = self._admission
        if not callable(admission):
            raise TypeError("measurement admission boundary is not callable")
        accepted = admission(*args, **kwargs)
        return self._confirmation.filter(accepted)

    def reset(self, *args: object, **kwargs: object) -> Any:
        try:
            return self._admission.reset(*args, **kwargs)
        finally:
            # Never retain a viewer anchor or candidate across a lifecycle reset,
            # even when the delegated boundary reports its own reset failure.
            self._confirmation.reset()

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
    """Run tracker bootstrap with every packaged protection active."""
    # Imported lazily because the recovery loop subclasses the stability loop
    # defined above. This keeps the dependency acyclic while PyInstaller receives
    # an explicit hidden import for both modules.
    from tracker.camera_control_recovery_runtime import (
        CameraControlRecoveryTrackingLoop,
    )

    original_loop = tracker_main.TrackingLoop
    tracker_main.TrackingLoop = CameraControlRecoveryTrackingLoop
    try:
        tracker_main.main()
    finally:
        tracker_main.TrackingLoop = original_loop
