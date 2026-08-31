"""Temporal primitives for the pure-OpenCV fallback face tracker.

The design follows OpenCV's documented video pattern: use a cascade detector to
establish/revalidate a face ROI, seed Shi-Tomasi corners inside that ROI, and
track those sparse points between detections with pyramidal Lucas-Kanade optical
flow.  Cascades remain the source of truth; optical flow only bridges the short
interval between bounded periodic re-detections.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Iterable, Sequence

import cv2
import numpy as np


RectLike = Sequence[int | float]
FeatureFunction = Callable[..., np.ndarray | None]
FlowFunction = Callable[..., tuple[np.ndarray | None, np.ndarray | None, np.ndarray | None]]


class Cv2FallbackTrackingError(RuntimeError):
    """The fallback detector/tracker cannot safely continue."""


@dataclass(frozen=True)
class FaceBox:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.width, self.height)
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("face box values must be finite")
        if self.width <= 0.0 or self.height <= 0.0:
            raise ValueError("face box dimensions must be positive")

    @classmethod
    def from_rect(cls, rect: RectLike) -> "FaceBox":
        if len(rect) != 4:
            raise ValueError("face rectangle must contain x, y, width, height")
        return cls(*(float(value) for value in rect))

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width * 0.5, self.y + self.height * 0.5

    def as_int_tuple(self) -> tuple[int, int, int, int]:
        return (
            int(round(self.x)),
            int(round(self.y)),
            max(1, int(round(self.width))),
            max(1, int(round(self.height))),
        )

    def clipped(
        self,
        image_width: int,
        image_height: int,
        *,
        minimum_size: float = 8.0,
    ) -> "FaceBox | None":
        x0 = max(0.0, min(float(image_width), self.x))
        y0 = max(0.0, min(float(image_height), self.y))
        x1 = max(0.0, min(float(image_width), self.x + self.width))
        y1 = max(0.0, min(float(image_height), self.y + self.height))
        width = x1 - x0
        height = y1 - y0
        if width < minimum_size or height < minimum_size:
            return None
        return FaceBox(x0, y0, width, height)

    def expanded(
        self,
        factor: float,
        image_width: int,
        image_height: int,
    ) -> "FaceBox | None":
        factor = max(1.0, float(factor))
        center_x, center_y = self.center
        expanded = FaceBox(
            center_x - self.width * factor * 0.5,
            center_y - self.height * factor * 0.5,
            self.width * factor,
            self.height * factor,
        )
        return expanded.clipped(image_width, image_height)

    def transformed(
        self,
        dx: float,
        dy: float,
        scale: float,
        image_width: int,
        image_height: int,
    ) -> "FaceBox | None":
        center_x, center_y = self.center
        transformed = FaceBox(
            center_x + dx - self.width * scale * 0.5,
            center_y + dy - self.height * scale * 0.5,
            self.width * scale,
            self.height * scale,
        )
        return transformed.clipped(image_width, image_height)


@dataclass(frozen=True)
class EyePair:
    left: tuple[float, float]
    right: tuple[float, float]

    @property
    def separation_px(self) -> float:
        return float(
            math.hypot(
                self.right[0] - self.left[0],
                self.right[1] - self.left[1],
            )
        )

    @property
    def midpoint(self) -> tuple[float, float]:
        return (
            (self.left[0] + self.right[0]) * 0.5,
            (self.left[1] + self.right[1]) * 0.5,
        )

    @property
    def roll_deg(self) -> float:
        return math.degrees(
            math.atan2(
                self.right[1] - self.left[1],
                self.right[0] - self.left[0],
            )
        )

    def transformed(
        self,
        old_center: tuple[float, float],
        new_center: tuple[float, float],
        scale: float,
    ) -> "EyePair":
        def transform(point: tuple[float, float]) -> tuple[float, float]:
            return (
                new_center[0] + (point[0] - old_center[0]) * scale,
                new_center[1] + (point[1] - old_center[1]) * scale,
            )

        return EyePair(transform(self.left), transform(self.right))


@dataclass(frozen=True)
class FaceObservation:
    box: FaceBox
    eyes: EyePair | None
    source: str
    quality: float


def box_iou(left: FaceBox, right: FaceBox) -> float:
    x0 = max(left.x, right.x)
    y0 = max(left.y, right.y)
    x1 = min(left.x + left.width, right.x + right.width)
    y1 = min(left.y + left.height, right.y + right.height)
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = left.area + right.area - intersection
    return 0.0 if union <= 0.0 else intersection / union


def blend_boxes(predicted: FaceBox, detected: FaceBox, alpha: float) -> FaceBox:
    """Correct optical-flow drift without snapping fully to cascade jitter."""
    alpha = min(1.0, max(0.0, float(alpha)))
    inverse = 1.0 - alpha
    return FaceBox(
        predicted.x * inverse + detected.x * alpha,
        predicted.y * inverse + detected.y * alpha,
        predicted.width * inverse + detected.width * alpha,
        predicted.height * inverse + detected.height * alpha,
    )


def select_face_candidate(
    candidates: Iterable[RectLike],
    prior: FaceBox | None,
) -> FaceBox | None:
    boxes: list[FaceBox] = []
    for candidate in candidates:
        try:
            boxes.append(FaceBox.from_rect(candidate))
        except (TypeError, ValueError):
            continue
    if not boxes:
        return None
    if prior is None:
        return max(boxes, key=lambda box: box.area)

    prior_center = prior.center
    prior_scale = max(prior.width, prior.height, 1.0)

    def score(box: FaceBox) -> tuple[float, float]:
        center = box.center
        normalized_distance = math.hypot(
            center[0] - prior_center[0],
            center[1] - prior_center[1],
        ) / prior_scale
        size_ratio = min(box.area, prior.area) / max(box.area, prior.area)
        continuity = (
            box_iou(box, prior) * 4.0
            + math.exp(-2.0 * normalized_distance) * 2.0
            + size_ratio
        )
        return continuity, box.area

    return max(boxes, key=score)


def select_eye_pair(
    eye_rectangles: Iterable[RectLike],
    face_box: FaceBox,
) -> EyePair | None:
    candidates: list[tuple[float, float, float, float]] = []
    upper_limit = face_box.y + face_box.height * 0.68
    for rectangle in eye_rectangles:
        try:
            eye = FaceBox.from_rect(rectangle)
        except (TypeError, ValueError):
            continue
        center_x, center_y = eye.center
        if center_y > upper_limit:
            continue
        if not (
            face_box.x <= center_x <= face_box.x + face_box.width
            and face_box.y <= center_y <= face_box.y + face_box.height
        ):
            continue
        candidates.append((center_x, center_y, eye.width, eye.height))

    best_pair: EyePair | None = None
    best_score = -float("inf")
    for left_index, first in enumerate(candidates):
        for second in candidates[left_index + 1 :]:
            left, right = sorted((first, second), key=lambda item: item[0])
            separation = right[0] - left[0]
            vertical_delta = abs(right[1] - left[1])
            if not (
                face_box.width * 0.18
                <= separation
                <= face_box.width * 0.78
            ):
                continue
            if vertical_delta > face_box.height * 0.20:
                continue
            size_delta = abs(
                left[2] * left[3] - right[2] * right[3]
            ) / max(left[2] * left[3], right[2] * right[3], 1.0)
            pair_midpoint_x = (left[0] + right[0]) * 0.5
            center_penalty = abs(
                pair_midpoint_x - face_box.center[0]
            ) / max(face_box.width, 1.0)
            score = (
                separation / face_box.width
                - 1.5 * vertical_delta / face_box.height
                - 0.5 * size_delta
                - 0.35 * center_penalty
            )
            if score > best_score:
                best_score = score
                best_pair = EyePair(
                    (left[0], left[1]),
                    (right[0], right[1]),
                )
    return best_pair


class SparseFaceMotionTracker:
    """Track a detected face ROI between periodic cascade corrections."""

    def __init__(
        self,
        *,
        minimum_points: int = 6,
        maximum_points: int = 40,
        maximum_flow_error: float = 35.0,
        maximum_motion_fraction: float = 0.35,
        maximum_scale_step: float = 0.12,
        feature_function: FeatureFunction = cv2.goodFeaturesToTrack,
        flow_function: FlowFunction = cv2.calcOpticalFlowPyrLK,
    ) -> None:
        if minimum_points < 3:
            raise ValueError("minimum_points must be at least three")
        if maximum_points < minimum_points:
            raise ValueError("maximum_points cannot be smaller than minimum_points")
        if maximum_flow_error <= 0.0:
            raise ValueError("maximum_flow_error must be positive")
        if not (0.0 < maximum_motion_fraction <= 1.0):
            raise ValueError("maximum_motion_fraction must be in (0, 1]")
        if not (0.0 <= maximum_scale_step < 1.0):
            raise ValueError("maximum_scale_step must be in [0, 1)")
        self._minimum_points = int(minimum_points)
        self._maximum_points = int(maximum_points)
        self._maximum_flow_error = float(maximum_flow_error)
        self._maximum_motion_fraction = float(maximum_motion_fraction)
        self._maximum_scale_step = float(maximum_scale_step)
        self._feature_function = feature_function
        self._flow_function = flow_function
        self._previous_gray: np.ndarray | None = None
        self._points: np.ndarray | None = None
        self._box: FaceBox | None = None
        self._eyes: EyePair | None = None

    @property
    def current_box(self) -> FaceBox | None:
        return self._box

    def reset(self) -> None:
        self._previous_gray = None
        self._points = None
        self._box = None
        self._eyes = None

    def _features(self, gray: np.ndarray, box: FaceBox) -> np.ndarray | None:
        mask = np.zeros(gray.shape, dtype=np.uint8)
        x, y, width, height = box.as_int_tuple()
        inset_x = max(1, int(round(width * 0.06)))
        inset_y = max(1, int(round(height * 0.06)))
        x0 = max(0, x + inset_x)
        y0 = max(0, y + inset_y)
        x1 = min(gray.shape[1], x + width - inset_x)
        y1 = min(gray.shape[0], y + height - inset_y)
        if x1 <= x0 or y1 <= y0:
            return None
        mask[y0:y1, x0:x1] = 255
        try:
            points = self._feature_function(
                gray,
                maxCorners=self._maximum_points,
                qualityLevel=0.01,
                minDistance=5.0,
                mask=mask,
                blockSize=7,
            )
        except Exception as error:
            raise Cv2FallbackTrackingError(
                "Shi-Tomasi feature detection failed"
            ) from error
        if points is None:
            return None
        points = np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
        return points if len(points) >= self._minimum_points else None

    def initialize(
        self,
        gray: np.ndarray,
        box: FaceBox,
        eyes: EyePair | None = None,
    ) -> bool:
        if gray.ndim != 2 or gray.dtype != np.uint8:
            raise ValueError("motion tracking requires an 8-bit grayscale frame")
        clipped = box.clipped(gray.shape[1], gray.shape[0])
        if clipped is None:
            self.reset()
            return False
        points = self._features(gray, clipped)
        self._previous_gray = np.ascontiguousarray(gray).copy()
        self._points = points
        self._box = clipped
        self._eyes = eyes
        return points is not None

    @staticmethod
    def _robust_inliers(
        old_points: np.ndarray,
        new_points: np.ndarray,
    ) -> np.ndarray:
        displacement = new_points - old_points
        median_displacement = np.median(displacement, axis=0)
        residual = np.linalg.norm(
            displacement - median_displacement,
            axis=1,
        )
        median_residual = float(np.median(residual))
        threshold = max(2.0, median_residual * 3.0)
        return residual <= threshold

    def track(self, gray: np.ndarray) -> FaceObservation | None:
        previous_gray = self._previous_gray
        previous_points = self._points
        previous_box = self._box
        if (
            previous_gray is None
            or previous_points is None
            or previous_box is None
            or gray.shape != previous_gray.shape
        ):
            return None
        try:
            next_points, status, errors = self._flow_function(
                previous_gray,
                gray,
                previous_points,
                None,
                winSize=(21, 21),
                maxLevel=3,
                criteria=(
                    cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
                    30,
                    0.01,
                ),
            )
        except Exception as error:
            raise Cv2FallbackTrackingError(
                "Lucas-Kanade optical flow failed"
            ) from error
        if next_points is None or status is None:
            return None

        old = np.asarray(previous_points, dtype=np.float32).reshape(-1, 2)
        new = np.asarray(next_points, dtype=np.float32).reshape(-1, 2)
        valid = np.asarray(status).reshape(-1).astype(bool)
        valid &= np.all(np.isfinite(old), axis=1)
        valid &= np.all(np.isfinite(new), axis=1)
        if errors is not None:
            error_values = np.asarray(errors, dtype=np.float32).reshape(-1)
            valid &= np.isfinite(error_values)
            valid &= error_values <= self._maximum_flow_error
        if int(np.count_nonzero(valid)) < self._minimum_points:
            return None

        old_valid = old[valid]
        new_valid = new[valid]
        inliers = self._robust_inliers(old_valid, new_valid)
        old_inliers = old_valid[inliers]
        new_inliers = new_valid[inliers]
        if len(old_inliers) < self._minimum_points:
            return None

        displacement = new_inliers - old_inliers
        dx, dy = (float(value) for value in np.median(displacement, axis=0))
        maximum_motion = (
            max(previous_box.width, previous_box.height)
            * self._maximum_motion_fraction
            + 2.0
        )
        if math.hypot(dx, dy) > maximum_motion:
            return None

        old_center = np.median(old_inliers, axis=0)
        new_center = np.median(new_inliers, axis=0)
        old_radius = np.linalg.norm(old_inliers - old_center, axis=1)
        new_radius = np.linalg.norm(new_inliers - new_center, axis=1)
        useful_radius = old_radius > 3.0
        scale = 1.0
        if int(np.count_nonzero(useful_radius)) >= self._minimum_points // 2:
            ratios = new_radius[useful_radius] / old_radius[useful_radius]
            ratios = ratios[np.isfinite(ratios)]
            if len(ratios):
                scale = float(np.median(ratios))
        scale = max(
            1.0 - self._maximum_scale_step,
            min(1.0 + self._maximum_scale_step, scale),
        )

        new_box = previous_box.transformed(
            dx,
            dy,
            scale,
            gray.shape[1],
            gray.shape[0],
        )
        if new_box is None:
            return None
        new_eyes = (
            self._eyes.transformed(
                previous_box.center,
                new_box.center,
                scale,
            )
            if self._eyes is not None
            else None
        )

        kept_points = new_inliers.reshape(-1, 1, 2).astype(np.float32)
        self._previous_gray = np.ascontiguousarray(gray).copy()
        self._box = new_box
        self._eyes = new_eyes
        if len(kept_points) < max(self._minimum_points * 2, 12):
            reseeded = self._features(gray, new_box)
            self._points = reseeded if reseeded is not None else kept_points
        else:
            self._points = kept_points

        valid_fraction = len(new_inliers) / max(1, len(old))
        median_error = (
            float(np.median(np.asarray(errors).reshape(-1)[valid][inliers]))
            if errors is not None
            else 0.0
        )
        quality = min(
            1.0,
            max(0.0, valid_fraction * math.exp(-median_error / 35.0)),
        )
        return FaceObservation(new_box, new_eyes, "flow", quality)


class CascadeFaceDetector:
    """Perform ROI-first periodic face and eye cascade corrections."""

    def __init__(
        self,
        face_classifier: object,
        eye_classifier: object,
        *,
        roi_expansion: float = 1.65,
    ) -> None:
        self._face_classifier = face_classifier
        self._eye_classifier = eye_classifier
        self._roi_expansion = max(1.0, float(roi_expansion))
        for name, classifier in (
            ("face", face_classifier),
            ("eye", eye_classifier),
        ):
            empty = getattr(classifier, "empty", None)
            if callable(empty) and bool(empty()):
                raise Cv2FallbackTrackingError(
                    f"OpenCV {name} cascade could not be loaded"
                )

    @staticmethod
    def _detect_multi_scale(
        classifier: object,
        image: np.ndarray,
        **kwargs: object,
    ) -> list[tuple[int, int, int, int]]:
        detect = getattr(classifier, "detectMultiScale", None)
        if not callable(detect):
            raise Cv2FallbackTrackingError(
                "OpenCV cascade does not expose detectMultiScale"
            )
        try:
            result = detect(image, **kwargs)
        except Exception as error:
            raise Cv2FallbackTrackingError(
                "OpenCV cascade detection failed"
            ) from error
        rectangles: list[tuple[int, int, int, int]] = []
        for rectangle in result if result is not None else ():
            try:
                values = tuple(int(value) for value in rectangle)
            except (TypeError, ValueError, OverflowError):
                continue
            if len(values) == 4 and values[2] > 0 and values[3] > 0:
                rectangles.append(values)
        return rectangles

    def _roi_faces(
        self,
        gray: np.ndarray,
        prior: FaceBox,
    ) -> list[tuple[int, int, int, int]]:
        roi_box = prior.expanded(
            self._roi_expansion,
            gray.shape[1],
            gray.shape[0],
        )
        if roi_box is None:
            return []
        x, y, width, height = roi_box.as_int_tuple()
        roi = gray[y : y + height, x : x + width]
        minimum = max(24, int(round(min(prior.width, prior.height) * 0.55)))
        local = self._detect_multi_scale(
            self._face_classifier,
            roi,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(minimum, minimum),
        )
        return [
            (local_x + x, local_y + y, local_width, local_height)
            for local_x, local_y, local_width, local_height in local
        ]

    def _full_faces(self, gray: np.ndarray) -> list[tuple[int, int, int, int]]:
        minimum = max(36, int(round(min(gray.shape[:2]) * 0.08)))
        return self._detect_multi_scale(
            self._face_classifier,
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(minimum, minimum),
        )

    def _eyes(self, gray: np.ndarray, face_box: FaceBox) -> EyePair | None:
        x, y, width, height = face_box.as_int_tuple()
        upper_height = max(1, int(round(height * 0.72)))
        roi = gray[y : y + upper_height, x : x + width]
        minimum = max(8, int(round(width * 0.08)))
        local = self._detect_multi_scale(
            self._eye_classifier,
            roi,
            scaleFactor=1.1,
            minNeighbors=4,
            minSize=(minimum, minimum),
        )
        global_rectangles = [
            (eye_x + x, eye_y + y, eye_width, eye_height)
            for eye_x, eye_y, eye_width, eye_height in local
        ]
        return select_eye_pair(global_rectangles, face_box)

    def detect(
        self,
        gray: np.ndarray,
        *,
        prior: FaceBox | None = None,
        allow_full_scan: bool = True,
    ) -> FaceObservation | None:
        candidates = self._roi_faces(gray, prior) if prior is not None else []
        if not candidates and allow_full_scan:
            candidates = self._full_faces(gray)
        selected = select_face_candidate(candidates, prior)
        if selected is None:
            return None
        clipped = selected.clipped(gray.shape[1], gray.shape[0])
        if clipped is None:
            return None
        return FaceObservation(
            clipped,
            self._eyes(gray, clipped),
            "cascade",
            1.0,
        )
