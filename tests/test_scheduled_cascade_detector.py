from __future__ import annotations

from collections import deque

import numpy as np

from tracker.cv2_temporal_tracker import FaceBox, FaceObservation
from tracker.scheduled_cascade_detector import (
    CascadeDetectorCallAdapter,
    ScheduledCascadeFaceDetector,
)


class FakeCascade:
    def __init__(self, *results) -> None:
        self._results = deque(results)
        self.calls: list[tuple[tuple[int, ...], dict[str, object]]] = []

    def empty(self) -> bool:
        return False

    def detectMultiScale(self, image, **kwargs):
        self.calls.append((tuple(image.shape), dict(kwargs)))
        return self._results.popleft() if self._results else ()


def test_roi_miss_falls_through_to_full_scan_in_the_same_call():
    face = FakeCascade((), ((30, 20, 50, 50),))
    eyes = FakeCascade(())
    detector = ScheduledCascadeFaceDetector(face, eyes)
    gray = np.zeros((120, 160), dtype=np.uint8)
    prior = FaceBox(60.0, 30.0, 40.0, 40.0)

    observation = detector.detect(
        gray,
        prior=prior,
        allow_full_scan=True,
        force_full_scan=False,
    )

    assert observation is not None
    assert len(face.calls) == 2
    assert face.calls[0][0][0] < gray.shape[0]
    assert face.calls[0][0][1] < gray.shape[1]
    assert face.calls[1][0] == gray.shape


def test_forced_full_scan_skips_roi_and_keeps_temporal_candidate_selection():
    prior = FaceBox(20.0, 20.0, 40.0, 40.0)
    nearby = (22, 21, 39, 41)
    distant_large = (100, 60, 55, 55)
    face = FakeCascade((distant_large, nearby))
    eyes = FakeCascade(())
    detector = ScheduledCascadeFaceDetector(face, eyes)
    gray = np.zeros((140, 180), dtype=np.uint8)

    observation = detector.detect(
        gray,
        prior=prior,
        allow_full_scan=True,
        force_full_scan=True,
    )

    assert observation is not None
    assert observation.box == FaceBox.from_rect(nearby)
    assert len(face.calls) == 1
    assert face.calls[0][0] == gray.shape


def test_roi_success_avoids_full_scan_between_forced_intervals():
    face = FakeCascade(((5, 6, 40, 42),))
    eyes = FakeCascade(())
    detector = ScheduledCascadeFaceDetector(face, eyes)
    gray = np.zeros((120, 160), dtype=np.uint8)

    observation = detector.detect(
        gray,
        prior=FaceBox(40.0, 30.0, 50.0, 55.0),
        allow_full_scan=True,
        force_full_scan=False,
    )

    assert observation is not None
    assert len(face.calls) == 1
    assert face.calls[0][0] != gray.shape


class LegacyDetector:
    def __init__(self) -> None:
        self.calls: list[tuple[FaceBox | None, bool]] = []

    def detect(self, _gray, *, prior=None, allow_full_scan=True):
        self.calls.append((prior, allow_full_scan))
        return None


def test_legacy_detector_contract_is_resolved_without_typeerror_retry():
    detector = LegacyDetector()
    adapter = CascadeDetectorCallAdapter(detector)
    prior = FaceBox(10.0, 10.0, 40.0, 40.0)
    gray = np.zeros((100, 120), dtype=np.uint8)

    adapter.detect(
        gray,
        prior=prior,
        allow_full_scan=True,
        force_full_scan=False,
    )
    adapter.detect(
        gray,
        prior=prior,
        allow_full_scan=True,
        force_full_scan=True,
    )

    assert not adapter.supports_force_full_scan
    assert detector.calls == [(prior, False), (None, True)]


class ModernDetector:
    def __init__(self) -> None:
        self.calls: list[tuple[FaceBox | None, bool, bool]] = []

    def detect(
        self,
        _gray,
        *,
        prior=None,
        allow_full_scan=True,
        force_full_scan=False,
    ):
        self.calls.append((prior, allow_full_scan, force_full_scan))
        return FaceObservation(
            FaceBox(10.0, 10.0, 40.0, 40.0),
            None,
            "cascade",
            1.0,
        )


def test_modern_detector_receives_explicit_force_full_scan_keyword():
    detector = ModernDetector()
    adapter = CascadeDetectorCallAdapter(detector)
    prior = FaceBox(10.0, 10.0, 40.0, 40.0)

    result = adapter.detect(
        np.zeros((100, 120), dtype=np.uint8),
        prior=prior,
        allow_full_scan=True,
        force_full_scan=True,
    )

    assert adapter.supports_force_full_scan
    assert result is not None
    assert detector.calls == [(prior, True, True)]
