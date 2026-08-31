"""Explicit tracker frame-call compatibility without exception-driven retries."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import inspect
from typing import Any, Callable

from tracker.backend_status_shared_memory import (
    TrackerBackendStatusPublisher,
    make_tracker_backend_status_publisher,
)
from tracker.pose import monotonic_ms


class FrameCallMode(str, Enum):
    """How a tracker accepts the optional camera capture timestamp."""

    LEGACY_FRAME_ONLY = "legacy_frame_only"
    TIMESTAMP_KEYWORD = "timestamp_keyword"
    TIMESTAMP_POSITIONAL_ONLY = "timestamp_positional_only"


def resolve_frame_call_mode(process_frame: Callable[..., object]) -> FrameCallMode:
    """Inspect a frame processor once, before any camera frame is submitted.

    Compatibility is based on the declared callable signature rather than by
    invoking the tracker and catching ``TypeError``. That distinction is vital:
    a genuine ``TypeError`` raised inside face processing must propagate once,
    not be misclassified as an old one-argument API and execute the same frame a
    second time.

    Current/uninspectable callables default to the documented keyword contract.
    Legacy Python callables with only a frame argument are detected explicitly.
    A positional-only parameter is supported only when it is actually named
    ``capture_timestamp_ms``; unrelated optional positional parameters are not
    guessed to be timestamps.
    """
    try:
        signature = inspect.signature(process_frame)
    except (TypeError, ValueError):
        return FrameCallMode.TIMESTAMP_KEYWORD

    parameters = signature.parameters
    timestamp_parameter = parameters.get("capture_timestamp_ms")
    if timestamp_parameter is not None:
        if timestamp_parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            return FrameCallMode.TIMESTAMP_POSITIONAL_ONLY
        if timestamp_parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return FrameCallMode.TIMESTAMP_KEYWORD

    if any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        return FrameCallMode.TIMESTAMP_KEYWORD

    return FrameCallMode.LEGACY_FRAME_ONLY


@dataclass(frozen=True)
class FrameProcessorAdapter:
    """Call one tracker method in its pre-resolved compatibility mode."""

    process_frame: Callable[..., Any]
    mode: FrameCallMode
    backend_status_publisher: TrackerBackendStatusPublisher | None = None

    @classmethod
    def from_tracker(cls, tracker: object) -> "FrameProcessorAdapter":
        process_frame = getattr(tracker, "process_frame", None)
        if not callable(process_frame):
            raise TypeError("tracker.process_frame must be callable")
        publisher = make_tracker_backend_status_publisher(tracker)
        if publisher is not None:
            publisher.publish(monotonic_ms())
        return cls(
            process_frame=process_frame,
            mode=resolve_frame_call_mode(process_frame),
            backend_status_publisher=publisher,
        )

    def __call__(self, frame: object, capture_timestamp_ms: int) -> Any:
        try:
            if self.mode is FrameCallMode.TIMESTAMP_KEYWORD:
                return self.process_frame(
                    frame,
                    capture_timestamp_ms=capture_timestamp_ms,
                )
            if self.mode is FrameCallMode.TIMESTAMP_POSITIONAL_ONLY:
                return self.process_frame(frame, capture_timestamp_ms)
            return self.process_frame(frame)
        finally:
            publisher = self.backend_status_publisher
            if publisher is not None:
                publisher.publish(capture_timestamp_ms)

    def close(self) -> None:
        publisher = self.backend_status_publisher
        if publisher is not None:
            publisher.close()
