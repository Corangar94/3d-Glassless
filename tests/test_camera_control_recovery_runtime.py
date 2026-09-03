from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import cv2
import pytest
import yaml

from tracker import camera_control_recovery_runtime, main as tracker_main
from tracker.camera_control_recovery import CameraControlRecoveryPolicy
from tracker.camera_control_recovery_runtime import (
    CameraControlRecoveryTrackingLoop,
    _CameraControlLockRetryObserver,
    _CameraControlRecoveryQualityMonitor,
    _policy_from_config_path,
)
from tracker.pose_stability_runtime import StableLatestFrameTrackingLoop


class _Capture:
    def __init__(self) -> None:
        self.calls: list[tuple[int, float]] = []

    def set(self, property_id: int, value: float) -> bool:
        self.calls.append((property_id, value))
        return True


class _Retry:
    def __init__(self, *, complete: bool = False) -> None:
        self.complete = complete
        self.reset_count = 0
        self.record_calls: list[tuple[int, dict[str, object]]] = []

    def reset(self) -> None:
        self.reset_count += 1

    def record_result(
        self,
        timestamp_ms: int,
        result: dict[str, object],
    ) -> bool:
        self.record_calls.append((timestamp_ms, result))
        return self.complete


class _QualityProxy:
    def __init__(self) -> None:
        self.reset_quality_count = 0

    def reset_quality_only(self) -> None:
        self.reset_quality_count += 1


def _bare_loop(policy: CameraControlRecoveryPolicy) -> CameraControlRecoveryTrackingLoop:
    from tracker.camera_control_recovery import CameraControlRecovery

    loop = CameraControlRecoveryTrackingLoop.__new__(
        CameraControlRecoveryTrackingLoop
    )
    loop._camera_control_recovery = CameraControlRecovery(policy)
    loop._camera_control_lock_state = {}
    loop._camera_control_recovery_capture = None
    loop._last_camera_control_recovery_result = {}
    return loop


def test_runtime_policy_reads_nested_camera_configuration(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "camera": {
                    "control_recovery": {
                        "degradation_hold_ms": 1500,
                        "retry_interval_ms": 4000,
                        "max_attempts_per_episode": 2,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    policy = _policy_from_config_path(config_path)

    assert policy == CameraControlRecoveryPolicy(1500, 4000, 2)


def test_missing_runtime_config_uses_safe_defaults(tmp_path, monkeypatch):
    logs: list[str] = []
    monkeypatch.setattr(
        camera_control_recovery_runtime,
        "print",
        logs.append,
        raising=False,
    )

    policy = _policy_from_config_path(tmp_path / "missing.yaml")

    assert policy == CameraControlRecoveryPolicy()
    assert any("using safe defaults" in message for message in logs)


def test_quality_proxy_uses_latest_frame_acquisition_timestamp():
    monitor = MagicMock()
    monitor.update.return_value = SimpleNamespace(problems=())
    owner = MagicMock()
    owner.capture_timestamp_ms.return_value = 1234
    proxy = _CameraControlRecoveryQualityMonitor(monitor, owner)
    frame = object()

    status = proxy.update(frame, 9999)

    assert status is monitor.update.return_value
    monitor.update.assert_called_once_with(frame, 9999)
    owner.capture_timestamp_ms.assert_called_once_with(9999)
    owner._observe_camera_control_quality.assert_called_once_with(1234, status)


def test_quality_proxy_session_reset_resets_monitor_and_recovery():
    monitor = MagicMock()
    owner = MagicMock()
    proxy = _CameraControlRecoveryQualityMonitor(monitor, owner)

    proxy.reset()

    monitor.reset.assert_called_once_with()
    owner._reset_camera_control_recovery_session.assert_called_once_with()


def test_constructor_wraps_existing_quality_monitor_and_retry(monkeypatch):
    monitor = MagicMock()
    retry = _Retry()

    def fake_super_init(self, *args, **kwargs):
        self._camera_quality_monitor = monitor
        self._camera_control_lock_retry = retry

    monkeypatch.setattr(
        StableLatestFrameTrackingLoop,
        "__init__",
        fake_super_init,
    )

    loop = CameraControlRecoveryTrackingLoop(
        camera_control_recovery_policy=CameraControlRecoveryPolicy()
    )

    assert isinstance(
        loop._camera_quality_monitor,
        _CameraControlRecoveryQualityMonitor,
    )
    assert loop._camera_quality_monitor._monitor is monitor
    assert isinstance(
        loop._camera_control_lock_retry,
        _CameraControlLockRetryObserver,
    )
    assert loop._camera_control_lock_retry.delegate is retry


def test_constructor_does_not_double_wrap_retry_observer(monkeypatch):
    loop = _bare_loop(CameraControlRecoveryPolicy())
    retry = _Retry()
    existing = _CameraControlLockRetryObserver(retry, loop)

    def fake_super_init(self, *args, **kwargs):
        self._camera_quality_monitor = None
        self._camera_control_lock_retry = existing

    monkeypatch.setattr(
        StableLatestFrameTrackingLoop,
        "__init__",
        fake_super_init,
    )

    constructed = CameraControlRecoveryTrackingLoop(
        camera_control_recovery_policy=CameraControlRecoveryPolicy()
    )

    assert constructed._camera_control_lock_retry is existing


def test_camera_reopen_stores_capture_and_resets_session(monkeypatch):
    capture = object()
    loop = _bare_loop(CameraControlRecoveryPolicy())
    loop._camera_control_lock_state = {"autofocus_locked": True}
    loop._last_camera_control_recovery_result = {"old": True}
    monkeypatch.setattr(
        StableLatestFrameTrackingLoop,
        "_open_camera_with_recovery",
        lambda *_args, **_kwargs: (capture, 2),
    )

    returned, backend_index = loop._open_camera_with_recovery(
        0,
        1280,
        720,
        30.0,
        backend_start_index=1,
    )

    assert returned is capture
    assert backend_index == 2
    assert loop._camera_control_recovery_capture is capture
    assert loop.camera_control_lock_state == {}
    assert loop.last_camera_control_recovery_result == {}


def test_successful_focus_recovery_restarts_quality_warmup(monkeypatch):
    autofocus_id = 1001
    monkeypatch.setattr(cv2, "CAP_PROP_AUTOFOCUS", autofocus_id, raising=False)
    capture = _Capture()
    loop = _bare_loop(
        CameraControlRecoveryPolicy(
            degradation_hold_ms=0,
            retry_interval_ms=5000,
            max_attempts_per_episode=3,
        )
    )
    loop._camera_control_recovery_capture = capture
    loop._camera_control_lock_state = {
        "autofocus_locked": True,
        "focus_preserved": True,
        "autofocus_value": 1.0,
        "auto_exposure_locked": False,
    }
    quality = _QualityProxy()
    retry = _Retry()
    loop._camera_quality_monitor = quality
    loop._camera_control_lock_retry = retry
    logs: list[str] = []
    monkeypatch.setattr(
        camera_control_recovery_runtime,
        "print",
        logs.append,
        raising=False,
    )

    loop._observe_camera_control_quality(
        1000,
        SimpleNamespace(problems=("soft or motion-blurred",)),
    )

    assert capture.calls == [(autofocus_id, 1.0)]
    assert loop.camera_control_lock_state["autofocus_locked"] is False
    assert quality.reset_quality_count == 1
    assert retry.reset_count == 1
    assert any("restored autofocus" in message for message in logs)
    assert (
        loop.camera_control_recovery_snapshot().autofocus_recovery_count
        == 1
    )


def test_exposure_recovery_does_not_unlock_focus(monkeypatch):
    autofocus_id = 1001
    exposure_id = 1002
    monkeypatch.setattr(cv2, "CAP_PROP_AUTOFOCUS", autofocus_id, raising=False)
    monkeypatch.setattr(
        cv2,
        "CAP_PROP_AUTO_EXPOSURE",
        exposure_id,
        raising=False,
    )
    capture = _Capture()
    loop = _bare_loop(
        CameraControlRecoveryPolicy(degradation_hold_ms=0)
    )
    loop._camera_control_recovery_capture = capture
    loop._camera_control_lock_state = {
        "autofocus_locked": True,
        "focus_preserved": True,
        "auto_exposure_locked": True,
        "exposure_preserved": True,
    }
    loop._camera_quality_monitor = _QualityProxy()
    loop._camera_control_lock_retry = _Retry()

    loop._observe_camera_control_quality(
        1000,
        SimpleNamespace(problems=("underexposed",)),
    )

    assert capture.calls == [(exposure_id, 0.75)]
    assert loop.camera_control_lock_state["auto_exposure_locked"] is False
    assert loop.camera_control_lock_state["autofocus_locked"] is True


def test_retry_observer_records_result_without_rewriting_global():
    capture = object()
    loop = _bare_loop(CameraControlRecoveryPolicy())
    loop._camera_control_recovery_capture = capture
    retry = _Retry(complete=True)
    observer = _CameraControlLockRetryObserver(retry, loop)
    original_global = tracker_main.try_lock_camera_controls
    expected = {
        "autofocus_locked": True,
        "focus_preserved": True,
    }

    complete = observer.record_result(1234, expected)

    assert complete
    assert retry.record_calls == [(1234, expected)]
    assert loop.camera_control_lock_state == expected
    assert tracker_main.try_lock_camera_controls is original_global


def test_retry_observers_are_isolated_between_loop_instances():
    first_capture = object()
    second_capture = object()
    first = _bare_loop(CameraControlRecoveryPolicy())
    second = _bare_loop(CameraControlRecoveryPolicy())
    first._camera_control_recovery_capture = first_capture
    second._camera_control_recovery_capture = second_capture
    first_observer = _CameraControlLockRetryObserver(_Retry(), first)
    second_observer = _CameraControlLockRetryObserver(_Retry(), second)

    first_observer.record_result(
        1000,
        {"autofocus_locked": True, "focus_preserved": True},
    )

    assert first.camera_control_lock_state["autofocus_locked"] is True
    assert second.camera_control_lock_state == {}

    second_observer.record_result(
        1001,
        {"auto_exposure_locked": True, "exposure_preserved": True},
    )

    assert first.camera_control_lock_state.get("auto_exposure_locked") is None
    assert second.camera_control_lock_state["auto_exposure_locked"] is True


def test_retry_observer_normalizes_malformed_lock_result():
    capture = object()
    loop = _bare_loop(CameraControlRecoveryPolicy())
    loop._camera_control_recovery_capture = capture
    retry = _Retry()
    observer = _CameraControlLockRetryObserver(retry, loop)

    assert not observer.record_result(1000, object())

    recorded = retry.record_calls[0][1]
    assert recorded == {
        "errors": ("camera control lock returned invalid data",)
    }
    assert loop.camera_control_lock_state == recorded


def test_retry_observer_forwards_reset_and_attributes():
    loop = _bare_loop(CameraControlRecoveryPolicy())
    retry = _Retry(complete=True)
    observer = _CameraControlLockRetryObserver(retry, loop)

    observer.reset()

    assert observer.complete is True
    assert retry.reset_count == 1


def test_runtime_contains_no_module_global_lock_replacement():
    source = Path("tracker/camera_control_recovery_runtime.py").read_text(
        encoding="utf-8"
    )
    runtime_class = source.split(
        "class CameraControlRecoveryTrackingLoop",
        1,
    )[1].split("\ndef main()", 1)[0]

    assert "tracker_main.try_lock_camera_controls =" not in source
    assert "def run(" not in runtime_class
    assert "_CameraControlLockRetryObserver(retry, self)" in runtime_class


def test_runtime_inherits_pose_stability_and_latest_frame_stack():
    assert issubclass(
        CameraControlRecoveryTrackingLoop,
        StableLatestFrameTrackingLoop,
    )


def test_repository_setup_docs_and_frozen_defaults_match_policy():
    config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    wizard = Path("launcher/wizard.py").read_text(encoding="utf-8")
    spec = Path("Glassless3D.spec").read_text(encoding="utf-8")
    docs = Path("docs/CAMERA_CONTROL_RECOVERY.md").read_text(
        encoding="utf-8"
    )

    assert config["camera"]["control_recovery"] == (
        CameraControlRecoveryPolicy().config_values()
    )
    assert "CameraControlRecoveryPolicy().config_values()" in wizard
    assert '"tracker.camera_control_recovery"' in spec
    assert '"tracker.camera_control_recovery_runtime"' in spec
    assert "two seconds of continuous evidence" in docs
    assert "restarts camera-quality warm-up" in docs
