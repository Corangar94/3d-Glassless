from __future__ import annotations

from collections import deque

import numpy as np
import pytest

from tracker.cv2_temporal_tracker import (
    Cv2FallbackTrackingError,
    EyePair,
    FaceBox,
    FaceObservation,
)
from tracker.face_tracker_cv2 import FaceTracker


class FakeDetector:
    def __init__(self, *results: FaceObservation | None) -> None:
        self.results = deque(results)
        self.calls: list[tuple[FaceBox | None, bool, tuple[int, int]]] = []

    def detect(self, gray, *, prior=None, allow_full_scan=True):
        self.calls.append((prior, allow_full_scan, tuple(gray.shape)))
        return self.results.popleft() if self.results else None


class FakeMotion:
    def __init__(self, *results: FaceObservation | None | BaseException) -> None:
        self.results = deque(results)
        self.track_calls = 0
        self.initializations: list[tuple[FaceBox, EyePair | None]] = []
        self.reset_count = 0
        self.current_box: FaceBox | None = None

    def track(self, _gray):
        self.track_calls += 1
        result = self.results.popleft() if self.results else None
        if isinstance(result, BaseException):
            raise result
        if result is not None:
            self.current_box = result.box
        return result

    def initialize(self, _gray, box, eyes=None):
        self.initializations.append((box, eyes))
        self.current_box = box
        return True

    def reset(self):
        self.reset_count += 1
        self.current_box = None


def _box(x: float = 20.0, y: float = 15.0) -> FaceBox:
    return FaceBox(x, y, 40.0, 48.0)


def _eyes(box: FaceBox | None = None) -> EyePair:
    box = box or _box()
    return EyePair(
        (box.x + box.width * 0.28, box.y + box.height * 0.36),
        (box.x + box.width * 0.72, box.y + box.height * 0.38),
    )


def _observation(
    *,
    box: FaceBox | None = None,
    eyes: EyePair | None = None,
    source: str = "cascade",
    quality: float = 1.0,
) -> FaceObservation:
    box = box or _box()
    return FaceObservation(
        box,
        _eyes(box) if eyes is None and source == "cascade" else eyes,
        source,
        quality,
    )


def _tracker(
    detector: FakeDetector,
    motion: FakeMotion,
    **kwargs,
) -> FaceTracker:
    return FaceTracker(
        real_ipd_cm=6.3,
        screen_width_cm=60.0,
        screen_height_cm=34.0,
        camera_fov_deg=90.0,
        detector=detector,
        motion_tracker=motion,
        **kwargs,
    )


def _frame(width: int = 320, height: int = 240) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def test_initial_cascade_then_four_flow_frames_then_periodic_correction():
    initial = _observation()
    flows = [
        _observation(
            box=_box(20.0 + index, 15.0),
            eyes=_eyes(_box(20.0 + index, 15.0)),
            source="flow",
            quality=0.9,
        )
        for index in range(1, 6)
    ]
    correction = _observation(box=_box(26.0, 15.0))
    detector = FakeDetector(initial, correction)
    motion = FakeMotion(None, *flows)
    tracker = _tracker(
        detector,
        motion,
        detection_interval_frames=5,
        full_scan_interval_frames=30,
    )

    outputs = [
        tracker.process_frame(_frame(), 1000 + index * 33)
        for index in range(6)
    ]

    assert all(output is not None for output in outputs)
    assert len(detector.calls) == 2
    assert detector.calls[0][1] is True
    assert detector.calls[1][1] is False
    assert len(motion.initializations) == 2
    assert motion.track_calls == 6


def test_low_quality_flow_forces_early_full_detection():
    initial = _observation()
    weak = _observation(
        box=_box(21.0, 15.0),
        eyes=_eyes(_box(21.0, 15.0)),
        source="flow",
        quality=0.05,
    )
    corrected = _observation(box=_box(22.0, 15.0))
    detector = FakeDetector(initial, corrected)
    motion = FakeMotion(None, weak)
    tracker = _tracker(
        detector,
        motion,
        detection_interval_frames=10,
        full_scan_interval_frames=30,
        minimum_flow_quality=0.25,
    )

    tracker.process_frame(_frame(), 1000)
    tracker.process_frame(_frame(), 1033)

    assert len(detector.calls) == 2
    assert detector.calls[-1][1] is True


def test_repeated_cascade_misses_retire_flow_track():
    initial = _observation()
    flow = _observation(
        source="flow",
        eyes=_eyes(),
        quality=0.9,
    )
    detector = FakeDetector(initial, None, None, None)
    motion = FakeMotion(None, flow, flow, flow)
    tracker = _tracker(
        detector,
        motion,
        detection_interval_frames=1,
        full_scan_interval_frames=1,
        maximum_cascade_misses=2,
    )

    first = tracker.process_frame(_frame(), 1000)
    second = tracker.process_frame(_frame(), 1033)
    third = tracker.process_frame(_frame(), 1066)
    fourth = tracker.process_frame(_frame(), 1099)

    assert first is not None
    assert second is not None
    assert third is not None
    assert fourth is None
    assert tracker.cascade_misses == 3
    assert motion.reset_count >= 1


def test_motion_failure_falls_back_to_cascade_in_same_frame():
    recovered = _observation()
    detector = FakeDetector(recovered)
    motion = FakeMotion(Cv2FallbackTrackingError("flow failed"))
    tracker = _tracker(detector, motion)

    output = tracker.process_frame(_frame(), 1000)

    assert output is not None
    assert len(detector.calls) == 1
    assert motion.reset_count == 1
    assert "flow failed" in tracker.last_motion_error


def test_cascade_eye_miss_propagates_flow_eye_geometry_without_refreshing_age():
    predicted_box = _box(20.0, 15.0)
    predicted = _observation(
        box=predicted_box,
        eyes=_eyes(predicted_box),
        source="flow",
        quality=0.9,
    )
    detected = FaceObservation(_box(22.0, 16.0), None, "cascade", 1.0)
    tracker = _tracker(FakeDetector(), FakeMotion())

    corrected = tracker._corrected_detection(
        predicted,
        detected,
        np.zeros((120, 160), dtype=np.uint8),
    )

    assert corrected.source == "cascade_flow_eyes"
    assert corrected.eyes is not None
    assert corrected.eyes.midpoint[0] == pytest.approx(
        corrected.box.center[0],
        abs=6.0,
    )


def test_recent_eye_memory_synthesizes_center_and_roll_when_pair_disappears():
    tracker = _tracker(
        FakeDetector(),
        FakeMotion(),
        eye_track_hold_frames=3,
    )
    box = _box()
    fresh = _observation(box=box)
    fresh_eyes = tracker._usable_eyes(fresh)
    assert fresh_eyes is not None

    missing = FaceObservation(_box(24.0, 17.0), None, "cascade", 1.0)
    synthetic = tracker._usable_eyes(missing)

    assert synthetic is not None
    assert synthetic.separation_px == pytest.approx(
        tracker._eye_ratio * missing.box.width
    )
    assert synthetic.roll_deg == pytest.approx(fresh_eyes.roll_deg)
    assert tracker._eye_age_frames == 1


def test_eye_memory_expires_after_bounded_hold():
    tracker = _tracker(
        FakeDetector(),
        FakeMotion(),
        eye_track_hold_frames=1,
    )
    tracker._usable_eyes(_observation())
    missing = FaceObservation(_box(), None, "cascade", 1.0)

    assert tracker._usable_eyes(missing) is not None
    assert tracker._usable_eyes(missing) is None
    assert tracker._eye_ratio is None
    assert tracker._eye_center_ratio is None


def test_tracking_resolution_scales_pose_back_to_original_pixels():
    box = FaceBox(280.0, 120.0, 80.0, 100.0)
    eyes = EyePair((300.0, 155.0), (340.0, 155.0))
    detector = FakeDetector(FaceObservation(box, eyes, "cascade", 1.0))
    motion = FakeMotion(None)
    tracker = _tracker(
        detector,
        motion,
        detection_width_px=640,
    )

    output = tracker.process_frame(_frame(1280, 720), 1000)

    assert output is not None
    assert detector.calls[0][2][1] == 640
    assert output.x_cm == pytest.approx(0.0, abs=0.1)
    assert output.capture_timestamp_ms == 1000


def test_reset_session_clears_temporal_and_eye_state():
    detector = FakeDetector(_observation())
    motion = FakeMotion(None)
    tracker = _tracker(detector, motion)
    assert tracker.process_frame(_frame(), 1000) is not None
    tracker._cascade_misses = 2
    tracker._last_motion_error = "old flow error"

    tracker.reset_session()

    assert tracker._frame_index == 0
    assert tracker.cascade_misses == 0
    assert tracker.last_motion_error == ""
    assert tracker._eye_ratio is None
    assert tracker._eye_center_ratio is None
    assert tracker._eye_age_frames == 0
    assert motion.reset_count == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"detection_width_px": 100},
        {"detection_interval_frames": 0},
        {"detection_interval_frames": 5, "full_scan_interval_frames": 4},
        {"eye_track_hold_frames": -1},
        {"maximum_cascade_misses": -1},
        {"minimum_flow_quality": -0.1},
        {"minimum_flow_quality": 1.1},
        {"cascade_correction_alpha": -0.1},
        {"cascade_correction_alpha": 1.1},
    ],
)
def test_invalid_temporal_fallback_configuration_fails_closed(kwargs):
    with pytest.raises(ValueError):
        _tracker(FakeDetector(), FakeMotion(), **kwargs)
