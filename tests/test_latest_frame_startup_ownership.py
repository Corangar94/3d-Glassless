from __future__ import annotations

import gc

import pytest

from tracker import latest_frame_runtime, main as tracker_main
from tracker.latest_frame_capture import (
    LatestFrameCapture,
    LatestFrameCapturePolicy,
)
from tracker.latest_frame_runtime import LatestFrameTrackingLoop


class _Capture:
    def __init__(self) -> None:
        self.release_count = 0

    def read(self):
        return False, None

    def release(self) -> None:
        self.release_count += 1


class _StartFailureThread:
    ident = None

    def start(self) -> None:
        raise RuntimeError("thread start failed")

    def is_alive(self) -> bool:
        return False

    def join(self, _timeout: float | None = None) -> None:
        raise AssertionError("an unstarted thread must not be joined")


class _SuccessfulFakeThread:
    def __init__(self) -> None:
        self.ident = None
        self.started = False
        self.join_count = 0

    def start(self) -> None:
        self.ident = 123
        self.started = True

    def is_alive(self) -> bool:
        return False

    def join(self, _timeout: float | None = None) -> None:
        self.join_count += 1


def _policy() -> LatestFrameCapturePolicy:
    return LatestFrameCapturePolicy(
        wait_timeout_ms=20,
        failure_backoff_ms=0,
        shutdown_timeout_ms=20,
    )


def test_thread_factory_failure_never_releases_callers_capture():
    capture = _Capture()

    def fail_factory(**_kwargs):
        raise RuntimeError("thread allocation failed")

    with pytest.raises(RuntimeError, match="allocation failed"):
        LatestFrameCapture(
            capture,
            _policy(),
            thread_factory=fail_factory,
        )

    gc.collect()
    assert capture.release_count == 0


def test_thread_start_failure_never_releases_callers_capture():
    capture = _Capture()
    created: list[_StartFailureThread] = []

    def factory(**_kwargs):
        thread = _StartFailureThread()
        created.append(thread)
        return thread

    with pytest.raises(RuntimeError, match="thread start failed"):
        LatestFrameCapture(
            capture,
            _policy(),
            thread_factory=factory,
        )

    gc.collect()
    assert len(created) == 1
    assert capture.release_count == 0


def test_successful_start_transfers_ownership_and_release_once():
    capture = _Capture()
    thread = _SuccessfulFakeThread()
    wrapper = LatestFrameCapture(
        capture,
        _policy(),
        thread_factory=lambda **_kwargs: thread,
    )

    assert thread.started
    assert wrapper.owns_capture
    assert not wrapper.snapshot().worker_alive

    wrapper.release()
    wrapper.release()

    assert not wrapper.owns_capture
    assert capture.release_count == 1
    assert thread.join_count >= 1


def test_runtime_fallback_keeps_raw_capture_after_start_failure(monkeypatch):
    capture = _Capture()
    loop = LatestFrameTrackingLoop.__new__(LatestFrameTrackingLoop)
    loop._latest_frame_capture_policy = _policy()
    loop._active_latest_frame_capture = None
    loop._last_latest_frame_snapshot = None
    logs: list[str] = []

    monkeypatch.setattr(
        tracker_main.TrackingLoop,
        "_open_camera_with_recovery",
        lambda *_args, **_kwargs: (capture, 2),
    )

    def failing_wrap(raw_capture, policy):
        return LatestFrameCapture(
            raw_capture,
            policy,
            thread_factory=lambda **_kwargs: _StartFailureThread(),
        )

    monkeypatch.setattr(
        latest_frame_runtime,
        "wrap_latest_frame_capture",
        failing_wrap,
    )
    monkeypatch.setattr(
        latest_frame_runtime,
        "print",
        logs.append,
        raising=False,
    )

    returned, backend_index = loop._open_camera_with_recovery(
        0,
        1280,
        720,
        30.0,
        backend_start_index=0,
    )
    gc.collect()

    assert returned is capture
    assert backend_index == 2
    assert capture.release_count == 0
    assert loop._active_latest_frame_capture is None
    assert any("using synchronous reads" in message for message in logs)


def test_snapshot_and_release_tolerate_missing_worker():
    capture = _Capture()
    wrapper = LatestFrameCapture.__new__(LatestFrameCapture)
    wrapper._capture = capture
    wrapper._policy = _policy()
    wrapper._clock = lambda: 1
    wrapper._steady_clock = lambda: 1.0
    wrapper._condition = __import__("threading").Condition()
    wrapper._io_lock = __import__("threading").Lock()
    wrapper._release_lock = __import__("threading").Lock()
    wrapper._stop_event = __import__("threading").Event()
    wrapper._released = False
    wrapper._underlying_released = False
    wrapper._owns_capture = False
    wrapper._worker = None
    wrapper._control_waiters = 0
    wrapper._generation = 0
    wrapper._delivered_generation = 0
    wrapper._event = None
    wrapper._captured_frame_count = 0
    wrapper._delivered_frame_count = 0
    wrapper._superseded_frame_count = 0
    wrapper._capture_failure_count = 0
    wrapper._read_timeout_count = 0
    wrapper._stale_frame_drop_count = 0
    wrapper._last_capture_timestamp_ms = None
    wrapper._last_delivered_capture_timestamp_ms = None
    wrapper._last_stale_frame_age_ms = None
    wrapper._last_error = ""

    snapshot = wrapper.snapshot()
    wrapper.release()

    assert not snapshot.worker_alive
    assert snapshot.stale_frame_drop_count == 0
    assert snapshot.last_stale_frame_age_ms is None
    assert wrapper.snapshot().released
    assert capture.release_count == 0
