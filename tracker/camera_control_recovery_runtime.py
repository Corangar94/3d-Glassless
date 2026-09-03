"""Packaged tracker runtime with selective webcam control recovery."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tracker import main as tracker_main
from tracker.camera_control_recovery import (
    CameraControlRecovery,
    CameraControlRecoveryPolicy,
    CameraControlRecoverySnapshot,
    apply_camera_control_recovery,
    parse_camera_control_recovery_policy,
    try_restore_automatic_camera_controls,
)
from tracker.pose_stability_runtime import StableLatestFrameTrackingLoop


class _CameraControlLockRetryObserver:
    """Record lock results for one loop without mutating module-global state."""

    def __init__(
        self,
        retry: object,
        owner: "CameraControlRecoveryTrackingLoop",
    ) -> None:
        self._retry = retry
        self._owner = owner

    @property
    def delegate(self) -> object:
        return self._retry

    @property
    def owner(self) -> "CameraControlRecoveryTrackingLoop":
        return self._owner

    def record_result(
        self,
        timestamp_ms: int,
        result: object,
    ) -> bool:
        normalized = (
            result
            if isinstance(result, dict)
            else {
                "errors": (
                    "camera control lock returned invalid data",
                )
            }
        )
        complete = bool(
            self._retry.record_result(timestamp_ms, normalized)
        )
        capture = self._owner._camera_control_recovery_capture
        if capture is not None:
            self._owner._record_camera_control_lock(
                capture,
                normalized,
            )
        return complete

    def __getattr__(self, name: str) -> Any:
        return getattr(self._retry, name)


class _CameraControlRecoveryQualityMonitor:
    """Observe quality after the existing monitor computes its status."""

    def __init__(
        self,
        monitor: object,
        owner: "CameraControlRecoveryTrackingLoop",
    ) -> None:
        self._monitor = monitor
        self._owner = owner

    def update(self, frame: object, timestamp_ms: int):
        status = self._monitor.update(frame, timestamp_ms)
        self._owner._observe_camera_control_quality(
            self._owner.capture_timestamp_ms(timestamp_ms),
            status,
        )
        return status

    def reset_quality_only(self) -> None:
        self._monitor.reset()

    def reset(self) -> None:
        try:
            self._monitor.reset()
        finally:
            self._owner._reset_camera_control_recovery_session()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._monitor, name)


def _policy_from_config_path(
    config_path: object,
) -> CameraControlRecoveryPolicy:
    if not config_path:
        return CameraControlRecoveryPolicy()
    try:
        with Path(str(config_path)).open(encoding="utf-8") as config_file:
            loaded = yaml.safe_load(config_file)
        root = loaded if isinstance(loaded, dict) else {}
        return parse_camera_control_recovery_policy(root.get("camera", {}))
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        print(
            "[G3D] Could not read camera control-recovery settings; "
            "using safe defaults"
        )
        return CameraControlRecoveryPolicy()


class CameraControlRecoveryTrackingLoop(StableLatestFrameTrackingLoop):
    """Restore only locked controls implicated by sustained quality loss."""

    def __init__(
        self,
        *args: object,
        camera_control_recovery_policy: (
            CameraControlRecoveryPolicy | None
        ) = None,
        **kwargs: object,
    ) -> None:
        config_path = kwargs.get("config_path")
        policy = (
            camera_control_recovery_policy
            if camera_control_recovery_policy is not None
            else _policy_from_config_path(config_path)
        )
        self._camera_control_recovery = CameraControlRecovery(policy)
        self._camera_control_lock_state: dict[str, object] = {}
        self._camera_control_recovery_capture: object | None = None
        self._last_camera_control_recovery_result: dict[str, object] = {}
        super().__init__(*args, **kwargs)

        self._bind_camera_control_lock_retry()
        monitor = getattr(self, "_camera_quality_monitor", None)
        if monitor is not None:
            self._camera_quality_monitor = (
                _CameraControlRecoveryQualityMonitor(monitor, self)
            )

    def _bind_camera_control_lock_retry(self) -> None:
        retry = getattr(self, "_camera_control_lock_retry", None)
        if retry is None:
            return
        if isinstance(retry, _CameraControlLockRetryObserver):
            if retry.owner is self:
                return
            retry = retry.delegate
        self._camera_control_lock_retry = (
            _CameraControlLockRetryObserver(retry, self)
        )

    @property
    def camera_control_recovery_policy(self) -> CameraControlRecoveryPolicy:
        return self._camera_control_recovery.policy

    @property
    def camera_control_lock_state(self) -> dict[str, object]:
        return dict(self._camera_control_lock_state)

    @property
    def last_camera_control_recovery_result(self) -> dict[str, object]:
        return dict(self._last_camera_control_recovery_result)

    def camera_control_recovery_snapshot(
        self,
    ) -> CameraControlRecoverySnapshot:
        return self._camera_control_recovery.snapshot()

    def _reset_camera_control_recovery_session(self) -> None:
        self._camera_control_recovery.reset()
        self._camera_control_lock_state = {}
        self._last_camera_control_recovery_result = {}

    def _open_camera_with_recovery(
        self,
        camera_index: int,
        camera_width: int,
        camera_height: int,
        camera_fps: float,
        *,
        backend_start_index: int,
    ):
        capture, backend_index = super()._open_camera_with_recovery(
            camera_index,
            camera_width,
            camera_height,
            camera_fps,
            backend_start_index=backend_start_index,
        )
        self._camera_control_recovery_capture = capture
        self._reset_camera_control_recovery_session()
        return capture, backend_index

    def _record_camera_control_lock(
        self,
        capture: object,
        result: dict[str, object],
    ) -> None:
        if capture is self._camera_control_recovery_capture:
            self._camera_control_lock_state = dict(result)
            self._camera_control_recovery.reset_episodes()

    def _observe_camera_control_quality(
        self,
        timestamp_ms: int,
        status: object,
    ) -> None:
        capture = self._camera_control_recovery_capture
        if capture is None:
            return
        problems = getattr(status, "problems", ())
        request = self._camera_control_recovery.observe(
            timestamp_ms,
            problems,
            self._camera_control_lock_state,
        )
        if not request.requested:
            return
        try:
            result = try_restore_automatic_camera_controls(
                capture,
                request,
                self._camera_control_lock_state,
            )
        except Exception as error:
            result = {
                "autofocus_requested": request.autofocus,
                "auto_exposure_requested": request.auto_exposure,
                "autofocus_reenabled": False,
                "auto_exposure_reenabled": False,
                "errors": (
                    f"camera control recovery failed: {type(error).__name__}",
                ),
            }
        self._last_camera_control_recovery_result = dict(result)
        recovered = self._camera_control_recovery.record_result(
            timestamp_ms,
            request,
            result,
        )
        self._camera_control_lock_state = apply_camera_control_recovery(
            self._camera_control_lock_state,
            result,
        )
        if not recovered:
            return

        monitor = getattr(self, "_camera_quality_monitor", None)
        reset_quality_only = getattr(monitor, "reset_quality_only", None)
        if callable(reset_quality_only):
            reset_quality_only()
        retry = getattr(self, "_camera_control_lock_retry", None)
        reset_retry = getattr(retry, "reset", None)
        if callable(reset_retry):
            reset_retry()
        print(
            "[G3D] Camera quality recovery restored "
            + ", ".join(recovered)
            + "; restarting quality warm-up"
        )


def main() -> None:
    """Run tracker bootstrap with every packaged tracking protection active."""
    original_loop = tracker_main.TrackingLoop
    tracker_main.TrackingLoop = CameraControlRecoveryTrackingLoop
    try:
        tracker_main.main()
    finally:
        tracker_main.TrackingLoop = original_loop
