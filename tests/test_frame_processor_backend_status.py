from __future__ import annotations

import pytest

from tracker.frame_processor import FrameProcessorAdapter


class _Publisher:
    def __init__(self) -> None:
        self.timestamps: list[int] = []
        self.closed = False

    def publish(self, timestamp_ms: int) -> None:
        self.timestamps.append(timestamp_ms)

    def close(self) -> None:
        self.closed = True


def _install_publisher(monkeypatch, publisher: _Publisher) -> None:
    monkeypatch.setattr(
        "tracker.frame_processor.make_tracker_backend_status_publisher",
        lambda _tracker: publisher,
    )
    monkeypatch.setattr(
        "tracker.frame_processor.monotonic_ms",
        lambda: 99,
    )


def test_backend_status_is_published_at_startup_and_after_success(monkeypatch):
    publisher = _Publisher()

    class Tracker:
        def process_frame(self, frame, capture_timestamp_ms=None):
            return frame, capture_timestamp_ms

    _install_publisher(monkeypatch, publisher)
    adapter = FrameProcessorAdapter.from_tracker(Tracker())

    assert adapter("frame", 1234) == ("frame", 1234)
    assert publisher.timestamps == [99, 1234]


def test_backend_status_is_published_when_tracker_raises(monkeypatch):
    publisher = _Publisher()

    class Tracker:
        def process_frame(self, frame, capture_timestamp_ms=None):
            raise RuntimeError("tracker failed")

    _install_publisher(monkeypatch, publisher)
    adapter = FrameProcessorAdapter.from_tracker(Tracker())

    with pytest.raises(RuntimeError, match="tracker failed"):
        adapter(object(), 5678)

    assert publisher.timestamps == [99, 5678]


def test_adapter_close_releases_backend_status_publisher(monkeypatch):
    publisher = _Publisher()

    class Tracker:
        def process_frame(self, frame):
            return None

    _install_publisher(monkeypatch, publisher)
    adapter = FrameProcessorAdapter.from_tracker(Tracker())

    adapter.close()

    assert publisher.timestamps == [99]
    assert publisher.closed
