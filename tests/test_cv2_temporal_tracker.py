from __future__ import annotations

from collections import deque

import cv2
import numpy as np
import pytest

from tracker.cv2_temporal_tracker import (
    CascadeFaceDetector,
    Cv2FallbackTrackingError,
    EyePair,
    FaceBox,
    SparseFaceMotionTracker,
    blend_boxes,
    box_iou,
    select_eye_pair,
    select_face_candidate,
)


def test_face_box_clip_expand_transform_and_iou():
    box = FaceBox(-5.0, 10.0, 40.0, 30.0)

    clipped = box.clipped(100, 80)
    expanded = clipped.expanded(1.5, 100, 80)
    moved = clipped.transformed(5.0, -2.0, 1.1, 100, 80)

    assert clipped == FaceBox(0.0, 10.0, 35.0, 30.0)
    assert expanded is not None and expanded.width > clipped.width
    assert moved is not None
    assert moved.center[0] == pytest.approx(clipped.center[0] + 5.0)
    assert moved.center[1] == pytest.approx(clipped.center[1] - 2.0)
    assert moved.width == pytest.approx(clipped.width * 1.1)
    assert box_iou(clipped, clipped) == pytest.approx(1.0)
    assert box_iou(clipped, FaceBox(80.0, 50.0, 10.0, 10.0)) == 0.0


def test_face_candidate_prefers_temporal_continuity_over_distant_size():
    prior = FaceBox(20.0, 20.0, 40.0, 40.0)
    nearby = (22, 21, 39, 41)
    distant_large = (100, 70, 80, 80)

    selected = select_face_candidate((distant_large, nearby), prior)

    assert selected == FaceBox.from_rect(nearby)
    assert select_face_candidate((distant_large, nearby), None) == FaceBox.from_rect(
        distant_large
    )


def test_eye_pair_selection_rejects_mouth_and_misaligned_candidates():
    face = FaceBox(10.0, 10.0, 100.0, 120.0)
    rectangles = (
        (28, 35, 18, 14),
        (76, 37, 17, 15),
        (45, 102, 20, 15),  # mouth/lower-face false positive
        (20, 65, 15, 15),   # vertically misaligned candidate
    )

    pair = select_eye_pair(rectangles, face)

    assert pair is not None
    assert pair.left == pytest.approx((37.0, 42.0))
    assert pair.right == pytest.approx((84.5, 44.5))
    assert pair.separation_px > 47.0
    assert abs(pair.roll_deg) < 5.0


def test_blended_box_applies_partial_cascade_correction():
    predicted = FaceBox(10.0, 10.0, 50.0, 50.0)
    detected = FaceBox(14.0, 6.0, 60.0, 40.0)

    blended = blend_boxes(predicted, detected, 0.75)

    assert blended == FaceBox(13.0, 7.0, 57.5, 42.5)


class FakeCascade:
    def __init__(self, *results, empty: bool = False) -> None:
        self._results = deque(results)
        self._empty = empty
        self.calls: list[tuple[tuple[int, ...], dict[str, object]]] = []

    def empty(self) -> bool:
        return self._empty

    def detectMultiScale(self, image, **kwargs):
        self.calls.append((tuple(image.shape), dict(kwargs)))
        return self._results.popleft() if self._results else ()


def test_cascade_detector_uses_roi_first_and_offsets_results():
    face_cascade = FakeCascade(((5, 6, 40, 42),))
    eye_cascade = FakeCascade(((8, 9, 10, 8), (28, 10, 10, 8)))
    detector = CascadeFaceDetector(face_cascade, eye_cascade)
    gray = np.zeros((120, 160), dtype=np.uint8)
    prior = FaceBox(40.0, 30.0, 50.0, 55.0)

    observation = detector.detect(
        gray,
        prior=prior,
        allow_full_scan=False,
    )

    assert observation is not None
    assert observation.source == "cascade"
    assert observation.eyes is not None
    assert face_cascade.calls[0][0][0] < gray.shape[0]
    assert face_cascade.calls[0][0][1] < gray.shape[1]
    assert observation.box.x >= 0.0
    assert observation.box.y >= 0.0


def test_cascade_detector_falls_back_to_full_frame_scan():
    face_cascade = FakeCascade((), ((30, 20, 50, 50),))
    eye_cascade = FakeCascade(())
    detector = CascadeFaceDetector(face_cascade, eye_cascade)
    gray = np.zeros((120, 160), dtype=np.uint8)

    observation = detector.detect(
        gray,
        prior=FaceBox(60.0, 30.0, 40.0, 40.0),
        allow_full_scan=True,
    )

    assert observation is not None
    assert len(face_cascade.calls) == 2
    assert face_cascade.calls[-1][0] == gray.shape


def test_empty_or_throwing_cascade_fails_explicitly():
    with pytest.raises(Cv2FallbackTrackingError, match="face cascade"):
        CascadeFaceDetector(FakeCascade(empty=True), FakeCascade())

    class ThrowingCascade(FakeCascade):
        def detectMultiScale(self, image, **kwargs):
            raise RuntimeError("corrupt classifier")

    detector = CascadeFaceDetector(ThrowingCascade(), FakeCascade())
    with pytest.raises(Cv2FallbackTrackingError, match="cascade detection failed"):
        detector.detect(np.zeros((100, 100), dtype=np.uint8))


def _points() -> np.ndarray:
    return np.array(
        [
            (30.0, 30.0),
            (50.0, 30.0),
            (70.0, 30.0),
            (30.0, 50.0),
            (70.0, 50.0),
            (30.0, 70.0),
            (50.0, 70.0),
            (70.0, 70.0),
        ],
        dtype=np.float32,
    ).reshape(-1, 1, 2)


def _full_status(count: int) -> np.ndarray:
    return np.ones((count, 1), dtype=np.uint8)


def _zero_errors(count: int) -> np.ndarray:
    return np.zeros((count, 1), dtype=np.float32)


def test_sparse_flow_tracks_translation_scale_and_eyes():
    initial_points = _points()
    calls = 0

    def features(_gray, **_kwargs):
        return initial_points.copy()

    def flow(_old, _new, previous, _unused, **_kwargs):
        nonlocal calls
        calls += 1
        points = np.asarray(previous).reshape(-1, 2)
        center = np.median(points, axis=0)
        translation = np.array((3.0, -2.0), dtype=np.float32)
        if calls == 1:
            transformed = (points - center) * 1.05 + center + translation
        else:
            transformed = (points - center) / 1.05 + center - translation
        count = len(points)
        return (
            transformed.astype(np.float32).reshape(-1, 1, 2),
            _full_status(count),
            _zero_errors(count),
        )

    tracker = SparseFaceMotionTracker(
        feature_function=features,
        flow_function=flow,
    )
    gray = np.zeros((120, 160), dtype=np.uint8)
    box = FaceBox(25.0, 25.0, 50.0, 50.0)
    eyes = EyePair((38.0, 43.0), (62.0, 43.0))

    assert tracker.initialize(gray, box, eyes)
    observation = tracker.track(gray.copy())

    assert observation is not None
    assert calls == 2
    assert observation.source == "flow"
    assert observation.box.center[0] == pytest.approx(box.center[0] + 3.0)
    assert observation.box.center[1] == pytest.approx(box.center[1] - 2.0)
    assert observation.box.width == pytest.approx(box.width * 1.05, rel=0.02)
    assert observation.eyes is not None
    assert observation.eyes.separation_px == pytest.approx(
        eyes.separation_px * 1.05,
        rel=0.02,
    )
    assert observation.quality == pytest.approx(1.0)
    assert tracker.last_forward_backward_error_px == pytest.approx(0.0)
    assert tracker.forward_backward_rejection_count == 0


def test_sparse_flow_rejects_insufficient_points_and_excess_motion():
    initial_points = _points()

    def features(_gray, **_kwargs):
        return initial_points.copy()

    def too_few(_old, _new, previous, _unused, **_kwargs):
        count = len(previous)
        status = np.zeros((count, 1), dtype=np.uint8)
        status[:3] = 1
        return previous.copy(), status, _zero_errors(count)

    tracker = SparseFaceMotionTracker(
        feature_function=features,
        flow_function=too_few,
    )
    gray = np.zeros((120, 160), dtype=np.uint8)
    tracker.initialize(gray, FaceBox(25.0, 25.0, 50.0, 50.0))
    assert tracker.track(gray.copy()) is None

    calls = 0

    def too_far(_old, _new, previous, _unused, **_kwargs):
        nonlocal calls
        calls += 1
        direction = 100.0 if calls == 1 else -100.0
        moved = previous + np.array((direction, 0.0), dtype=np.float32)
        count = len(previous)
        return moved, _full_status(count), _zero_errors(count)

    tracker = SparseFaceMotionTracker(
        feature_function=features,
        flow_function=too_far,
    )
    tracker.initialize(gray, FaceBox(25.0, 25.0, 50.0, 50.0))
    assert tracker.track(gray.copy()) is None
    assert calls == 2
    assert tracker.forward_backward_rejection_count == 0


def test_sparse_flow_rejects_forward_tracks_that_fail_round_trip():
    initial_points = _points()
    calls = 0

    def features(_gray, **_kwargs):
        return initial_points.copy()

    def inconsistent_flow(_old, _new, previous, _unused, **_kwargs):
        nonlocal calls
        calls += 1
        points = np.asarray(previous, dtype=np.float32)
        if calls == 1:
            points = points + np.array((2.0, 0.0), dtype=np.float32)
        count = len(points)
        return points, _full_status(count), _zero_errors(count)

    tracker = SparseFaceMotionTracker(
        feature_function=features,
        flow_function=inconsistent_flow,
    )
    gray = np.zeros((120, 160), dtype=np.uint8)
    tracker.initialize(gray, FaceBox(25.0, 25.0, 50.0, 50.0))

    assert tracker.track(gray.copy()) is None
    assert calls == 2
    assert tracker.last_forward_backward_error_px == pytest.approx(2.0)
    assert tracker.forward_backward_rejection_count == len(initial_points)


def test_sparse_flow_keeps_only_round_trip_consistent_points():
    initial_points = _points()
    calls = 0

    def features(_gray, **_kwargs):
        return initial_points.copy()

    def partially_consistent_flow(_old, _new, previous, _unused, **_kwargs):
        nonlocal calls
        calls += 1
        count = len(previous)
        if calls == 1:
            moved = previous + np.array((4.0, -1.0), dtype=np.float32)
            return moved, _full_status(count), _zero_errors(count)
        returned = initial_points.copy()
        returned[-2:] += np.array((3.0, 0.0), dtype=np.float32)
        return returned, _full_status(count), _zero_errors(count)

    tracker = SparseFaceMotionTracker(
        feature_function=features,
        flow_function=partially_consistent_flow,
    )
    gray = np.zeros((120, 160), dtype=np.uint8)
    box = FaceBox(25.0, 25.0, 50.0, 50.0)
    tracker.initialize(gray, box)

    observation = tracker.track(gray.copy())

    assert observation is not None
    assert observation.box.center[0] == pytest.approx(box.center[0] + 4.0)
    assert observation.box.center[1] == pytest.approx(box.center[1] - 1.0)
    assert observation.quality == pytest.approx(0.75)
    assert tracker.last_forward_backward_error_px == pytest.approx(0.0)
    assert tracker.forward_backward_rejection_count == 2


def test_sparse_flow_round_trip_error_reduces_quality_before_rejection():
    initial_points = _points()
    calls = 0

    def features(_gray, **_kwargs):
        return initial_points.copy()

    def noisy_inverse(_old, _new, previous, _unused, **_kwargs):
        nonlocal calls
        calls += 1
        count = len(previous)
        if calls == 1:
            moved = previous + np.array((1.0, 0.0), dtype=np.float32)
            return moved, _full_status(count), _zero_errors(count)
        returned = initial_points + np.array((0.75, 0.0), dtype=np.float32)
        return returned, _full_status(count), _zero_errors(count)

    tracker = SparseFaceMotionTracker(
        feature_function=features,
        flow_function=noisy_inverse,
    )
    gray = np.zeros((120, 160), dtype=np.uint8)
    tracker.initialize(gray, FaceBox(25.0, 25.0, 50.0, 50.0))

    observation = tracker.track(gray.copy())

    assert observation is not None
    assert observation.quality == pytest.approx(np.exp(-0.25), rel=1e-4)
    assert tracker.last_forward_backward_error_px == pytest.approx(0.75)
    assert tracker.forward_backward_rejection_count == 0


def test_sparse_flow_malformed_backward_output_fails_closed():
    initial_points = _points()
    calls = 0

    def features(_gray, **_kwargs):
        return initial_points.copy()

    def malformed_backward(_old, _new, previous, _unused, **_kwargs):
        nonlocal calls
        calls += 1
        count = len(previous)
        if calls == 1:
            return (
                previous.copy(),
                _full_status(count),
                _zero_errors(count),
            )
        return (
            previous[:-1].copy(),
            _full_status(count - 1),
            _zero_errors(count - 1),
        )

    tracker = SparseFaceMotionTracker(
        feature_function=features,
        flow_function=malformed_backward,
    )
    gray = np.zeros((120, 160), dtype=np.uint8)
    tracker.initialize(gray, FaceBox(25.0, 25.0, 50.0, 50.0))

    assert tracker.track(gray.copy()) is None
    assert tracker.forward_backward_rejection_count == len(initial_points)


def test_sparse_flow_contains_feature_and_flow_backend_errors():
    gray = np.zeros((120, 160), dtype=np.uint8)

    def broken_features(_gray, **_kwargs):
        raise RuntimeError("feature failure")

    tracker = SparseFaceMotionTracker(feature_function=broken_features)
    with pytest.raises(Cv2FallbackTrackingError, match="feature detection failed"):
        tracker.initialize(gray, FaceBox(20.0, 20.0, 50.0, 50.0))

    def features(_gray, **_kwargs):
        return _points()

    def broken_flow(*_args, **_kwargs):
        raise RuntimeError("flow failure")

    tracker = SparseFaceMotionTracker(
        feature_function=features,
        flow_function=broken_flow,
    )
    tracker.initialize(gray, FaceBox(20.0, 20.0, 50.0, 50.0))
    with pytest.raises(Cv2FallbackTrackingError, match="forward optical flow failed"):
        tracker.track(gray.copy())


def test_sparse_flow_contains_backward_flow_backend_error():
    calls = 0

    def features(_gray, **_kwargs):
        return _points()

    def broken_backward(_old, _new, previous, _unused, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("backward flow failure")
        count = len(previous)
        return previous.copy(), _full_status(count), _zero_errors(count)

    tracker = SparseFaceMotionTracker(
        feature_function=features,
        flow_function=broken_backward,
    )
    gray = np.zeros((120, 160), dtype=np.uint8)
    tracker.initialize(gray, FaceBox(20.0, 20.0, 50.0, 50.0))

    with pytest.raises(Cv2FallbackTrackingError, match="backward optical flow failed"):
        tracker.track(gray.copy())


@pytest.mark.parametrize(
    "value",
    [0.0, -1.0, float("nan"), float("inf")],
)
def test_sparse_flow_requires_finite_positive_round_trip_limit(value):
    with pytest.raises(
        ValueError,
        match="maximum_forward_backward_error",
    ):
        SparseFaceMotionTracker(maximum_forward_backward_error=value)


def test_real_opencv_flow_tracks_a_synthetic_face_patch():
    first = np.zeros((140, 180), dtype=np.uint8)
    for y in range(35, 105, 10):
        for x in range(45, 115, 10):
            cv2.rectangle(first, (x, y), (x + 4, y + 4), 255, -1)
    transform = np.array(((1.0, 0.0, 4.0), (0.0, 1.0, 3.0)), dtype=np.float32)
    second = cv2.warpAffine(first, transform, (first.shape[1], first.shape[0]))
    tracker = SparseFaceMotionTracker()
    box = FaceBox(40.0, 30.0, 80.0, 80.0)

    assert tracker.initialize(first, box)
    observation = tracker.track(second)

    assert observation is not None
    assert observation.box.center[0] == pytest.approx(box.center[0] + 4.0, abs=1.0)
    assert observation.box.center[1] == pytest.approx(box.center[1] + 3.0, abs=1.0)
    assert observation.quality > 0.5
    assert tracker.last_forward_backward_error_px is not None
    assert tracker.last_forward_backward_error_px < 0.5
