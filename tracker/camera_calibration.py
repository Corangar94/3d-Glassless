"""Checkerboard intrinsics and guided camera-to-screen alignment."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import statistics
import time
from typing import Iterable, Sequence

import cv2
import numpy as np
import yaml

from tracker.camera_geometry import (
    CameraExtrinsics,
    CameraGeometry,
    CameraIntrinsics,
)


@dataclass(frozen=True)
class CheckerboardObservation:
    image_size: tuple[int, int]
    corners: np.ndarray
    source: str = ""

    @property
    def centroid(self) -> tuple[float, float]:
        points = self.corners.reshape(-1, 2)
        center = np.mean(points, axis=0)
        return float(center[0]), float(center[1])

    @property
    def coverage(self) -> float:
        points = self.corners.reshape(-1, 2)
        minimum = np.min(points, axis=0)
        maximum = np.max(points, axis=0)
        width, height = self.image_size
        return float(
            max(0.0, maximum[0] - minimum[0])
            * max(0.0, maximum[1] - minimum[1])
            / max(1.0, width * height)
        )


@dataclass(frozen=True)
class CalibrationResult:
    intrinsics: CameraIntrinsics
    views_used: int
    mean_reprojection_error_px: float
    max_reprojection_error_px: float

    def to_mapping(self) -> dict[str, object]:
        mapping = self.intrinsics.to_mapping()
        mapping.update(
            {
                "views_used": self.views_used,
                "mean_reprojection_error_px": self.mean_reprojection_error_px,
                "max_reprojection_error_px": self.max_reprojection_error_px,
            }
        )
        return mapping


def parse_pattern_size(value: str | Sequence[int]) -> tuple[int, int]:
    if isinstance(value, str):
        normalized = value.lower().replace("×", "x")
        parts = normalized.split("x")
        if len(parts) != 2:
            raise ValueError("pattern size must look like 9x6")
        columns, rows = (int(part.strip()) for part in parts)
    else:
        if len(value) != 2:
            raise ValueError("pattern size requires columns and rows")
        columns, rows = int(value[0]), int(value[1])
    if columns < 3 or rows < 3:
        raise ValueError("checkerboard requires at least 3x3 inner corners")
    return columns, rows


def checkerboard_object_points(
    pattern_size: tuple[int, int],
    square_size_cm: float,
) -> np.ndarray:
    columns, rows = pattern_size
    if square_size_cm <= 0.0 or not math.isfinite(square_size_cm):
        raise ValueError("square_size_cm must be finite and positive")
    points = np.zeros((columns * rows, 3), np.float32)
    points[:, :2] = np.mgrid[0:columns, 0:rows].T.reshape(-1, 2)
    points[:, :2] *= float(square_size_cm)
    return points


def detect_checkerboard(
    frame_bgr: np.ndarray,
    pattern_size: tuple[int, int],
    *,
    source: str = "",
) -> CheckerboardObservation | None:
    if frame_bgr.ndim != 3 or frame_bgr.shape[0] <= 0 or frame_bgr.shape[1] <= 0:
        return None
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    flags = cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY | cv2.CALIB_CB_NORMALIZE_IMAGE
    found, corners = cv2.findChessboardCornersSB(gray, pattern_size, flags=flags)
    if not found or corners is None:
        return None
    return CheckerboardObservation(
        image_size=(frame_bgr.shape[1], frame_bgr.shape[0]),
        corners=np.asarray(corners, dtype=np.float32).reshape(-1, 1, 2),
        source=source,
    )


def observation_is_diverse(
    candidate: CheckerboardObservation,
    accepted: Sequence[CheckerboardObservation],
    *,
    minimum_centroid_fraction: float = 0.08,
    minimum_coverage_delta: float = 0.025,
) -> bool:
    if not accepted:
        return True
    width, height = candidate.image_size
    candidate_center = np.asarray(candidate.centroid)
    for previous in accepted:
        if previous.image_size != candidate.image_size:
            continue
        center_distance = np.linalg.norm(
            (candidate_center - np.asarray(previous.centroid))
            / np.asarray((max(1, width), max(1, height)))
        )
        coverage_delta = abs(candidate.coverage - previous.coverage)
        if (
            center_distance < minimum_centroid_fraction
            and coverage_delta < minimum_coverage_delta
        ):
            return False
    return True


def calibrate_intrinsics(
    observations: Sequence[CheckerboardObservation],
    *,
    pattern_size: tuple[int, int],
    square_size_cm: float,
    reject_outliers: bool = True,
) -> CalibrationResult:
    if len(observations) < 6:
        raise ValueError("at least six diverse checkerboard observations are required")
    image_size = observations[0].image_size
    if any(observation.image_size != image_size for observation in observations):
        raise ValueError("all calibration observations must have the same image size")
    object_template = checkerboard_object_points(pattern_size, square_size_cm)

    def solve(selected: Sequence[CheckerboardObservation]):
        object_points = [object_template.copy() for _ in selected]
        image_points = [observation.corners for observation in selected]
        camera_matrix = cv2.initCameraMatrix2D(
            object_points,
            image_points,
            image_size,
            aspectRatio=1.0,
        )
        flags = cv2.CALIB_RATIONAL_MODEL
        rms, matrix, distortion, rotation_vectors, translation_vectors = cv2.calibrateCamera(
            object_points,
            image_points,
            image_size,
            camera_matrix,
            None,
            flags=flags,
        )
        errors: list[float] = []
        for object_values, observation, rotation, translation in zip(
            object_points,
            selected,
            rotation_vectors,
            translation_vectors,
            strict=True,
        ):
            projected, _ = cv2.projectPoints(
                object_values,
                rotation,
                translation,
                matrix,
                distortion,
            )
            difference = projected.reshape(-1, 2) - observation.corners.reshape(-1, 2)
            errors.append(float(np.sqrt(np.mean(np.sum(difference * difference, axis=1)))))
        return float(rms), matrix, distortion, errors

    selected = list(observations)
    rms, matrix, distortion, errors = solve(selected)
    if reject_outliers and len(selected) >= 10:
        median = statistics.median(errors)
        deviations = [abs(error - median) for error in errors]
        mad = statistics.median(deviations) or 1e-6
        threshold = median + 3.5 * mad
        retained = [
            observation
            for observation, error in zip(selected, errors, strict=True)
            if error <= threshold
        ]
        if len(retained) >= 6 and len(retained) < len(selected):
            selected = retained
            rms, matrix, distortion, errors = solve(selected)

    coefficients = np.asarray(distortion, dtype=np.float64).ravel()
    first_five = tuple(float(coefficients[index]) if index < coefficients.size else 0.0 for index in range(5))
    intrinsics = CameraIntrinsics(
        width=image_size[0],
        height=image_size[1],
        fx=float(matrix[0, 0]),
        fy=float(matrix[1, 1]),
        cx=float(matrix[0, 2]),
        cy=float(matrix[1, 2]),
        distortion=first_five,  # type: ignore[arg-type]
        rms_error_px=float(rms),
    )
    return CalibrationResult(
        intrinsics=intrinsics,
        views_used=len(selected),
        mean_reprojection_error_px=float(statistics.mean(errors)),
        max_reprojection_error_px=float(max(errors)),
    )


def load_checkerboard_observations(
    paths: Iterable[str | Path],
    pattern_size: tuple[int, int],
) -> list[CheckerboardObservation]:
    observations: list[CheckerboardObservation] = []
    for value in paths:
        path = Path(value)
        frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if frame is None:
            continue
        observation = detect_checkerboard(frame, pattern_size, source=str(path))
        if observation is not None:
            observations.append(observation)
    return observations


def capture_checkerboard_observations(
    *,
    camera_index: int,
    pattern_size: tuple[int, int],
    sample_count: int = 18,
    width: int = 1280,
    height: int = 720,
    fps: float = 30.0,
    timeout_seconds: float = 90.0,
    show_preview: bool = True,
) -> list[CheckerboardObservation]:
    cap = cv2.VideoCapture(camera_index, cv2.CAP_MSMF)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        cap.release()
        raise RuntimeError(f"could not open camera {camera_index}")
    for property_id, value in (
        (cv2.CAP_PROP_FRAME_WIDTH, width),
        (cv2.CAP_PROP_FRAME_HEIGHT, height),
        (cv2.CAP_PROP_FPS, fps),
        (getattr(cv2, "CAP_PROP_BUFFERSIZE", -1), 1),
    ):
        if property_id >= 0 and value > 0:
            cap.set(property_id, float(value))
    accepted: list[CheckerboardObservation] = []
    last_accept = 0.0
    started = time.monotonic()
    window_name = "Glassless3D camera calibration"
    try:
        while len(accepted) < sample_count and time.monotonic() - started < timeout_seconds:
            ok, frame = cap.read()
            if not ok:
                continue
            observation = detect_checkerboard(frame, pattern_size)
            now = time.monotonic()
            if (
                observation is not None
                and now - last_accept >= 0.45
                and observation.coverage >= 0.035
                and observation_is_diverse(observation, accepted)
            ):
                accepted.append(observation)
                last_accept = now
            if show_preview:
                preview = frame.copy()
                if observation is not None:
                    cv2.drawChessboardCorners(
                        preview,
                        pattern_size,
                        observation.corners,
                        True,
                    )
                cv2.putText(
                    preview,
                    f"views {len(accepted)}/{sample_count} - move/tilt board - Esc cancels",
                    (20, 35),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 255, 0),
                    2,
                    cv2.LINE_AA,
                )
                cv2.imshow(window_name, preview)
                if cv2.waitKey(1) & 0xFF == 27:
                    raise KeyboardInterrupt
    finally:
        cap.release()
        if show_preview:
            cv2.destroyWindow(window_name)
    if len(accepted) < 6:
        raise RuntimeError(f"only captured {len(accepted)} usable checkerboard views")
    return accepted


def center_align_geometry(
    geometry: CameraGeometry,
    camera_points_cm: Iterable[Sequence[float]],
    *,
    viewer_distance_cm: float,
) -> CameraGeometry:
    if viewer_distance_cm <= 0.0 or not math.isfinite(viewer_distance_cm):
        raise ValueError("viewer_distance_cm must be finite and positive")
    return geometry.with_center_alignment(
        camera_points_cm,
        desired_screen_position_cm=(0.0, 0.0, viewer_distance_cm),
    )


def update_config_camera_geometry(
    config_path: str | Path,
    geometry: CameraGeometry,
    *,
    calibration_result: CalibrationResult | None = None,
) -> None:
    path = Path(config_path)
    if path.exists():
        with path.open(encoding="utf-8") as stream:
            loaded = yaml.safe_load(stream)
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ValueError("config top level must be a mapping")
    else:
        loaded = {}
    tracking = loaded.setdefault("tracking", {})
    if not isinstance(tracking, dict):
        raise ValueError("tracking config must be a mapping")
    mapping = geometry.to_mapping()
    if calibration_result is not None:
        mapping["quality"] = {
            "views_used": calibration_result.views_used,
            "mean_reprojection_error_px": calibration_result.mean_reprojection_error_px,
            "max_reprojection_error_px": calibration_result.max_reprojection_error_px,
        }
    tracking["camera_calibration"] = mapping
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        yaml.safe_dump(loaded, stream, sort_keys=False)
    temporary.replace(path)


def generated_checkerboard_image(
    *,
    pattern_size: tuple[int, int],
    square_pixels: int = 120,
    margin_pixels: int = 120,
) -> np.ndarray:
    columns, rows = pattern_size
    if square_pixels < 8 or margin_pixels < 0:
        raise ValueError("invalid checkerboard image dimensions")
    squares_x, squares_y = columns + 1, rows + 1
    width = squares_x * square_pixels + 2 * margin_pixels
    height = squares_y * square_pixels + 2 * margin_pixels
    image = np.full((height, width), 255, dtype=np.uint8)
    for row in range(squares_y):
        for column in range(squares_x):
            if (row + column) % 2 == 0:
                x0 = margin_pixels + column * square_pixels
                y0 = margin_pixels + row * square_pixels
                image[y0 : y0 + square_pixels, x0 : x0 + square_pixels] = 0
    return image
