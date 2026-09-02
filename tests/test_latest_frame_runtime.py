from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from tracker import latest_frame_runtime, main as tracker_main
from tracker.latest_frame_capture import (
    LatestFrameCapture,
    LatestFrameCapturePolicy,
)
from tracker.latest_frame_runtime import (
    LatestFrameTrackingLoop,
    _AcquisitionTimestampQualityMonitor,
    _policy_from_config_path,
)


class _Capture:
    def __init__(self) -> None:
        self.released = False

    def read(self):
        return False, None

    def release(self) -> None:
        self.released = True


class _TimestampCapture:
    last_delivered_capture_timestamp_ms = 1234

    def snapshot(self):
        return "snapshot"


def test_runtime_policy_reads_nested_camera_configuration(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "camera": {
                    "latest_frame": {
                        "enabled": False,
                        "wait_timeout_ms": 750,
                        "failure_backoff_ms": 25,
                        "shutdown_timeout_ms": 900,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    policy = _policy_from_config_path(config_path)

    assert policy.enabled is False
    assert policy.wait_timeout_ms == 750
    assert policy.failure_backoff_ms == 25
    assert policy.shutdown_timeout_ms == 900


def test_missing_runtime_config_uses_safe_defaults(tmp_path, monkeypatch):
    logs: list[str] = []
    missing = tmp_path / "missing.yaml"
    monkeypatch.setattr(
        latest_frame_runtime,
        "print",
        lambda message: logs.append(str(message)),
        raising=False,
    )

    policy = _policy_from_config_path(missing)

    assert policy == LatestFrameCapturePolicy()
    assert any("using safe defaults" in message for message in logs)


def test_recovered_capture_is_wrapped_when_policy_is_enabled(monkeypatch):
    raw = _Capture()
    loop = LatestFrameTrackingLoop.__new__(LatestFrameTrackingLoop)
    loop._latest_frame_capture_policy = LatestFrameCapturePolicy(
        wait_timeout_ms=20,
        failure_backoff_ms=1,
        shutdown_timeout_ms=100,
    )
    loop._active_latest_frame_capture = None
    loop._last_latest_frame_snapshot = None
    monkeypatch.setattr(
        tracker_main.TrackingLoop,
        "_open_camera_with_recovery",
        lambda *_args, **_kwargs: (raw, 2),
    )

    capture, backend_index = loop._open_camera_with_recovery(
        0,
        1280,
        720,
        30.0,
        backend_start_index=1,
    )
    try:
        assert isinstance(capture, LatestFrameCapture)
        assert backend_index == 2
        assert loop._active_latest_frame_capture is capture
        assert capture.native_capture is raw
    finally:
        capture.release()


def test_disabled_policy_preserves_raw_capture(monkeypatch):
    raw = _Capture()
    loop = LatestFrameTrackingLoop.__new__(LatestFrameTrackingLoop)
    loop._latest_frame_capture_policy = LatestFrameCapturePolicy(enabled=False)
    loop._active_latest_frame_capture = None
    loop._last_latest_frame_snapshot = None
    monkeypatch.setattr(
        tracker_main.TrackingLoop,
        "_open_camera_with_recovery",
        lambda *_args, **_kwargs: (raw, 1),
    )

    capture, backend_index = loop._open_camera_with_recovery(
        0,
        0,
        0,
        0.0,
        backend_start_index=0,
    )

    assert capture is raw
    assert backend_index == 1
    assert loop._active_latest_frame_capture is None


def test_tracker_receives_worker_acquisition_timestamp(monkeypatch):
    loop = LatestFrameTrackingLoop.__new__(LatestFrameTrackingLoop)
    loop._active_latest_frame_capture = _TimestampCapture()
    calls: list[tuple[object, int]] = []
    expected = object()
    monkeypatch.setattr(
        tracker_main.TrackingLoop,
        "_process_frame",
        lambda _self, frame, timestamp_ms: (
            calls.append((frame, timestamp_ms)) or expected
        ),
    )
    frame = object()

    result = loop._process_frame(frame, 9999)

    assert result is expected
    assert calls == [(frame, 1234)]


def test_tracker_falls_back_to_processing_timestamp_without_wrapper(monkeypatch):
    loop = LatestFrameTrackingLoop.__new__(LatestFrameTrackingLoop)
    loop._active_latest_frame_capture = None
    calls: list[int] = []
    monkeypatch.setattr(
        tracker_main.TrackingLoop,
        "_process_frame",
        lambda _self, _frame, timestamp_ms: calls.append(timestamp_ms),
    )

    loop._process_frame(object(), 9999)

    assert calls == [9999]


def test_camera_quality_monitor_receives_acquisition_timestamp():
    monitor = MagicMock()
    monitor.update.return_value = "quality"
    owner = MagicMock()
    owner.capture_timestamp_ms.return_value = 1234
    proxy = _AcquisitionTimestampQualityMonitor(monitor, owner)
    frame = object()

    result = proxy.update(frame, 9999)

    assert result == "quality"
    owner.capture_timestamp_ms.assert_called_once_with(9999)
    monitor.update.assert_called_once_with(frame, 1234)
    proxy.reset()
    monitor.reset.assert_called_once_with()


def test_latest_frame_runtime_main_remains_available_for_direct_callers(
    monkeypatch,
):
    original = tracker_main.TrackingLoop
    observed: list[object] = []
    monkeypatch.setattr(
        tracker_main,
        "main",
        lambda: observed.append(tracker_main.TrackingLoop),
    )

    latest_frame_runtime.main()

    assert observed == [LatestFrameTrackingLoop]
    assert tracker_main.TrackingLoop is original


def test_real_entrypoints_select_pose_stability_runtime():
    source_entry = Path("tracker/__main__.py").read_text(encoding="utf-8")
    frozen_entry = Path("launcher/__main__.py").read_text(encoding="utf-8")

    assert "from tracker.pose_stability_runtime import main" in source_entry
    assert "from tracker.pose_stability_runtime import main" in frozen_entry
    assert "from tracker.main import main" not in source_entry


def test_repository_config_and_setup_defaults_match_policy():
    config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    wizard = Path("launcher/wizard.py").read_text(encoding="utf-8")
    expected = LatestFrameCapturePolicy().config_values()

    assert all(
        config["camera"]["latest_frame"][key] == value
        for key, value in expected.items()
    )
    assert (
        '"latest_frame": LatestFrameCapturePolicy().config_values()'
        in wizard
    )


def test_frozen_package_includes_latest_frame_and_stability_modules():
    spec = Path("Glassless3D.spec").read_text(encoding="utf-8")

    assert '"tracker.latest_frame_capture"' in spec
    assert '"tracker.latest_frame_runtime"' in spec
    assert '"tracker.pose_jump_confirmation"' in spec
    assert '"tracker.pose_stability_runtime"' in spec


def test_direct_tracking_loop_class_is_not_replaced_at_import_time():
    assert tracker_main.TrackingLoop is not LatestFrameTrackingLoop
    assert issubclass(LatestFrameTrackingLoop, tracker_main.TrackingLoop)
