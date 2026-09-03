from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from tracker.camera_control_recovery import (
    CameraControlRecovery,
    CameraControlRecoveryPolicy,
)
from tracker.camera_control_recovery_runtime import (
    CameraControlRecoveryTrackingLoop,
    _AutomaticControlRestoringCapture,
    _CameraControlLockRetryObserver,
)


class _Capture:
    def __init__(self, *, accept_sets: bool = True) -> None:
        self.accept_sets = accept_sets
        self.events: list[tuple[object, ...]] = []
        self.release_count = 0

    def read(self):
        self.events.append(("read",))
        return False, None

    def set(self, property_id: int, value: float) -> bool:
        self.events.append(("set", property_id, value))
        return self.accept_sets

    def release(self) -> None:
        self.events.append(("release",))
        self.release_count += 1


class _RaisingRetry:
    def record_result(self, _timestamp_ms, _result):
        raise RuntimeError("retry accounting failed")


def _loop(*, enabled: bool = True) -> CameraControlRecoveryTrackingLoop:
    loop = CameraControlRecoveryTrackingLoop.__new__(
        CameraControlRecoveryTrackingLoop
    )
    loop._camera_control_recovery = CameraControlRecovery(
        CameraControlRecoveryPolicy()
    )
    loop._camera_control_lock_state = {}
    loop._camera_control_recovery_capture = None
    loop._last_camera_control_recovery_result = {}
    loop._lock_camera_controls = enabled
    return loop


def _lock_both(loop, capture) -> None:
    loop._camera_control_recovery_capture = capture
    loop._record_camera_control_lock(
        capture,
        {
            "autofocus_locked": True,
            "focus_preserved": True,
            "autofocus_value": 1.0,
            "auto_exposure_locked": True,
            "exposure_preserved": True,
            "auto_exposure_value": 0.75,
        },
    )


def test_release_restores_both_automatic_controls_before_driver_release(
    monkeypatch,
):
    monkeypatch.setattr(cv2, "CAP_PROP_AUTOFOCUS", 1001, raising=False)
    monkeypatch.setattr(
        cv2,
        "CAP_PROP_AUTO_EXPOSURE",
        1002,
        raising=False,
    )
    raw = _Capture()
    loop = _loop()
    wrapped = loop._wrap_restoring_capture(raw)

    assert isinstance(wrapped, _AutomaticControlRestoringCapture)
    _lock_both(loop, wrapped)
    wrapped.release()
    wrapped.release()

    assert raw.events == [
        ("set", 1001, 1.0),
        ("set", 1002, 0.75),
        ("release",),
    ]
    assert raw.release_count == 1
    assert wrapped.released


def test_release_snapshot_survives_base_session_reset(monkeypatch):
    monkeypatch.setattr(cv2, "CAP_PROP_AUTOFOCUS", 1001, raising=False)
    raw = _Capture()
    loop = _loop()
    wrapped = loop._wrap_restoring_capture(raw)
    assert isinstance(wrapped, _AutomaticControlRestoringCapture)
    loop._camera_control_recovery_capture = wrapped
    loop._record_camera_control_lock(
        wrapped,
        {
            "autofocus_locked": True,
            "focus_preserved": True,
            "autofocus_value": 1.0,
        },
    )

    # TrackingLoop resets quality/retry state before it releases a failed
    # capture. The per-capture snapshot must retain the reversible hardware state.
    loop._reset_camera_control_recovery_session()
    assert loop.camera_control_lock_state == {}
    wrapped.release()

    assert raw.events == [
        ("set", 1001, 1.0),
        ("release",),
    ]


def test_later_failed_retry_does_not_erase_proven_lock_ownership(monkeypatch):
    monkeypatch.setattr(cv2, "CAP_PROP_AUTOFOCUS", 1001, raising=False)
    raw = _Capture()
    loop = _loop()
    wrapped = loop._wrap_restoring_capture(raw)
    assert isinstance(wrapped, _AutomaticControlRestoringCapture)
    loop._camera_control_recovery_capture = wrapped
    loop._record_camera_control_lock(
        wrapped,
        {
            "autofocus_locked": True,
            "focus_preserved": True,
            "autofocus_value": 1.0,
            "focus_value": 42.0,
        },
    )

    # A transient later read/write failure cannot prove that the earlier manual
    # mode transition was undone. The release snapshot must keep the known lock.
    loop._record_camera_control_lock(
        wrapped,
        {
            "autofocus_locked": False,
            "focus_preserved": False,
            "autofocus_value": None,
            "focus_value": None,
            "errors": ("temporary control failure",),
        },
    )

    state = loop.camera_control_lock_state
    assert state["autofocus_locked"] is True
    assert state["focus_preserved"] is True
    assert state["autofocus_value"] == 1.0
    assert state["focus_value"] == 42.0
    assert state["errors"] == ("temporary control failure",)

    wrapped.release()
    assert raw.events == [
        ("set", 1001, 1.0),
        ("release",),
    ]


def test_successful_transactional_rollback_clears_prior_lock_ownership():
    loop = _loop()
    capture = object()
    loop._camera_control_recovery_capture = capture
    loop._record_camera_control_lock(
        capture,
        {
            "autofocus_locked": True,
            "focus_preserved": True,
            "autofocus_value": 1.0,
            "focus_value": 42.0,
        },
    )

    loop._record_camera_control_lock(
        capture,
        {
            "autofocus_locked": False,
            "focus_preserved": False,
            "autofocus_rollback": True,
        },
    )

    assert loop.camera_control_lock_state["autofocus_locked"] is False


def test_retry_delegate_exception_still_records_hardware_lock(monkeypatch):
    monkeypatch.setattr(cv2, "CAP_PROP_AUTOFOCUS", 1001, raising=False)
    raw = _Capture()
    loop = _loop()
    wrapped = loop._wrap_restoring_capture(raw)
    assert isinstance(wrapped, _AutomaticControlRestoringCapture)
    loop._camera_control_recovery_capture = wrapped
    observer = _CameraControlLockRetryObserver(_RaisingRetry(), loop)
    result = {
        "autofocus_locked": True,
        "focus_preserved": True,
        "autofocus_value": 1.0,
    }

    with pytest.raises(RuntimeError, match="retry accounting failed"):
        observer.record_result(1000, result)

    assert loop.camera_control_lock_state == result
    wrapped.release()
    assert raw.events == [
        ("set", 1001, 1.0),
        ("release",),
    ]


def test_quality_recovery_updates_release_snapshot(monkeypatch):
    monkeypatch.setattr(cv2, "CAP_PROP_AUTOFOCUS", 1001, raising=False)
    monkeypatch.setattr(
        cv2,
        "CAP_PROP_AUTO_EXPOSURE",
        1002,
        raising=False,
    )
    raw = _Capture()
    loop = _loop()
    wrapped = loop._wrap_restoring_capture(raw)
    assert isinstance(wrapped, _AutomaticControlRestoringCapture)
    _lock_both(loop, wrapped)

    # Autofocus was already restored during quality recovery. Only the still
    # locked exposure controller should be touched at release.
    loop._camera_control_lock_state["autofocus_locked"] = False
    loop._camera_control_lock_state["focus_preserved"] = False
    loop._synchronize_capture_lock_state()
    wrapped.release()

    assert raw.events == [
        ("set", 1002, 0.75),
        ("release",),
    ]


def test_explicit_opt_out_does_not_wrap_capture():
    raw = _Capture()
    loop = _loop(enabled=False)

    assert loop._wrap_restoring_capture(raw) is raw


def test_capture_without_release_remains_compatible():
    opaque = object()
    loop = _loop(enabled=True)

    assert loop._wrap_restoring_capture(opaque) is opaque


def test_failed_restoration_still_releases_driver_once(monkeypatch):
    monkeypatch.setattr(cv2, "CAP_PROP_AUTOFOCUS", 1001, raising=False)
    raw = _Capture(accept_sets=False)
    loop = _loop()
    wrapped = loop._wrap_restoring_capture(raw)
    assert isinstance(wrapped, _AutomaticControlRestoringCapture)
    loop._camera_control_recovery_capture = wrapped
    loop._record_camera_control_lock(
        wrapped,
        {
            "autofocus_locked": True,
            "focus_preserved": True,
            "autofocus_value": 1.0,
        },
    )

    wrapped.release()
    wrapped.release()

    assert raw.release_count == 1
    assert raw.events[-1] == ("release",)
    assert loop.camera_control_lock_state["autofocus_locked"] is True


def test_retired_wrapper_cannot_overwrite_new_capture_state(monkeypatch):
    monkeypatch.setattr(cv2, "CAP_PROP_AUTOFOCUS", 1001, raising=False)
    old_raw = _Capture()
    loop = _loop()
    retired = loop._wrap_restoring_capture(old_raw)
    assert isinstance(retired, _AutomaticControlRestoringCapture)
    loop._camera_control_recovery_capture = retired
    loop._record_camera_control_lock(
        retired,
        {
            "autofocus_locked": True,
            "focus_preserved": True,
            "autofocus_value": 1.0,
        },
    )

    replacement = object()
    loop._camera_control_recovery_capture = replacement
    loop._camera_control_lock_state = {
        "auto_exposure_locked": True,
        "exposure_preserved": True,
    }
    retired.release()

    assert loop._camera_control_recovery_capture is replacement
    assert loop.camera_control_lock_state == {
        "auto_exposure_locked": True,
        "exposure_preserved": True,
    }
    assert old_raw.release_count == 1


def test_runtime_source_wraps_only_enabled_releasable_captures():
    source = Path("tracker/camera_control_recovery_runtime.py").read_text(
        encoding="utf-8"
    )
    wrapper = source.split("    def _wrap_restoring_capture(", 1)[1].split(
        "    def _open_camera_with_recovery(",
        1,
    )[0]
    observer = source.split(
        "class _CameraControlLockRetryObserver:",
        1,
    )[1].split("class _AutomaticControlRestoringCapture:", 1)[0]
    release = source.split(
        "class _AutomaticControlRestoringCapture:",
        1,
    )[1].split("class _CameraControlRecoveryQualityMonitor:", 1)[0]

    assert 'getattr(self, "_lock_camera_controls", False)' in wrapper
    assert 'callable(getattr(capture, "release", None))' in wrapper
    assert "_restore_automatic_controls_before_release" in release
    assert release.index("_restore_automatic_controls_before_release") < (
        release.index('getattr(self._capture, "release")')
    )
    assert "finally:" in observer
    assert "_merge_camera_control_lock_state" in source
    assert "autofocus_rollback" in source
    assert "auto_exposure_rollback" in source
