from __future__ import annotations

from collections import deque

import numpy as np

from tracker.main import TrackingLoop
from tracker.pose import FilteredPose, HeadPosition
from tracker.pose_step_limiter import PoseStepLimiter


class _Tracker:
    def __init__(self, results: list[HeadPosition | None]) -> None:
        self._results = deque(results)

    def process_frame(self, _frame, capture_timestamp_ms=None):
        return self._results.popleft()

    def reset_session(self) -> None:
        pass


class _Capture:
    def __init__(self) -> None:
        self._frame = np.zeros((4, 6, 3), dtype=np.uint8)
        self.release_count = 0

    def read(self):
        return True, self._frame

    def release(self) -> None:
        self.release_count += 1


class _SettingsReader:
    def read(self):
        return None

    def close(self) -> None:
        pass


class _Filter:
    def __init__(self) -> None:
        self.inputs: list[HeadPosition] = []
        self.last = FilteredPose(x_cm=0.0, y_cm=0.0, z_cm=60.0)

    def update_pose(self, pose: HeadPosition) -> FilteredPose:
        self.inputs.append(pose)
        self.last = FilteredPose(
            x_cm=pose.x_cm,
            y_cm=pose.y_cm,
            z_cm=pose.z_cm,
            yaw_deg=pose.yaw_deg,
            pitch_deg=pose.pitch_deg,
            roll_deg=pose.roll_deg,
            confidence=pose.confidence,
            capture_timestamp_ms=pose.capture_timestamp_ms,
        )
        return self.last

    def predict(self) -> FilteredPose:
        return self.last

    def set_measurement_noise(self, _value: float) -> None:
        pass

    def reset(self) -> None:
        self.inputs.clear()
        self.last = FilteredPose(x_cm=0.0, y_cm=0.0, z_cm=60.0)


class _Writer:
    def __init__(self) -> None:
        self.states: list[str] = []
        self.poses: list[FilteredPose] = []

    def write_state(self, state: str) -> None:
        self.states.append(state)

    def write_pose(self, pose: FilteredPose, *, valid: bool) -> None:
        self.poses.append(pose)


def _pose(
    timestamp_ms: int,
    *,
    x_cm: float,
    yaw_deg: float,
    confidence: float,
) -> HeadPosition:
    return HeadPosition(
        x_cm=x_cm,
        y_cm=0.0,
        z_cm=60.0,
        yaw_deg=yaw_deg,
        confidence=confidence,
        capture_timestamp_ms=timestamp_ms,
    )


def test_duplicate_and_out_of_order_results_become_hold_not_tracking(monkeypatch):
    accepted_first = _pose(
        1000,
        x_cm=1.0,
        yaw_deg=5.0,
        confidence=0.9,
    )
    duplicate = _pose(
        1000,
        x_cm=50.0,
        yaw_deg=120.0,
        confidence=0.1,
    )
    out_of_order = _pose(
        999,
        x_cm=-50.0,
        yaw_deg=-120.0,
        confidence=0.2,
    )
    accepted_next = _pose(
        1033,
        x_cm=2.0,
        yaw_deg=6.0,
        confidence=0.8,
    )
    tracker = _Tracker(
        [accepted_first, duplicate, out_of_order, accepted_next]
    )
    filter_ = _Filter()
    writer = _Writer()
    capture = _Capture()
    limiter = PoseStepLimiter()
    loop = TrackingLoop(
        tracker=tracker,
        writer=writer,
        smoother=filter_,
        hold_ms=500,
        pose_step_limiter=limiter,
    )
    monkeypatch.setattr(
        loop,
        "_open_camera_with_recovery",
        lambda *_args, **_kwargs: (capture, 0),
    )
    monkeypatch.setattr(
        "tracker.main.SharedSettingsReader",
        _SettingsReader,
    )
    capture_times = iter((2000, 2033, 2066, 2099))
    monkeypatch.setattr(
        "tracker.main.monotonic_ms",
        lambda: next(capture_times),
    )
    face_times = iter((1.000, 1.033, 1.066, 1.099))
    monkeypatch.setattr(
        "tracker.main.time.monotonic",
        lambda: next(face_times),
    )

    loop.run(max_frames=4)

    assert writer.states == ["tracking", "hold", "hold", "tracking"]
    assert filter_.inputs == [accepted_first, accepted_next]
    assert all(pose.yaw_deg not in {120.0, -120.0} for pose in filter_.inputs)
    assert all(pose.confidence not in {0.1, 0.2} for pose in filter_.inputs)
    assert loop._last_face_ms == 1099.0
    assert loop._last_raw_pos == accepted_next.xyz
    assert capture.release_count == 1

    timeline = loop._frame_processor.result_timeline_snapshot()
    assert timeline.duplicate_count == 1
    assert timeline.out_of_order_count == 1
    assert timeline.last_timestamp_ms == 1033
    # Rejected measurements never reach the production speed limiter.
    limiter_snapshot = limiter.snapshot()
    assert limiter_snapshot.duplicate_or_out_of_order_count == 0
    assert limiter_snapshot.last_timestamp_ms == 1033


def test_repeated_duplicate_results_cannot_keep_tracking_alive(monkeypatch):
    first = _pose(1000, x_cm=1.0, yaw_deg=0.0, confidence=0.9)
    duplicates = [
        _pose(1000, x_cm=10.0 + index, yaw_deg=90.0, confidence=0.1)
        for index in range(3)
    ]
    filter_ = _Filter()
    writer = _Writer()
    capture = _Capture()
    loop = TrackingLoop(
        tracker=_Tracker([first, *duplicates]),
        writer=writer,
        smoother=filter_,
        hold_ms=50,
        pose_step_limiter=PoseStepLimiter(),
    )
    monkeypatch.setattr(
        loop,
        "_open_camera_with_recovery",
        lambda *_args, **_kwargs: (capture, 0),
    )
    monkeypatch.setattr(
        "tracker.main.SharedSettingsReader",
        _SettingsReader,
    )
    capture_times = iter((2000, 2033, 2066, 2099))
    monkeypatch.setattr(
        "tracker.main.monotonic_ms",
        lambda: next(capture_times),
    )
    face_times = iter((1.000, 1.033, 1.066, 1.099))
    monkeypatch.setattr(
        "tracker.main.time.monotonic",
        lambda: next(face_times),
    )

    loop.run(max_frames=4)

    assert writer.states == ["tracking", "hold", "paused", "paused"]
    assert filter_.inputs == [first]
    assert loop._last_face_ms == 1000.0
