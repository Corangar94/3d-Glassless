"""Packaged/source tracker entrypoint with latest-only camera acquisition."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tracker import main as tracker_main
from tracker.latest_frame_capture import (
    LatestFrameCapture,
    LatestFrameCapturePolicy,
    LatestFrameCaptureSnapshot,
    parse_latest_frame_capture_policy,
    wrap_latest_frame_capture,
)


class _AcquisitionTimestampQualityMonitor:
    """Give camera-quality cadence the worker's acquisition timestamp."""

    def __init__(self, monitor: object, owner: "LatestFrameTrackingLoop") -> None:
        self._monitor = monitor
        self._owner = owner

    def update(self, frame: object, fallback_timestamp_ms: int):
        return self._monitor.update(
            frame,
            self._owner.capture_timestamp_ms(fallback_timestamp_ms),
        )

    def reset(self) -> None:
        self._monitor.reset()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._monitor, name)


def _policy_from_config_path(
    config_path: object,
) -> LatestFrameCapturePolicy:
    if not config_path:
        return LatestFrameCapturePolicy()
    try:
        with Path(str(config_path)).open(encoding="utf-8") as config_file:
            loaded = yaml.safe_load(config_file)
        root = loaded if isinstance(loaded, dict) else {}
        return parse_latest_frame_capture_policy(root.get("camera", {}))
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        print(
            "[G3D] Could not read latest-frame camera settings; "
            "using safe defaults"
        )
        return LatestFrameCapturePolicy()


class LatestFrameTrackingLoop(tracker_main.TrackingLoop):
    """Wrap each recovered camera and pass acquisition time to tracking."""

    def __init__(
        self,
        *args: object,
        latest_frame_capture_policy: LatestFrameCapturePolicy | None = None,
        **kwargs: object,
    ) -> None:
        config_path = kwargs.get("config_path")
        policy = (
            latest_frame_capture_policy
            if latest_frame_capture_policy is not None
            else _policy_from_config_path(config_path)
        )
        self._latest_frame_capture_policy = policy
        self._active_latest_frame_capture: LatestFrameCapture | None = None
        self._last_latest_frame_snapshot: (
            LatestFrameCaptureSnapshot | None
        ) = None
        super().__init__(*args, **kwargs)
        monitor = self._camera_quality_monitor
        if policy.enabled and monitor is not None:
            self._camera_quality_monitor = _AcquisitionTimestampQualityMonitor(
                monitor,
                self,
            )

    @property
    def latest_frame_capture_policy(self) -> LatestFrameCapturePolicy:
        return self._latest_frame_capture_policy

    @property
    def last_latest_frame_snapshot(
        self,
    ) -> LatestFrameCaptureSnapshot | None:
        active = self._active_latest_frame_capture
        return active.snapshot() if active is not None else self._last_latest_frame_snapshot

    def capture_timestamp_ms(self, fallback_timestamp_ms: int) -> int:
        active = self._active_latest_frame_capture
        if active is None:
            return int(fallback_timestamp_ms)
        timestamp = active.last_delivered_capture_timestamp_ms
        return int(fallback_timestamp_ms) if timestamp is None else int(timestamp)

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
        if capture is None or not self._latest_frame_capture_policy.enabled:
            self._active_latest_frame_capture = None
            return capture, backend_index

        wrapped = wrap_latest_frame_capture(
            capture,
            self._latest_frame_capture_policy,
        )
        if isinstance(wrapped, LatestFrameCapture):
            previous = self._active_latest_frame_capture
            if previous is not None and previous is not wrapped:
                self._last_latest_frame_snapshot = previous.snapshot()
            self._active_latest_frame_capture = wrapped
        return wrapped, backend_index

    def _process_frame(
        self,
        frame: object,
        capture_timestamp_ms: int,
    ):
        return super()._process_frame(
            frame,
            self.capture_timestamp_ms(capture_timestamp_ms),
        )


def main() -> None:
    """Run the existing tracker bootstrap with the runtime loop substituted."""
    original_loop = tracker_main.TrackingLoop
    tracker_main.TrackingLoop = LatestFrameTrackingLoop
    try:
        tracker_main.main()
    finally:
        tracker_main.TrackingLoop = original_loop
