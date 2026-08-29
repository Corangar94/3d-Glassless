from unittest.mock import MagicMock, call, patch

import tracker.main as tracker_main
from tracker.main import TrackingLoop, _open_camera


def _capture(*, opened: bool, reads=None) -> MagicMock:
    cap = MagicMock()
    cap.isOpened.return_value = opened
    cap.get.return_value = 0.0
    if reads is not None:
        cap.read.side_effect = reads
    return cap


class _SettingsReader:
    def read(self):
        return None

    def close(self) -> None:
        pass


def test_open_camera_can_start_with_media_foundation():
    cap = _capture(opened=True)
    metadata: dict[str, object] = {}

    with patch("tracker.main.cv2.VideoCapture", return_value=cap) as video_capture:
        selected = _open_camera(
            2,
            backend_start_index=1,
            metadata=metadata,
        )

    assert selected is cap
    video_capture.assert_called_once_with(2, tracker_main.cv2.CAP_MSMF)
    assert metadata == {
        "backend_index": 1,
        "backend_id": tracker_main.cv2.CAP_MSMF,
        "backend_name": "CAP_MSMF",
    }


def test_open_camera_falls_through_to_next_backend_and_records_actual_choice():
    msmf = _capture(opened=False)
    default = _capture(opened=True)
    metadata: dict[str, object] = {"stale": True}

    with patch(
        "tracker.main.cv2.VideoCapture",
        side_effect=[msmf, default],
    ) as video_capture:
        selected = _open_camera(
            4,
            backend_start_index=1,
            metadata=metadata,
        )

    assert selected is default
    assert video_capture.call_args_list == [
        call(4, tracker_main.cv2.CAP_MSMF),
        call(4),
    ]
    msmf.release.assert_called_once()
    assert metadata == {
        "backend_index": 2,
        "backend_id": None,
        "backend_name": "default backend",
    }


def test_open_camera_releases_every_failed_candidate():
    failed = [_capture(opened=False) for _ in range(3)]
    metadata: dict[str, object] = {"old": "value"}

    with patch(
        "tracker.main.cv2.VideoCapture",
        side_effect=failed,
    ) as video_capture:
        selected = _open_camera(0, metadata=metadata)

    assert selected is failed[-1]
    assert video_capture.call_args_list == [
        call(0, tracker_main.cv2.CAP_DSHOW),
        call(0, tracker_main.cv2.CAP_MSMF),
        call(0),
    ]
    for cap in failed:
        cap.release.assert_called_once()
    assert metadata == {}


def test_tracking_loop_rotates_backend_after_each_stalled_capture_session():
    directshow = _capture(
        opened=True,
        reads=[(False, None), (False, None), (False, None)],
    )
    media_foundation = _capture(
        opened=True,
        reads=[(False, None), (False, None), (False, None)],
    )
    default = _capture(opened=True, reads=[(True, MagicMock())])
    tracker = MagicMock()
    tracker.process_frame.return_value = None
    writer = MagicMock()
    smoother = MagicMock()

    loop = TrackingLoop(
        tracker=tracker,
        writer=writer,
        smoother=smoother,
        hold_ms=0,
    )

    with (
        patch(
            "tracker.main.cv2.VideoCapture",
            side_effect=[directshow, media_foundation, default],
        ) as video_capture,
        patch("tracker.main.SharedSettingsReader", return_value=_SettingsReader()),
        patch("tracker.main.time.sleep"),
    ):
        loop.run(camera_index=0, max_frames=1)

    assert video_capture.call_args_list == [
        call(0, tracker_main.cv2.CAP_DSHOW),
        call(0, tracker_main.cv2.CAP_MSMF),
        call(0),
    ]
    directshow.release.assert_called()
    media_foundation.release.assert_called()
    tracker.reset_session.assert_has_calls([call(), call()])
    assert tracker.reset_session.call_count == 2
    assert smoother.reset.call_count == 2
    assert tracker.process_frame.call_count == 1


def test_failed_preferred_backend_does_not_break_future_rotation():
    directshow = _capture(opened=False)
    media_foundation = _capture(
        opened=True,
        reads=[(False, None), (False, None), (False, None)],
    )
    default = _capture(opened=True, reads=[(True, MagicMock())])
    tracker = MagicMock()
    tracker.process_frame.return_value = None

    loop = TrackingLoop(
        tracker=tracker,
        writer=MagicMock(),
        smoother=MagicMock(),
        hold_ms=0,
    )

    with (
        patch(
            "tracker.main.cv2.VideoCapture",
            side_effect=[directshow, media_foundation, default],
        ) as video_capture,
        patch("tracker.main.SharedSettingsReader", return_value=_SettingsReader()),
        patch("tracker.main.time.sleep"),
    ):
        loop.run(camera_index=1, max_frames=1)

    # Initial open falls through DSHOW to MSMF. Because metadata records MSMF as
    # the selected backend, the stalled-session retry starts at the default path.
    assert video_capture.call_args_list == [
        call(1, tracker_main.cv2.CAP_DSHOW),
        call(1, tracker_main.cv2.CAP_MSMF),
        call(1),
    ]
    directshow.release.assert_called_once()
