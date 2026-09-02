from __future__ import annotations

from tracker import latest_frame_runtime, main as tracker_main
from tracker.latest_frame_capture import LatestFrameCapturePolicy
from tracker.latest_frame_runtime import LatestFrameTrackingLoop


class _Capture:
    def read(self):
        return False, None

    def release(self) -> None:
        pass


class _PreviousCapture:
    def __init__(self, snapshot_value) -> None:
        self.snapshot_value = snapshot_value
        self.snapshot_calls = 0

    def snapshot(self):
        self.snapshot_calls += 1
        return self.snapshot_value


def _bare_loop(policy: LatestFrameCapturePolicy) -> LatestFrameTrackingLoop:
    loop = LatestFrameTrackingLoop.__new__(LatestFrameTrackingLoop)
    loop._latest_frame_capture_policy = policy
    loop._active_latest_frame_capture = None
    loop._last_latest_frame_snapshot = None
    return loop


def test_wrapper_construction_failure_falls_back_to_synchronous_capture(
    monkeypatch,
):
    capture = _Capture()
    loop = _bare_loop(LatestFrameCapturePolicy())
    logs: list[str] = []
    monkeypatch.setattr(
        tracker_main.TrackingLoop,
        "_open_camera_with_recovery",
        lambda *_args, **_kwargs: (capture, 2),
    )
    monkeypatch.setattr(
        latest_frame_runtime,
        "wrap_latest_frame_capture",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("thread unavailable")
        ),
    )
    monkeypatch.setattr(
        latest_frame_runtime,
        "print",
        logs.append,
        raising=False,
    )

    result, backend_index = loop._open_camera_with_recovery(
        0,
        1280,
        720,
        30.0,
        backend_start_index=0,
    )

    assert result is capture
    assert backend_index == 2
    assert loop._active_latest_frame_capture is None
    assert any("using synchronous reads" in message for message in logs)


def test_retired_capture_snapshot_is_preserved_when_camera_open_stops(
    monkeypatch,
):
    previous = _PreviousCapture("final snapshot")
    loop = _bare_loop(LatestFrameCapturePolicy())
    loop._active_latest_frame_capture = previous
    monkeypatch.setattr(
        tracker_main.TrackingLoop,
        "_open_camera_with_recovery",
        lambda *_args, **_kwargs: (None, 1),
    )

    capture, backend_index = loop._open_camera_with_recovery(
        0,
        0,
        0,
        0.0,
        backend_start_index=1,
    )

    assert capture is None
    assert backend_index == 1
    assert loop._active_latest_frame_capture is None
    assert loop.last_latest_frame_snapshot == "final snapshot"
    assert previous.snapshot_calls == 1


def test_disabled_policy_retires_previous_snapshot_before_raw_reopen(
    monkeypatch,
):
    previous = _PreviousCapture("retired")
    raw = _Capture()
    loop = _bare_loop(LatestFrameCapturePolicy(enabled=False))
    loop._active_latest_frame_capture = previous
    monkeypatch.setattr(
        tracker_main.TrackingLoop,
        "_open_camera_with_recovery",
        lambda *_args, **_kwargs: (raw, 0),
    )

    capture, _backend_index = loop._open_camera_with_recovery(
        0,
        0,
        0,
        0.0,
        backend_start_index=0,
    )

    assert capture is raw
    assert loop._active_latest_frame_capture is None
    assert loop.last_latest_frame_snapshot == "retired"


def test_snapshot_failure_does_not_block_camera_recovery(monkeypatch):
    class BrokenSnapshot:
        def snapshot(self):
            raise RuntimeError("diagnostics unavailable")

    raw = _Capture()
    loop = _bare_loop(LatestFrameCapturePolicy(enabled=False))
    loop._active_latest_frame_capture = BrokenSnapshot()
    monkeypatch.setattr(
        tracker_main.TrackingLoop,
        "_open_camera_with_recovery",
        lambda *_args, **_kwargs: (raw, 0),
    )

    capture, _backend_index = loop._open_camera_with_recovery(
        0,
        0,
        0,
        0.0,
        backend_start_index=0,
    )

    assert capture is raw
    assert loop._active_latest_frame_capture is None
    assert loop.last_latest_frame_snapshot is None
