from __future__ import annotations

import numpy as np

from tracker.cv2_temporal_tracker import FaceBox
from tracker.scheduled_cascade_detector import ScheduledCascadeFaceDetector


class ProbeScheduledDetector(ScheduledCascadeFaceDetector):
    def __init__(self, *, full=(), roi=()) -> None:
        self._full_results = list(full)
        self._roi_results = list(roi)
        self.calls: list[str] = []

    def _full_faces(self, _gray):
        self.calls.append("full")
        return list(self._full_results)

    def _roi_faces(self, _gray, _prior):
        self.calls.append("roi")
        return list(self._roi_results)

    def _eyes(self, _gray, _box):
        self.calls.append("eyes")
        return None


def test_plausible_full_result_remains_single_call_fast_path():
    prior = FaceBox(20.0, 20.0, 40.0, 40.0)
    detector = ProbeScheduledDetector(
        full=((22, 21, 39, 41),),
        roi=((20, 20, 40, 40),),
    )

    observation = detector.detect(
        np.zeros((140, 180), dtype=np.uint8),
        prior=prior,
        force_full_scan=True,
    )

    assert observation is not None
    assert observation.box == FaceBox(22.0, 21.0, 39.0, 41.0)
    assert detector.calls == ["full", "eyes"]


def test_missing_full_result_falls_back_to_prior_roi():
    prior = FaceBox(20.0, 20.0, 40.0, 40.0)
    detector = ProbeScheduledDetector(
        full=(),
        roi=((21, 22, 40, 39),),
    )

    observation = detector.detect(
        np.zeros((140, 180), dtype=np.uint8),
        prior=prior,
        force_full_scan=True,
    )

    assert observation is not None
    assert observation.box == FaceBox(21.0, 22.0, 40.0, 39.0)
    assert detector.calls == ["full", "roi", "eyes"]


def test_distant_full_result_is_compared_with_roi_and_continuity_wins():
    prior = FaceBox(20.0, 20.0, 40.0, 40.0)
    detector = ProbeScheduledDetector(
        full=((110, 70, 55, 55),),
        roi=((22, 21, 39, 41),),
    )

    observation = detector.detect(
        np.zeros((160, 200), dtype=np.uint8),
        prior=prior,
        force_full_scan=True,
    )

    assert observation is not None
    assert observation.box == FaceBox(22.0, 21.0, 39.0, 41.0)
    assert detector.calls == ["full", "roi", "eyes"]


def test_distant_full_result_still_reacquires_when_roi_misses():
    prior = FaceBox(20.0, 20.0, 40.0, 40.0)
    detector = ProbeScheduledDetector(
        full=((110, 70, 55, 55),),
        roi=(),
    )

    observation = detector.detect(
        np.zeros((160, 200), dtype=np.uint8),
        prior=prior,
        force_full_scan=True,
    )

    assert observation is not None
    assert observation.box == FaceBox(110.0, 70.0, 55.0, 55.0)
    assert detector.calls == ["full", "roi", "eyes"]


def test_forced_scan_without_prior_uses_only_the_global_result():
    detector = ProbeScheduledDetector(
        full=((50, 40, 60, 60),),
        roi=((1, 1, 20, 20),),
    )

    observation = detector.detect(
        np.zeros((140, 180), dtype=np.uint8),
        prior=None,
        force_full_scan=True,
    )

    assert observation is not None
    assert observation.box == FaceBox(50.0, 40.0, 60.0, 60.0)
    assert detector.calls == ["full", "eyes"]
