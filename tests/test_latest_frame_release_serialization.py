from __future__ import annotations

import threading
import time

from tracker.latest_frame_capture import (
    LatestFrameCapture,
    LatestFrameCapturePolicy,
)


class _StoppedThread:
    ident = 123

    def start(self) -> None:
        pass

    def is_alive(self) -> bool:
        return False

    def join(self, _timeout: float | None = None) -> None:
        pass


class _Capture:
    def __init__(self) -> None:
        self.release_count = 0
        self.io_lock: threading.Lock | None = None
        self.release_saw_lock = False
        self.events: list[str] = []

    def read(self):
        return False, None

    def release(self) -> None:
        self.release_count += 1
        self.release_saw_lock = bool(
            self.io_lock is not None and self.io_lock.locked()
        )
        self.events.append("release")


def _wrapper(
    capture: _Capture,
    *,
    shutdown_timeout_ms: int = 200,
) -> LatestFrameCapture:
    wrapper = LatestFrameCapture(
        capture,
        LatestFrameCapturePolicy(
            wait_timeout_ms=50,
            failure_backoff_ms=0,
            shutdown_timeout_ms=shutdown_timeout_ms,
        ),
        thread_factory=lambda **_kwargs: _StoppedThread(),
    )
    capture.io_lock = wrapper._io_lock
    return wrapper


def test_normal_release_holds_camera_io_lock():
    capture = _Capture()
    wrapper = _wrapper(capture)

    wrapper.release()

    assert capture.release_count == 1
    assert capture.release_saw_lock
    assert not wrapper.owns_capture


def test_release_waits_for_short_inflight_control_call():
    control_started = threading.Event()
    allow_control_finish = threading.Event()

    class ControlledCapture(_Capture):
        def get(self, _property_id: int) -> float:
            self.events.append("get-start")
            control_started.set()
            assert allow_control_finish.wait(0.5)
            self.events.append("get-end")
            return 42.0

    capture = ControlledCapture()
    wrapper = _wrapper(capture, shutdown_timeout_ms=400)
    control = threading.Thread(target=lambda: wrapper.get(1))
    control.start()
    assert control_started.wait(0.5)

    timer = threading.Timer(0.03, allow_control_finish.set)
    timer.start()
    wrapper.release()
    control.join(0.5)
    timer.join(0.5)

    assert not control.is_alive()
    assert capture.release_count == 1
    assert capture.release_saw_lock
    assert capture.events == ["get-start", "get-end", "release"]


def test_stuck_read_uses_bounded_unlocked_release_escape():
    capture = _Capture()
    wrapper = _wrapper(capture, shutdown_timeout_ms=40)
    lock_acquired = threading.Event()
    unblock_holder = threading.Event()

    def hold_io_lock() -> None:
        with wrapper._io_lock:
            lock_acquired.set()
            unblock_holder.wait(0.5)

    holder = threading.Thread(target=hold_io_lock)
    holder.start()
    assert lock_acquired.wait(0.5)

    started = time.monotonic()
    wrapper.release()
    elapsed = time.monotonic() - started

    assert elapsed < 0.25
    assert capture.release_count == 1
    assert not capture.release_saw_lock
    assert not wrapper.owns_capture

    unblock_holder.set()
    holder.join(0.5)
    assert not holder.is_alive()


def test_release_remains_idempotent_after_serialized_teardown():
    capture = _Capture()
    wrapper = _wrapper(capture)

    wrapper.release()
    wrapper.release()

    assert capture.release_count == 1
