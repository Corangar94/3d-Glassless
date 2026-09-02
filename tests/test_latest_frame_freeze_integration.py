from __future__ import annotations

from collections import deque
import queue
import threading

import numpy as np

from tracker.latest_frame_capture import (
    LatestFrameCapture,
    LatestFrameCapturePolicy,
    LatestFrameCaptureSnapshot,
)


_STOP = object()


class _ControlledCapture:
    def __init__(self) -> None:
        self._results: queue.Queue[object] = queue.Queue()
        self.release_count = 0

    def push(self, ok: bool, frame: object | None) -> None:
        self._results.put((ok, frame))

    def read(self):
        result = self._results.get(timeout=2.0)
        return (False, None) if result is _STOP else result

    def release(self) -> None:
        self.release_count += 1
        self._results.put(_STOP)


class _MutableSteadyClock:
    def __init__(self, value: float = 0.0) -> None:
        self._value = value
        self._lock = threading.Lock()

    def set(self, value: float) -> None:
        with self._lock:
            self._value = value

    def __call__(self) -> float:
        with self._lock:
            return self._value


class _SequenceClock:
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
        "wait_timeout_ms": 200,
        "failure_backoff_ms": 0,
        "shutdown_timeout_ms": 200,
        "max_frame_age_ms": 0,
        "freeze_check_interval_ms": 250,
        "freeze_timeout_ms": 500,
    }
    values.update(overrides)
    return LatestFrameCapturePolicy(**values)


def _still(value: int = 5) -> np.ndarray:
    return np.full((4, 6, 3), value, dtype=np.uint8)


def _read_at(
    wrapper: LatestFrameCapture,
    capture: _ControlledCapture,
    steady: _MutableSteadyClock,
    observed_at_s: float,
    frame: object | None,
    *,
    ok: bool = True,
):
    steady.set(observed_at_s)
    capture.push(ok, frame)
    return wrapper.read_with_timestamp()


def test_sustained_identical_success_becomes_reconnect_failure():
    capture = _ControlledCapture()
    steady = _MutableSteadyClock()
    wrapper = LatestFrameCapture(
        capture,
        _policy(),
        clock=_SequenceClock(1000, 1250, 1500, 1600, 1750),
        steady_clock=steady,
    )
    image = _still()
    try:
        assert _read_at(wrapper, capture, steady, 0.0, image)[0]
        assert _read_at(wrapper, capture, steady, 0.25, image)[0]
        frozen = _read_at(wrapper, capture, steady, 0.50, image)

        assert frozen[:2] == (False, None)
        snapshot = wrapper.snapshot()
        assert snapshot.capture_failure_count == 1
        assert snapshot.frozen_frame_failure_count == 1
        assert snapshot.freeze_episode_count == 1
        assert snapshot.last_frozen_frame_age_ms == 500
        assert "read:FrozenFrame(500ms)" in wrapper.failure_summary

        # Once established, the freeze remains a failure between fingerprint
        # samples, allowing the existing repeated-read reopen threshold to fire.
        again = _read_at(wrapper, capture, steady, 0.60, image)
        assert again[:2] == (False, None)
        assert wrapper.snapshot().frozen_frame_failure_count == 2

        # A changed sampled buffer clears the episode immediately.
        recovered = _read_at(wrapper, capture, steady, 0.75, _still(6))
        assert recovered[0]
        assert recovered[1] is not None
        assert wrapper.snapshot().freeze_episode_count == 1
    finally:
        wrapper.release()
    assert capture.release_count == 1


def test_native_read_failure_breaks_identical_success_episode():
    capture = _ControlledCapture()
    steady = _MutableSteadyClock()
    wrapper = LatestFrameCapture(
        capture,
        _policy(freeze_timeout_ms=500),
        clock=_SequenceClock(1000, 1250, 1500, 1750),
        steady_clock=steady,
    )
    image = _still()
    try:
        assert _read_at(wrapper, capture, steady, 0.0, image)[0]
        assert _read_at(
            wrapper,
            capture,
            steady,
            0.25,
            None,
            ok=False,
        )[:2] == (False, None)
        assert _read_at(wrapper, capture, steady, 0.50, image)[0]
        assert _read_at(wrapper, capture, steady, 0.75, image)[0]
        assert wrapper.snapshot().freeze_episode_count == 0
    finally:
        wrapper.release()


def test_disabled_freeze_gate_preserves_identical_frames():
    capture = _ControlledCapture()
    steady = _MutableSteadyClock()
    wrapper = LatestFrameCapture(
        capture,
        _policy(freeze_timeout_ms=0),
        clock=_SequenceClock(1000, 5000),
        steady_clock=steady,
    )
    image = _still()
    try:
        assert _read_at(wrapper, capture, steady, 0.0, image)[0]
        assert _read_at(wrapper, capture, steady, 100.0, image)[0]
        snapshot = wrapper.snapshot()
        assert snapshot.frozen_frame_failure_count == 0
        assert snapshot.freeze_episode_count == 0
    finally:
        wrapper.release()


def test_snapshot_defaults_preserve_existing_direct_construction():
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

    assert snapshot.frozen_frame_failure_count == 0
    assert snapshot.freeze_episode_count == 0
    assert snapshot.last_frozen_frame_age_ms is None
