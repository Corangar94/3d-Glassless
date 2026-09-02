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
    def __init__(self) -> None:
        self.reset_count = 0

    def reset(self) -> None:
        self.reset_count += 1


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


def test_constructor_wraps_existing_quality_monitor(monkeypatch):
    monitor = MagicMock()

    def fake_super_init(self, *args, **kwargs):
        self._camera_quality_monitor = monitor

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


def test_lock_interceptor_records_result_and_restores_global(monkeypatch):
    capture = object()
    loop = _bare_loop(CameraControlRecoveryPolicy())
    loop._camera_control_recovery_capture = capture
    expected = {
        "autofocus_locked": True,
        "focus_preserved": True,
    }
    original = lambda _capture: expected
    monkeypatch.setattr(tracker_main, "try_lock_camera_controls", original)

    def fake_run(self, *args, **kwargs):
        return tracker_main.try_lock_camera_controls(capture)

    monkeypatch.setattr(StableLatestFrameTrackingLoop, "run", fake_run)

    result = loop.run()

    assert result == expected
    assert loop.camera_control_lock_state == expected
    assert tracker_main.try_lock_camera_controls is original


def test_lock_interceptor_restores_global_when_tracking_raises(monkeypatch):
    loop = _bare_loop(CameraControlRecoveryPolicy())
    original = lambda _capture: {}
    monkeypatch.setattr(tracker_main, "try_lock_camera_controls", original)
    monkeypatch.setattr(
        StableLatestFrameTrackingLoop,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("tracking failed")
        ),
    )

    with pytest.raises(RuntimeError, match="tracking failed"):
        loop.run()

    assert tracker_main.try_lock_camera_controls is original


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
