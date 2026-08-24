from unittest.mock import MagicMock

import pytest

import tracker.main as tracker_main
from tracker.face_tracker_cv2 import HeadPosition
from tracker.shared_settings import OverlaySettings


def _camera():
    cap = MagicMock()
    cap.isOpened.return_value = True
    cap.read.return_value = (True, object())
    return cap


def test_resolvers_reject_non_finite_calibration_values():
    cfg = {
        "tracking": {"ipd_cm": "inf", "camera_fov_deg": "nan"},
        "overlay": {"camera_fov_deg": "inf"},
    }

    assert tracker_main._resolve_ipd_cm(cfg) == 6.4
    assert tracker_main._resolve_camera_fov_deg(cfg) == 90.0


@pytest.mark.parametrize(
    "position",
    [
        HeadPosition(x_cm=float("nan"), y_cm=0.0, z_cm=60.0),
        HeadPosition(x_cm=0.0, y_cm=float("inf"), z_cm=60.0),
        HeadPosition(x_cm=0.0, y_cm=0.0, z_cm=float("nan")),
        HeadPosition(x_cm=0.0, y_cm=0.0, z_cm=0.0),
        HeadPosition(x_cm=0.0, y_cm=0.0, z_cm=-1.0),
    ],
)
def test_validated_pose_rejects_invalid_tracker_output(position):
    assert tracker_main._validated_pose(position) is None


def test_live_calibration_is_validated_and_not_reapplied_when_unchanged():
    tracker = MagicMock()
    settings = OverlaySettings(
        ipd_mm=65.0,
        camera_fov_deg=88.0,
        smoothing_alpha=float("nan"),
    )

    applied = tracker_main._apply_live_calibration(tracker, settings, None)
    repeated = tracker_main._apply_live_calibration(tracker, settings, applied)

    tracker.set_calibration.assert_called_once_with(
        real_ipd_cm=6.5,
        camera_fov_deg=88.0,
    )
    assert repeated == applied
    assert tracker_main._measurement_noise(settings) == 0.1


def test_tracking_loop_applies_live_calibration_before_measuring_frame(monkeypatch):
    events = []

    class Tracker:
        def set_calibration(self, **values):
            events.append(("calibration", values))

        def process_frame(self, _frame):
            events.append(("process", None))
            return HeadPosition(x_cm=1.0, y_cm=2.0, z_cm=60.0)

    class SettingsReader:
        def read(self):
            return OverlaySettings(ipd_mm=65.0, camera_fov_deg=88.0)

        def close(self):
            pass

    smoother = MagicMock()
    smoother.update.return_value = (1.0, 2.0, 60.0)
    writer = MagicMock()
    cap = _camera()
    monkeypatch.setattr(tracker_main, "_open_camera", lambda *_args: cap)
    monkeypatch.setattr(
        tracker_main,
        "SharedSettingsReader",
        SettingsReader,
    )

    loop = tracker_main.TrackingLoop(Tracker(), writer, smoother)
    loop.run(max_frames=1)

    assert events == [
        (
            "calibration",
            {"real_ipd_cm": 6.5, "camera_fov_deg": 88.0},
        ),
        ("process", None),
    ]
    writer.write.assert_called_once_with(x=1.0, y=2.0, z=60.0)


def test_non_finite_pose_is_published_as_neutral_paused_state(monkeypatch):
    tracker = MagicMock()
    tracker.process_frame.return_value = HeadPosition(
        x_cm=float("nan"),
        y_cm=0.0,
        z_cm=60.0,
    )
    writer = MagicMock()
    smoother = MagicMock()
    cap = _camera()
    monkeypatch.setattr(tracker_main, "_open_camera", lambda *_args: cap)

    loop = tracker_main.TrackingLoop(
        tracker,
        writer,
        smoother,
        hold_ms=0,
    )
    loop.run(max_frames=1)

    smoother.update.assert_not_called()
    writer.write_state.assert_called_once_with("paused")
    writer.write.assert_called_once_with(x=0.0, y=0.0, z=60.0)
