"""Packaged tracker runtime with selective webcam control recovery."""
from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

import yaml

from tracker import main as tracker_main
from tracker.camera_control_lock_policy import (
    parse_camera_control_lock_enabled,
)
from tracker.camera_control_recovery import (
    CameraControlRecovery,
    CameraControlRecoveryPolicy,
    CameraControlRecoveryRequest,
    CameraControlRecoverySnapshot,
    apply_camera_control_recovery,
    parse_camera_control_recovery_policy,
    try_restore_automatic_camera_controls,
)
from tracker.pose_stability_runtime import StableLatestFrameTrackingLoop


_LOCK_STATE_GROUPS = (
    (
        "autofocus_locked",
        "autofocus_rollback",
        ("focus_preserved", "autofocus_value", "focus_value"),
    ),
    (
        "auto_exposure_locked",
        "auto_exposure_rollback",
        (
            "exposure_preserved",
            "auto_exposure_value",
            "exposure_value",
            "auto_exposure_manual_value",
        ),
    ),
)


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
        try:
            return bool(
                self._retry.record_result(timestamp_ms, normalized)
            )
        finally:
            # The hardware transaction happened before retry accounting. Keep
            # its ownership record even if unexpected retry bookkeeping raises,
            # so camera release can still restore automatic controls.
            capture = self._owner._camera_control_recovery_capture
            if capture is not None:
                self._owner._record_camera_control_lock(
                    capture,
                    normalized,
                )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._retry, name)


class _AutomaticControlRestoringCapture:
    """Restore this capture's locked automatic modes before releasing it."""

    __g3d_camera_control_restoring_capture__ = True

    def __init__(
        self,
        capture: object,
        owner: "CameraControlRecoveryTrackingLoop",
    ) -> None:
        self._capture = capture
        self._owner = owner
        self._lock_state: dict[str, object] = {}
        self._release_lock = threading.Lock()
        self._released = False

    @property
    def delegate(self) -> object:
        return self._capture

    @property
    def owner(self) -> "CameraControlRecoveryTrackingLoop":
        return self._owner

    @property
    def released(self) -> bool:
        with self._release_lock:
            return self._released

    def record_lock_state(self, state: dict[str, object]) -> None:
        with self._release_lock:
            if not self._released:
                self._lock_state = dict(state)

    def release(self) -> None:
        with self._release_lock:
            if self._released:
                return
            self._released = True
            lock_state = dict(self._lock_state)
        try:
            self._owner._restore_automatic_controls_before_release(
                self,
                lock_state,
            )
        finally:
            release = getattr(self._capture, "release")
            release()

    def __enter__(self) -> "_AutomaticControlRestoringCapture":
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._capture, name)


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


def _camera_config_from_path(
    config_path: object,
) -> dict[str, object] | None:
    if not config_path:
        return {}
    try:
        with Path(str(config_path)).open(encoding="utf-8") as config_file:
            loaded = yaml.safe_load(config_file)
        if not isinstance(loaded, dict):
            raise ValueError("configuration root must be a mapping")
        camera = loaded.get("camera", {})
        if not isinstance(camera, dict):
            raise ValueError("camera configuration must be a mapping")
        return camera
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        print(
            "[G3D] Could not read camera control settings; "
            "using caller defaults"
        )
        return None


def _policy_from_config_path(
    config_path: object,
) -> CameraControlRecoveryPolicy:
    camera = _camera_config_from_path(config_path)
    return (
        CameraControlRecoveryPolicy()
        if camera is None
        else parse_camera_control_recovery_policy(camera)
    )


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
        camera_config = _camera_config_from_path(config_path)
        policy = (
            camera_control_recovery_policy
            if camera_control_recovery_policy is not None
            else (
                CameraControlRecoveryPolicy()
                if camera_config is None
                else parse_camera_control_recovery_policy(camera_config)
            )
        )
        if config_path and camera_config is not None:
            kwargs["lock_camera_controls"] = (
                parse_camera_control_lock_enabled(
                    camera_config,
                    logger=print,
                )
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

    def _wrap_restoring_capture(self, capture: object | None) -> object | None:
        if (
            capture is None
            or not bool(getattr(self, "_lock_camera_controls", False))
            or not callable(getattr(capture, "release", None))
        ):
            return capture
        if isinstance(capture, _AutomaticControlRestoringCapture):
            if capture.owner is self:
                return capture
            capture = capture.delegate
        return _AutomaticControlRestoringCapture(capture, self)

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
        wrapped = self._wrap_restoring_capture(capture)
        self._camera_control_recovery_capture = wrapped
        self._reset_camera_control_recovery_session()
        return wrapped, backend_index

    def _merge_camera_control_lock_state(
        self,
        result: dict[str, object],
    ) -> dict[str, object]:
        previous = self._camera_control_lock_state
        merged = dict(previous)
        merged.update(result)
        for lock_key, rollback_key, metadata_keys in _LOCK_STATE_GROUPS:
            if not bool(previous.get(lock_key, False)):
                continue
            if bool(result.get(rollback_key, False)):
                # A successful transactional rollback is explicit evidence that
                # this automatic controller is no longer owned in manual mode.
                merged[lock_key] = False
                continue
            if bool(result.get(lock_key, False)):
                for key in metadata_keys:
                    if (
                        (key not in result or result.get(key) is None)
                        and key in previous
                    ):
                        merged[key] = previous[key]
                continue
            # A failed or malformed later retry does not prove that an earlier
            # manual-mode transition was undone. Retain ownership until an
            # explicit rollback or quality/release recovery succeeds.
            merged[lock_key] = True
            for key in metadata_keys:
                if key in previous:
                    merged[key] = previous[key]
        return merged

    def _record_camera_control_lock(
        self,
        capture: object,
        result: dict[str, object],
    ) -> None:
        if capture is not self._camera_control_recovery_capture:
            return
        self._camera_control_lock_state = (
            self._merge_camera_control_lock_state(result)
        )
        self._camera_control_recovery.reset_episodes()
        if isinstance(capture, _AutomaticControlRestoringCapture):
            capture.record_lock_state(self._camera_control_lock_state)

    def _synchronize_capture_lock_state(self) -> None:
        capture = self._camera_control_recovery_capture
        if isinstance(capture, _AutomaticControlRestoringCapture):
            capture.record_lock_state(self._camera_control_lock_state)

    def _restore_automatic_controls_before_release(
        self,
        capture: object,
        lock_state: dict[str, object],
    ) -> None:
        request = CameraControlRecoveryRequest(
            autofocus=bool(lock_state.get("autofocus_locked", False)),
            auto_exposure=bool(
                lock_state.get("auto_exposure_locked", False)
            ),
            reasons=("camera release",),
        )
        if not request.requested:
            return
        try:
            result = try_restore_automatic_camera_controls(
                capture,
                request,
                lock_state,
            )
        except Exception as error:
            result = {
                "autofocus_requested": request.autofocus,
                "auto_exposure_requested": request.auto_exposure,
                "autofocus_reenabled": False,
                "auto_exposure_reenabled": False,
                "errors": (
                    "camera release control restoration failed: "
                    f"{type(error).__name__}",
                ),
            }

        restored: list[str] = []
        if bool(result.get("autofocus_reenabled", False)):
            restored.append("autofocus")
        if bool(result.get("auto_exposure_reenabled", False)):
            restored.append("auto exposure")

        if capture is self._camera_control_recovery_capture:
            self._last_camera_control_recovery_result = dict(result)
            self._camera_control_lock_state = apply_camera_control_recovery(
                lock_state,
                result,
            )
        if restored:
            print(
                "[G3D] Restored "
                + ", ".join(restored)
                + " before camera release"
            )
        failed = request.autofocus and "autofocus" not in restored
        failed = failed or (
            request.auto_exposure and "auto exposure" not in restored
        )
        if failed:
            errors = result.get("errors", ())
            print(
                "[G3D] Camera automatic-control restoration incomplete "
                f"before release: {errors}"
            )

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
        self._synchronize_capture_lock_state()
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
