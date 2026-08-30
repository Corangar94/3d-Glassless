from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tracker.frame_processor import FrameCallMode
from tracker.main import TrackingLoop
from tracker.pose import HeadPosition


class _SettingsReader:
    def read(self):
        return None

    def close(self) -> None:
        pass


def _capture(frame: object | None = None) -> MagicMock:
    cap = MagicMock()
    cap.isOpened.return_value = True
    cap.read.return_value = (True, object() if frame is None else frame)
    return cap


def test_tracking_loop_calls_legacy_tracker_once_without_timestamp():
    calls: list[object] = []

    class LegacyTracker:
        def process_frame(self, frame):
            calls.append(frame)
            return None

    loop = TrackingLoop(
        tracker=LegacyTracker(),
        writer=MagicMock(),
        smoother=MagicMock(),
        hold_ms=0,
    )
    frame = object()

    with (
        patch("tracker.main._open_camera", return_value=_capture(frame)),
        patch("tracker.main.SharedSettingsReader", return_value=_SettingsReader()),
    ):
        loop.run(camera_index=0, max_frames=1)

    assert loop._frame_processor.mode is FrameCallMode.LEGACY_FRAME_ONLY
    assert calls == [frame]


def test_tracking_loop_passes_timestamp_to_current_tracker_once():
    calls: list[tuple[object, int | None]] = []

    class CurrentTracker:
        def process_frame(self, frame, capture_timestamp_ms=None):
            calls.append((frame, capture_timestamp_ms))
            return HeadPosition(
                x_cm=0.0,
                y_cm=0.0,
                z_cm=60.0,
                capture_timestamp_ms=int(capture_timestamp_ms or 1),
            )

    frame = object()
    loop = TrackingLoop(
        tracker=CurrentTracker(),
        writer=MagicMock(),
        smoother=MagicMock(),
    )

    with (
        patch("tracker.main._open_camera", return_value=_capture(frame)),
        patch("tracker.main.SharedSettingsReader", return_value=_SettingsReader()),
        patch("tracker.main.monotonic_ms", return_value=4242),
    ):
        loop.run(camera_index=0, max_frames=1)

    assert loop._frame_processor.mode is FrameCallMode.TIMESTAMP_KEYWORD
    assert calls == [(frame, 4242)]


def test_backend_typeerror_escapes_once_instead_of_reprocessing_frame():
    calls = 0

    class BrokenTracker:
        def process_frame(self, frame, capture_timestamp_ms=None):
            nonlocal calls
            calls += 1
            raise TypeError("internal tracker bug")

    loop = TrackingLoop(
        tracker=BrokenTracker(),
        writer=MagicMock(),
        smoother=MagicMock(),
    )

    with (
        patch("tracker.main._open_camera", return_value=_capture()),
        patch("tracker.main.SharedSettingsReader", return_value=_SettingsReader()),
    ):
        with pytest.raises(TypeError, match="internal tracker bug"):
            loop.run(camera_index=0, max_frames=1)

    assert calls == 1


def test_signature_is_resolved_before_first_camera_open():
    class MissingTracker:
        pass

    with patch("tracker.main._open_camera") as open_camera:
        with pytest.raises(TypeError, match="tracker.process_frame must be callable"):
            TrackingLoop(
                tracker=MissingTracker(),
                writer=MagicMock(),
                smoother=MagicMock(),
            )

    open_camera.assert_not_called()


def test_main_source_has_no_exception_driven_frame_signature_fallback():
    source = open("tracker/main.py", encoding="utf-8").read()
    method = source.split("    def _process_frame(", 1)[1].split(
        "    def _supports_pose_filter",
        1,
    )[0]

    assert "except TypeError" not in method
    assert "self._frame_processor(frame, capture_timestamp_ms)" in method
