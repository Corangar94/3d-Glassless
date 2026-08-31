from __future__ import annotations

from collections import deque

import numpy as np

from tracker.cv2_temporal_tracker import EyePair, FaceBox, FaceObservation
from tracker.face_tracker_cv2 import FaceTracker


class FakeDetector:
    def __init__(self, *results: FaceObservation | None) -> None:
        self.results = deque(results)
        self.calls: list[
            tuple[float, FaceBox | None, bool]
        ] = []

    def detect(self, gray, *, prior=None, allow_full_scan=True):
        self.calls.append(
            (float(np.mean(gray)), prior, bool(allow_full_scan))
        )
        return self.results.popleft() if self.results else None


class ModernDetector:
    def __init__(self, result: FaceObservation) -> None:
        self.result = result
        self.calls: list[
            tuple[FaceBox | None, bool, bool]
        ] = []

    def detect(
        self,
        _gray,
        *,
        prior=None,
        allow_full_scan=True,
        force_full_scan=False,
    ):
        self.calls.append(
            (prior, bool(allow_full_scan), bool(force_full_scan))
        )
        return self.result


class FakeMotion:
    def __init__(self, *results: FaceObservation | None) -> None:
        self.results = deque(results)
        self.track_means: list[float] = []
        self.initialize_means: list[float] = []
        self.current_box: FaceBox | None = None
        self.reset_count = 0

    def track(self, gray):
        self.track_means.append(float(np.mean(gray)))
        result = self.results.popleft() if self.results else None
        if result is not None:
            self.current_box = result.box
        return result

    def initialize(self, gray, box, eyes=None):
        self.initialize_means.append(float(np.mean(gray)))
        self.current_box = box
        return True

    def reset(self):
        self.reset_count += 1
        self.current_box = None


def _eyes(box: FaceBox) -> EyePair:
    return EyePair(
        (box.x + box.width * 0.30, box.y + box.height * 0.36),
        (box.x + box.width * 0.70, box.y + box.height * 0.36),
    )


def _observation(
    x: float,
    *,
    source: str,
    quality: float = 1.0,
) -> FaceObservation:
    box = FaceBox(x, 40.0, 80.0, 90.0)
    return FaceObservation(box, _eyes(box), source, quality)


def _tracker(detector, motion, **kwargs) -> FaceTracker:
    return FaceTracker(
        real_ipd_cm=6.3,
        screen_width_cm=60.0,
        screen_height_cm=34.0,
        camera_fov_deg=90.0,
        detector=detector,
        motion_tracker=motion,
        **kwargs,
    )


def test_histogram_equalization_runs_only_when_cascade_is_due(monkeypatch):
    initial = _observation(100.0, source="cascade")
    correction = _observation(105.0, source="cascade")
    flows = [
        _observation(100.0 + index, source="flow", quality=0.9)
        for index in range(1, 6)
    ]
    detector = FakeDetector(initial, correction)
    motion = FakeMotion(None, *flows)
    tracker = _tracker(
        detector,
        motion,
        detection_interval_frames=5,
        full_scan_interval_frames=30,
    )
    equalize_calls: list[float] = []

    def equalize(gray):
        equalize_calls.append(float(np.mean(gray)))
        return np.full_like(gray, 200)

    monkeypatch.setattr(
        "tracker.face_tracker_cv2.cv2.equalizeHist",
        equalize,
    )
    frame = np.full((240, 320, 3), 30, dtype=np.uint8)

    outputs = [
        tracker.process_frame(frame, 1000 + index * 33)
        for index in range(6)
    ]

    assert all(output is not None for output in outputs)
    assert equalize_calls == [30.0, 30.0]
    assert [call[0] for call in detector.calls] == [200.0, 200.0]
    assert motion.track_means == [30.0] * 6
    assert motion.initialize_means == [30.0, 30.0]
    # Legacy injected detectors preserve their historical periodic ROI cadence.
    assert detector.calls[0][2] is True
    assert detector.calls[1][2] is False


def test_flow_only_frames_do_not_call_equalize(monkeypatch):
    flow = _observation(100.0, source="flow", quality=0.95)
    motion = FakeMotion(flow)
    detector = FakeDetector()
    tracker = _tracker(
        detector,
        motion,
        detection_interval_frames=5,
        full_scan_interval_frames=30,
    )
    tracker._frame_index = 1

    def unexpected_equalize(_gray):
        raise AssertionError("equalizeHist ran on a flow-only frame")

    monkeypatch.setattr(
        "tracker.face_tracker_cv2.cv2.equalizeHist",
        unexpected_equalize,
    )

    output = tracker.process_frame(
        np.full((240, 320, 3), 40, dtype=np.uint8),
        1000,
    )

    assert output is not None
    assert detector.calls == []
    assert motion.track_means == [40.0]


def test_periodic_full_scan_is_explicit_for_modern_detector():
    flow = _observation(100.0, source="flow", quality=0.9)
    detected = _observation(102.0, source="cascade")
    detector = ModernDetector(detected)
    motion = FakeMotion(flow)
    tracker = _tracker(
        detector,
        motion,
        detection_interval_frames=5,
        full_scan_interval_frames=30,
    )
    tracker._frame_index = 30

    output = tracker.process_frame(
        np.full((240, 320, 3), 50, dtype=np.uint8),
        1000,
    )

    assert output is not None
    assert detector.calls == [(flow.box, True, True)]


def test_periodic_roi_correction_allows_same_frame_full_fallback():
    flow = _observation(100.0, source="flow", quality=0.9)
    detected = _observation(102.0, source="cascade")
    detector = ModernDetector(detected)
    motion = FakeMotion(flow)
    tracker = _tracker(
        detector,
        motion,
        detection_interval_frames=5,
        full_scan_interval_frames=30,
    )
    tracker._frame_index = 5

    output = tracker.process_frame(
        np.full((240, 320, 3), 50, dtype=np.uint8),
        1000,
    )

    assert output is not None
    assert detector.calls == [(flow.box, True, False)]
