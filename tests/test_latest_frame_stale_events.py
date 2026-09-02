from __future__ import annotations

import math
import queue
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

    def read(self):
        return False, None

    def release(self) -> None:
        self.release_count += 1


class _ManualSteadyClock:
    def __init__(self, value: float) -> None:
        self.value = float(value)

    def __call__(self) -> float:
        return self.value


def _wrapper(
    steady_clock: _ManualSteadyClock,
    *,
    max_frame_age_ms: int = 250,
    wait_timeout_ms: int = 200,
) -> LatestFrameCapture:
    return LatestFrameCapture(
        _Capture(),
        LatestFrameCapturePolicy(
            wait_timeout_ms=wait_timeout_ms,
            failure_backoff_ms=0,
            shutdown_timeout_ms=20,
            max_frame_age_ms=max_frame_age_ms,
        ),
        clock=lambda: 9999,
        steady_clock=steady_clock,
        thread_factory=lambda **_kwargs: _StoppedThread(),
    )


def _wait_until(predicate, timeout_s: float = 0.5) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.001)
    raise AssertionError("condition was not reached before timeout")


def test_success_frame_at_exact_age_limit_is_delivered():
    steady = _ManualSteadyClock(10.0)
    wrapper = _wrapper(steady)
    try:
        wrapper._publish_event(
            True,
            "boundary-frame",
            1234,
            9.75,
        )

        result = wrapper.read_with_timestamp()

        assert result == (True, "boundary-frame", 1234)
        snapshot = wrapper.snapshot()
        assert snapshot.delivered_frame_count == 1
        assert snapshot.stale_frame_drop_count == 0
        assert snapshot.last_stale_frame_age_ms is None
    finally:
        wrapper.release()


def test_stale_success_is_retired_then_consumer_waits_for_fresh_generation():
    steady = _ManualSteadyClock(10.0)
    wrapper = _wrapper(steady)
    results: queue.Queue[tuple[bool, object | None, int]] = queue.Queue()
    try:
        wrapper._publish_event(True, "stale", 1000, 9.0)
        consumer = threading.Thread(
            target=lambda: results.put(wrapper.read_with_timestamp())
        )
        consumer.start()

        _wait_until(
            lambda: wrapper.snapshot().stale_frame_drop_count == 1
        )
        stale_snapshot = wrapper.snapshot()
        assert stale_snapshot.delivered_frame_count == 0
        assert stale_snapshot.delivered_generation == 1
        assert stale_snapshot.last_stale_frame_age_ms == 1000
        assert "StaleFrame(1000ms)" in wrapper.failure_summary

        wrapper._publish_event(True, "fresh", 2000, 10.0)
        consumer.join(0.5)

        assert not consumer.is_alive()
        assert results.get_nowait() == (True, "fresh", 2000)
        snapshot = wrapper.snapshot()
        assert snapshot.delivered_generation == 2
        assert snapshot.delivered_frame_count == 1
        assert snapshot.stale_frame_drop_count == 1
        assert snapshot.read_timeout_count == 0
    finally:
        wrapper.release()


def test_old_failure_event_is_preserved_for_reconnect_accounting():
    steady = _ManualSteadyClock(100.0)
    wrapper = _wrapper(steady)
    try:
        wrapper._publish_event(
            False,
            None,
            3000,
            1.0,
            error_text="read:DriverFailure",
        )

        result = wrapper.read_with_timestamp()

        assert result == (False, None, 3000)
        snapshot = wrapper.snapshot()
        assert snapshot.capture_failure_count == 1
        assert snapshot.stale_frame_drop_count == 0
        assert snapshot.delivered_generation == 1
    finally:
        wrapper.release()


def test_zero_age_limit_disables_early_retirement():
    steady = _ManualSteadyClock(100.0)
    wrapper = _wrapper(steady, max_frame_age_ms=0)
    try:
        wrapper._publish_event(True, "old-but-enabled", 4000, 1.0)

        assert wrapper.read_with_timestamp() == (
            True,
            "old-but-enabled",
            4000,
        )
        assert wrapper.snapshot().stale_frame_drop_count == 0
    finally:
        wrapper.release()


def test_future_steady_timestamp_is_unknown_not_falsely_stale():
    steady = _ManualSteadyClock(10.0)
    wrapper = _wrapper(steady)
    try:
        wrapper._publish_event(True, "future", 5000, 11.0)

        assert wrapper.read_with_timestamp() == (True, "future", 5000)
        assert wrapper.snapshot().stale_frame_drop_count == 0
    finally:
        wrapper.release()


def test_nonfinite_steady_observation_is_unknown_not_falsely_stale():
    steady = _ManualSteadyClock(math.nan)
    wrapper = _wrapper(steady)
    try:
        wrapper._publish_event(True, "unknown-age", 6000, 1.0)

        assert wrapper.read_with_timestamp() == (
            True,
            "unknown-age",
            6000,
        )
        assert wrapper.snapshot().stale_frame_drop_count == 0
    finally:
        wrapper.release()


def test_stale_event_is_retired_once_before_timeout():
    steady = _ManualSteadyClock(10.0)
    wrapper = _wrapper(steady, wait_timeout_ms=15)
    try:
        wrapper._publish_event(True, "stale", 7000, 9.0)

        ok, frame, _timestamp_ms = wrapper.read_with_timestamp()

        assert not ok
        assert frame is None
        snapshot = wrapper.snapshot()
        assert snapshot.stale_frame_drop_count == 1
        assert snapshot.read_timeout_count == 1
        assert snapshot.delivered_generation == 1

        wrapper.read_with_timestamp()
        assert wrapper.snapshot().stale_frame_drop_count == 1
    finally:
        wrapper.release()
