from __future__ import annotations

from collections import deque
import queue
import threading
import time

import pytest

from tracker.latest_frame_capture import (
    LatestFrameCapture,
    LatestFrameCapturePolicy,
    parse_latest_frame_capture_policy,
    wrap_latest_frame_capture,
)


_STOP = object()


class ControlledCapture:
    def __init__(self) -> None:
        self._results: queue.Queue[object] = queue.Queue()
        self.released = False
        self.release_count = 0
        self.properties: dict[int, float] = {3: 640.0}
        self.read_count = 0

    def push(self, ok: bool, frame: object | None) -> None:
        self.push_outcome((ok, frame))

    def push_outcome(self, outcome: object) -> None:
        self._results.put(outcome)

    def read(self):
        self.read_count += 1
        result = self._results.get(timeout=2.0)
        if result is _STOP:
            return False, None
        if isinstance(result, BaseException):
            raise result
        return result

    def release(self) -> None:
        self.release_count += 1
        self.released = True
        self._results.put(_STOP)

    def isOpened(self) -> bool:
        return not self.released

    def get(self, property_id: int) -> float:
        return self.properties.get(property_id, 0.0)

    def set(self, property_id: int, value: float) -> bool:
        self.properties[property_id] = float(value)
        return True

    def getBackendName(self) -> str:
        return "CONTROLLED"


class SequenceClock:
    def __init__(self, *values: int) -> None:
        self._values = deque(values)
        self._last = values[-1] if values else 1
        self._lock = threading.Lock()

    def __call__(self) -> int:
        with self._lock:
            if self._values:
                self._last = self._values.popleft()
            return self._last


def _policy(**overrides) -> LatestFrameCapturePolicy:
    values = {
        "enabled": True,
        "wait_timeout_ms": 100,
        "failure_backoff_ms": 0,
        "shutdown_timeout_ms": 500,
    }
    values.update(overrides)
    return LatestFrameCapturePolicy(**values)


def _wait_until(predicate, timeout_s: float = 1.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("condition was not reached before timeout")


def test_slow_consumer_receives_only_the_newest_completed_frame():
    capture = ControlledCapture()
    latest = LatestFrameCapture(
        capture,
        _policy(),
        clock=SequenceClock(100, 133, 166),
    )
    try:
        capture.push(True, "frame-1")
        capture.push(True, "frame-2")
        capture.push(True, "frame-3")
        _wait_until(lambda: latest.snapshot().captured_frame_count == 3)

        ok, frame, timestamp_ms = latest.read_with_timestamp()

        assert ok
        assert frame == "frame-3"
        assert timestamp_ms == 166
        snapshot = latest.snapshot()
        assert snapshot.captured_frame_count == 3
        assert snapshot.delivered_frame_count == 1
        assert snapshot.superseded_frame_count == 2
        assert snapshot.latest_generation == 3
        assert snapshot.delivered_generation == 3
        assert snapshot.last_delivered_capture_timestamp_ms == 166
    finally:
        latest.release()


def test_capture_timestamp_is_recorded_when_worker_receives_the_frame():
    capture = ControlledCapture()
    latest = LatestFrameCapture(
        capture,
        _policy(),
        clock=SequenceClock(4242),
    )
    try:
        capture.push(True, object())
        _wait_until(lambda: latest.snapshot().captured_frame_count == 1)
        time.sleep(0.01)

        ok, _frame, timestamp_ms = latest.read_with_timestamp()

        assert ok
        assert timestamp_ms == 4242
        snapshot = latest.snapshot()
        assert snapshot.last_capture_timestamp_ms == 4242
        assert snapshot.last_delivered_capture_timestamp_ms == 4242
    finally:
        latest.release()


def test_same_frame_generation_is_never_delivered_twice():
    capture = ControlledCapture()
    latest = LatestFrameCapture(
        capture,
        _policy(wait_timeout_ms=15),
        clock=SequenceClock(1000, 1010),
    )
    try:
        capture.push(True, "one")
        assert latest.read_with_timestamp() == (True, "one", 1000)

        ok, frame, _timestamp_ms = latest.read_with_timestamp()

        assert not ok
        assert frame is None
        assert latest.snapshot().read_timeout_count == 1
    finally:
        latest.release()


def test_failure_and_recovery_are_delivered_as_distinct_generations():
    capture = ControlledCapture()
    latest = LatestFrameCapture(
        capture,
        _policy(),
        clock=SequenceClock(1000, 1020),
    )
    try:
        capture.push(False, object())
        assert latest.read_with_timestamp() == (False, None, 1000)

        capture.push(True, "recovered")
        assert latest.read_with_timestamp() == (True, "recovered", 1020)

        snapshot = latest.snapshot()
        assert snapshot.capture_failure_count == 1
        assert snapshot.captured_frame_count == 1
        assert snapshot.delivered_frame_count == 1
    finally:
        latest.release()


def test_malformed_or_throwing_backend_read_becomes_failure_event():
    capture = ControlledCapture()
    latest = LatestFrameCapture(
        capture,
        _policy(failure_backoff_ms=1),
        clock=SequenceClock(1000, 1020, 1040),
    )
    try:
        capture.push_outcome(RuntimeError("driver"))
        first = latest.read_with_timestamp()

        capture.push_outcome((True,))
        second = latest.read_with_timestamp()

        capture.push(True, "good")
        third = latest.read_with_timestamp()

        assert first == (False, None, 1000)
        assert second == (False, None, 1020)
        assert third == (True, "good", 1040)
        assert latest.snapshot().capture_failure_count == 2
    finally:
        latest.release()


def test_release_unblocks_a_worker_waiting_inside_native_read():
    capture = ControlledCapture()
    latest = LatestFrameCapture(capture, _policy())
    _wait_until(lambda: capture.read_count >= 1)

    latest.release()

    snapshot = latest.snapshot()
    assert capture.released
    assert capture.release_count == 1
    assert snapshot.released
    assert not snapshot.worker_alive

    latest.release()
    assert capture.release_count == 1


def test_read_after_release_fails_without_touching_native_capture():
    capture = ControlledCapture()
    latest = LatestFrameCapture(capture, _policy())
    latest.release()
    read_count = capture.read_count

    ok, frame, _timestamp_ms = latest.read_with_timestamp()

    assert not ok
    assert frame is None
    assert capture.read_count == read_count


def test_capture_properties_are_proxied_and_contained():
    class ImmediateFailureCapture(ControlledCapture):
        def read(self):
            time.sleep(0.002)
            return False, None

    capture = ImmediateFailureCapture()
    latest = LatestFrameCapture(
        capture,
        _policy(failure_backoff_ms=2),
    )
    try:
        assert latest.isOpened()
        assert latest.get(3) == pytest.approx(640.0)
        assert latest.set(3, 1280.0)
        assert latest.get(3) == pytest.approx(1280.0)
        assert latest.getBackendName() == "CONTROLLED"
    finally:
        latest.release()


def test_control_call_times_out_behind_a_stuck_native_read():
    class StuckCapture(ControlledCapture):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.unblock = threading.Event()

        def read(self):
            self.read_count += 1
            self.started.set()
            self.unblock.wait(2.0)
            return False, None

        def release(self) -> None:
            self.release_count += 1
            self.released = True
            self.unblock.set()

    capture = StuckCapture()
    latest = LatestFrameCapture(
        capture,
        _policy(wait_timeout_ms=20, shutdown_timeout_ms=200),
    )
    try:
        assert capture.started.wait(0.5)
        started = time.monotonic()

        result = latest.get(3)
        elapsed = time.monotonic() - started

        assert result == 0.0
        assert elapsed < 0.20
        assert latest.snapshot().last_error == "get:TimeoutError"
    finally:
        latest.release()
    assert not latest.snapshot().worker_alive


def test_wrap_is_idempotent_and_respects_disabled_policy():
    capture = ControlledCapture()
    disabled = LatestFrameCapturePolicy(enabled=False)

    assert wrap_latest_frame_capture(capture, disabled) is capture

    wrapped = wrap_latest_frame_capture(capture, _policy())
    try:
        assert isinstance(wrapped, LatestFrameCapture)
        assert wrap_latest_frame_capture(wrapped, _policy()) is wrapped
    finally:
        wrapped.release()


def test_policy_parser_accepts_explicit_values_and_boolean_strings():
    policy = parse_latest_frame_capture_policy(
        {
            "latest_frame": {
                "enabled": "false",
                "wait_timeout_ms": "750",
                "failure_backoff_ms": "25",
                "shutdown_timeout_ms": "900",
            }
        }
    )

    assert policy == LatestFrameCapturePolicy(
        enabled=False,
        wait_timeout_ms=750,
        failure_backoff_ms=25,
        shutdown_timeout_ms=900,
    )


@pytest.mark.parametrize(
    "latest_frame",
    [
        [],
        {"enabled": "maybe"},
        {"wait_timeout_ms": 0},
        {"failure_backoff_ms": -1},
        {"shutdown_timeout_ms": -1},
    ],
)
def test_invalid_policy_falls_back_atomically(latest_frame):
    logs: list[str] = []

    policy = parse_latest_frame_capture_policy(
        {"latest_frame": latest_frame},
        logger=logs.append,
    )

    assert policy == LatestFrameCapturePolicy()
    assert len(logs) == 1
    assert "using safe defaults" in logs[0]
