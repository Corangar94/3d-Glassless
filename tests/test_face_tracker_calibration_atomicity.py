from __future__ import annotations

from types import SimpleNamespace
import threading

import pytest

from tracker.face_tracker import FaceTracker


def _tracker(
    *,
    ipd_cm: float = 6.3,
    fov_deg: float = 60.0,
    with_lock: bool = True,
) -> FaceTracker:
    tracker = FaceTracker.__new__(FaceTracker)
    if with_lock:
        tracker._lock = threading.Lock()
    tracker._real_ipd_cm = ipd_cm
    tracker._camera_fov_deg = fov_deg
    tracker._camera_geometry = None
    return tracker


def _landmarks():
    points = [
        SimpleNamespace(x=0.5, y=0.5, presence=1.0, visibility=1.0)
        for _ in range(474)
    ]
    points[468] = SimpleNamespace(
        x=0.45,
        y=0.5,
        presence=1.0,
        visibility=1.0,
    )
    points[473] = SimpleNamespace(
        x=0.55,
        y=0.5,
        presence=1.0,
        visibility=1.0,
    )
    return points


def _result(landmarks=None):
    return SimpleNamespace(
        face_landmarks=[_landmarks() if landmarks is None else landmarks],
        facial_transformation_matrixes=[],
    )


def test_inflight_pose_uses_one_calibration_generation():
    tracker = _tracker(ipd_cm=6.3, fov_deg=60.0)
    old_reference = _tracker(ipd_cm=6.3, fov_deg=60.0)
    new_reference = _tracker(ipd_cm=7.0, fov_deg=90.0)
    points = _landmarks()
    entered_landmark_read = threading.Event()
    release_landmark_read = threading.Event()

    class BlockingResult:
        facial_transformation_matrixes = []

        @property
        def face_landmarks(self):
            entered_landmark_read.set()
            assert release_landmark_read.wait(2.0)
            return [points]

    output = []
    worker = threading.Thread(
        target=lambda: output.append(
            tracker._pose_from_result(
                BlockingResult(),
                640,
                480,
                1000,
            )
        )
    )
    worker.start()
    assert entered_landmark_read.wait(1.0)

    tracker.set_calibration(
        real_ipd_cm=7.0,
        camera_fov_deg=90.0,
    )
    release_landmark_read.set()
    worker.join(2.0)

    assert not worker.is_alive()
    assert len(output) == 1
    inflight = output[0]
    old_pose = old_reference._pose_from_result(
        _result(points),
        640,
        480,
        1000,
    )
    new_pose = new_reference._pose_from_result(
        _result(points),
        640,
        480,
        1000,
    )
    assert inflight is not None
    assert old_pose is not None
    assert new_pose is not None
    assert inflight.xyz == pytest.approx(old_pose.xyz)
    assert inflight.z_cm != pytest.approx(new_pose.z_cm)

    following = tracker._pose_from_result(
        _result(points),
        640,
        480,
        1033,
    )
    assert following is not None
    assert following.xyz == pytest.approx(new_pose.xyz)


def test_invalid_combined_update_does_not_partially_commit_ipd():
    tracker = _tracker(ipd_cm=6.3, fov_deg=60.0)

    with pytest.raises(ValueError, match="camera_fov_deg"):
        tracker.set_calibration(
            real_ipd_cm=7.0,
            camera_fov_deg=0.0,
        )

    snapshot = tracker._calibration_snapshot()
    assert snapshot.real_ipd_cm == pytest.approx(6.3)
    assert snapshot.camera_fov_deg == pytest.approx(60.0)


def test_nonfinite_ipd_does_not_partially_commit_valid_fov():
    tracker = _tracker(ipd_cm=6.3, fov_deg=60.0)

    with pytest.raises(ValueError, match="real_ipd_cm"):
        tracker.set_calibration(
            real_ipd_cm=float("inf"),
            camera_fov_deg=90.0,
        )

    snapshot = tracker._calibration_snapshot()
    assert snapshot.real_ipd_cm == pytest.approx(6.3)
    assert snapshot.camera_fov_deg == pytest.approx(60.0)


def test_valid_combined_update_commits_both_values():
    tracker = _tracker(ipd_cm=6.3, fov_deg=60.0)

    tracker.set_calibration(real_ipd_cm=7.1, camera_fov_deg=88.0)

    snapshot = tracker._calibration_snapshot()
    assert snapshot.real_ipd_cm == pytest.approx(7.1)
    assert snapshot.camera_fov_deg == pytest.approx(88.0)


def test_nonpositive_ipd_retains_historical_ignore_behavior():
    tracker = _tracker(ipd_cm=6.3, fov_deg=60.0)

    tracker.set_calibration(real_ipd_cm=0.0, camera_fov_deg=75.0)

    snapshot = tracker._calibration_snapshot()
    assert snapshot.real_ipd_cm == pytest.approx(6.3)
    assert snapshot.camera_fov_deg == pytest.approx(75.0)


def test_bare_legacy_tracker_without_lock_remains_supported():
    tracker = _tracker(ipd_cm=6.3, fov_deg=60.0, with_lock=False)

    tracker.set_calibration(real_ipd_cm="7.0", camera_fov_deg="80")

    snapshot = tracker._calibration_snapshot()
    assert snapshot.real_ipd_cm == pytest.approx(7.0)
    assert snapshot.camera_fov_deg == pytest.approx(80.0)
    assert snapshot.camera_geometry is None


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf"), "not-a-number"],
)
def test_invalid_fov_values_fail_without_mutation(value):
    tracker = _tracker(ipd_cm=6.3, fov_deg=60.0)

    with pytest.raises(ValueError):
        tracker.set_calibration(camera_fov_deg=value)

    snapshot = tracker._calibration_snapshot()
    assert snapshot.real_ipd_cm == pytest.approx(6.3)
    assert snapshot.camera_fov_deg == pytest.approx(60.0)
