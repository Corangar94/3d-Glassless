from __future__ import annotations

import threading
import time

from tracker.latest_frame_capture import (
    LatestFrameCapture,
    LatestFrameCapturePolicy,
)


class _FatalCapture:
    def __init__(self) -> None:
        self.release_count = 0

    def read(self):
        raise SystemExit("fatal worker boundary")

    def release(self) -> None:
        self.release_count += 1


class _OneFrameThenFatalCapture:
    def __init__(self) -> None:
        self.read_count = 0
        self.release_count = 0

    def read(self):
        self.read_count += 1
        if self.read_count == 1:
            return True, "last-good-frame"
        raise SystemExit("fatal after frame")

    def release(self) -> None:
        self.release_count += 1


class _BlockingCapture:
    def __init__(self) -> None:
        self.read_started = threading.Event()
        self.unblock = threading.Event()
        self.release_count = 0

    def read(self):
        self.read_started.set()
        self.unblock.wait(2.0)
        return False, None

    def release(self) -> None:
        self.release_count += 1
        self.unblock.set()


def _policy(*, wait_timeout_ms: int = 1_000) -> LatestFrameCapturePolicy:
    return LatestFrameCapturePolicy(
        wait_timeout_ms=wait_timeout_ms,
        failure_backoff_ms=0,
        shutdown_timeout_ms=200,
        max_frame_age_ms=0,
    )


def _wait_until(predicate, timeout_s: float = 0.5) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("condition was not reached before timeout")


def test_fatal_worker_failure_wakes_consumer_before_read_timeout():
    capture = _FatalCapture()
    wrapper = LatestFrameCapture(capture, _policy(wait_timeout_ms=1_000))
    try:
        started = time.monotonic()

        ok, frame, _timestamp_ms = wrapper.read_with_timestamp()
        elapsed = time.monotonic() - started

        assert not ok
        assert frame is None
        assert elapsed < 0.25
        snapshot = wrapper.snapshot()
        assert snapshot.worker_failed
        assert snapshot.worker_failure_count == 1
        assert snapshot.read_timeout_count == 0
        assert snapshot.capture_failure_count == 0
        assert snapshot.last_error == "worker:SystemExit"
        assert "worker:SystemExit" in wrapper.failure_summary
    finally:
        wrapper.release()
    assert capture.release_count == 1


def test_silent_unexpected_loop_exit_is_reported_without_exception():
    class _SilentExitWrapper(LatestFrameCapture):
        def _wait_for_control_calls(self) -> bool:
            return False

    capture = _FatalCapture()
    wrapper = _SilentExitWrapper(capture, _policy())
    try:
        _wait_until(lambda: wrapper.snapshot().worker_failed)

        ok, frame, _timestamp_ms = wrapper.read_with_timestamp()

        assert not ok
        assert frame is None
        snapshot = wrapper.snapshot()
        assert snapshot.worker_failure_count == 1
        assert snapshot.last_error == "worker:UnexpectedExit"
        assert snapshot.read_timeout_count == 0
    finally:
        wrapper.release()


def test_last_published_frame_is_delivered_before_worker_failure_signal():
    capture = _OneFrameThenFatalCapture()
    wrapper = LatestFrameCapture(capture, _policy())
    try:
        _wait_until(
            lambda: (
                wrapper.snapshot().captured_frame_count == 1
                and wrapper.snapshot().worker_failed
            )
        )

        first = wrapper.read_with_timestamp()
        second = wrapper.read_with_timestamp()

        assert first[0]
        assert first[1] == "last-good-frame"
        assert not second[0]
        assert second[1] is None
        snapshot = wrapper.snapshot()
        assert snapshot.delivered_frame_count == 1
        assert snapshot.worker_failure_count == 1
        assert snapshot.read_timeout_count == 0
    finally:
        wrapper.release()


def test_repeated_reads_after_worker_failure_remain_immediate_and_bounded():
    capture = _FatalCapture()
    wrapper = LatestFrameCapture(capture, _policy(wait_timeout_ms=1_000))
    try:
        _wait_until(lambda: wrapper.worker_failed)
        started = time.monotonic()

        results = [wrapper.read_with_timestamp() for _ in range(4)]
        elapsed = time.monotonic() - started

        assert all(not result[0] and result[1] is None for result in results)
        assert elapsed < 0.25
        snapshot = wrapper.snapshot()
        assert snapshot.worker_failure_count == 1
        assert snapshot.read_timeout_count == 0
    finally:
        wrapper.release()


def test_normal_release_does_not_record_worker_failure():
    capture = _BlockingCapture()
    wrapper = LatestFrameCapture(capture, _policy())
    assert capture.read_started.wait(0.5)

    wrapper.release()

    snapshot = wrapper.snapshot()
    assert snapshot.released
    assert not snapshot.worker_alive
    assert not snapshot.worker_failed
    assert snapshot.worker_failure_count == 0
    assert capture.release_count == 1


def test_snapshot_defaults_preserve_direct_constructor_compatibility():
    from tracker.latest_frame_capture import LatestFrameCaptureSnapshot

    snapshot = LatestFrameCaptureSnapshot(
        captured_frame_count=0,
        delivered_frame_count=0,
        superseded_frame_count=0,
        capture_failure_count=0,
        read_timeout_count=0,
        latest_generation=0,
        delivered_generation=0,
        last_capture_timestamp_ms=None,
        last_delivered_capture_timestamp_ms=None,
        worker_alive=False,
        released=False,
        last_error="",
    )

    assert snapshot.worker_failure_count == 0
    assert not snapshot.worker_failed
