from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tracker.frame_processor import (
    FrameCallMode,
    FrameProcessorAdapter,
    resolve_frame_call_mode,
)


def test_current_tracker_receives_capture_timestamp_by_keyword():
    calls: list[tuple[object, int | None]] = []

    class Tracker:
        def process_frame(self, frame, capture_timestamp_ms=None):
            calls.append((frame, capture_timestamp_ms))
            return "pose"

    adapter = FrameProcessorAdapter.from_tracker(Tracker())
    frame = object()

    assert adapter.mode is FrameCallMode.TIMESTAMP_KEYWORD
    assert adapter(frame, 1234) == "pose"
    assert calls == [(frame, 1234)]


def test_keyword_only_timestamp_is_supported():
    class Tracker:
        def process_frame(self, frame, *, capture_timestamp_ms=None):
            return frame, capture_timestamp_ms

    adapter = FrameProcessorAdapter.from_tracker(Tracker())

    assert adapter.mode is FrameCallMode.TIMESTAMP_KEYWORD
    assert adapter("frame", 42) == ("frame", 42)


def test_positional_only_named_timestamp_is_supported():
    class Tracker:
        def process_frame(self, frame, capture_timestamp_ms, /):
            return frame, capture_timestamp_ms

    adapter = FrameProcessorAdapter.from_tracker(Tracker())

    assert adapter.mode is FrameCallMode.TIMESTAMP_POSITIONAL_ONLY
    assert adapter("frame", 77) == ("frame", 77)


def test_legacy_one_argument_tracker_is_called_once_without_timestamp():
    calls: list[object] = []

    class LegacyTracker:
        def process_frame(self, frame):
            calls.append(frame)
            return "legacy pose"

    adapter = FrameProcessorAdapter.from_tracker(LegacyTracker())
    frame = object()

    assert adapter.mode is FrameCallMode.LEGACY_FRAME_ONLY
    assert adapter(frame, 999) == "legacy pose"
    assert calls == [frame]


def test_unrelated_optional_positional_parameter_is_not_guessed_as_timestamp():
    calls: list[tuple[object, bool]] = []

    class LegacyTracker:
        def process_frame(self, frame, debug=False):
            calls.append((frame, debug))
            return None

    adapter = FrameProcessorAdapter.from_tracker(LegacyTracker())
    frame = object()
    adapter(frame, 222)

    assert adapter.mode is FrameCallMode.LEGACY_FRAME_ONLY
    assert calls == [(frame, False)]


def test_kwargs_tracker_receives_timestamp_keyword():
    class Tracker:
        def process_frame(self, frame, **kwargs):
            return frame, kwargs

    adapter = FrameProcessorAdapter.from_tracker(Tracker())

    assert adapter.mode is FrameCallMode.TIMESTAMP_KEYWORD
    assert adapter("frame", 55) == (
        "frame",
        {"capture_timestamp_ms": 55},
    )


def test_internal_type_error_propagates_and_frame_is_not_retried():
    calls = 0

    class BrokenTracker:
        def process_frame(self, frame, capture_timestamp_ms=None):
            nonlocal calls
            calls += 1
            raise TypeError("bug inside tracker math")

    adapter = FrameProcessorAdapter.from_tracker(BrokenTracker())

    with pytest.raises(TypeError, match="bug inside tracker math"):
        adapter(object(), 123)

    assert calls == 1


def test_missing_process_frame_fails_during_loop_construction():
    with pytest.raises(TypeError, match="tracker.process_frame must be callable"):
        FrameProcessorAdapter.from_tracker(object())


def test_magic_mock_uses_current_keyword_contract():
    tracker = MagicMock()
    tracker.process_frame.return_value = "pose"
    adapter = FrameProcessorAdapter.from_tracker(tracker)
    frame = object()

    assert adapter.mode is FrameCallMode.TIMESTAMP_KEYWORD
    assert adapter(frame, 314) == "pose"
    tracker.process_frame.assert_called_once_with(
        frame,
        capture_timestamp_ms=314,
    )


def test_uninspectable_callable_defaults_to_current_keyword_contract(monkeypatch):
    def processor(frame, **kwargs):
        return frame, kwargs

    def fail_signature(_callable):
        raise ValueError("signature unavailable")

    monkeypatch.setattr("tracker.frame_processor.inspect.signature", fail_signature)

    assert resolve_frame_call_mode(processor) is FrameCallMode.TIMESTAMP_KEYWORD
