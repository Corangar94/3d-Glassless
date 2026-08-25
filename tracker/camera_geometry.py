"""Calibrated camera intrinsics and camera-to-screen geometry."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np


def _finite(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _vector(values: object, length: int, fallback: Sequence[float]) -> tuple[float, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return tuple(float(value) for value in fallback)
    parsed = tuple(_finite(value) for value in values[:length])
    if len(parsed) != length or any(value is None for value in parsed):
        return tuple(float(value) for value in fallback)
    return tuple(float(value) for value in parsed if value is not None)


def rotation_matrix_from_euler_degrees(
    yaw_deg: float = 0.0,
    pitch_deg: float = 0.0,
    roll_deg: float = 0.0,
) -> np.ndarray:
    """Build screen-frame rotation as yaw(Y), pitch(X), then roll(Z)."""
    yaw, pitch, roll = map(math.radians, (yaw_deg, pitch_deg, roll_deg))
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cr, sr = math.cos(roll), math.sin(roll)
    ry = np.array(((cy, 0.0, sy), (0.0, 1.0, 0.0), (-sy, 0.0, cy)))
    rx = np.array(((1.0, 0.0, 0.0), (0.0, cp, -sp), (0.0, sp, cp)))
    rz = np.array(((cr, -sr, 0.0), (sr, cr, 0.0), (0.0, 0.0, 1.0)))
    return rz @ rx @ ry


def euler_degrees_from_rotation_matrix(rotation: np.ndarray) -> tuple[float, float, float]:
    matrix = np.asarray(rotation, dtype=np.float64).reshape(3, 3)
    sy = math.hypot(float(matrix[0, 0]), float(matrix[1, 0]))
    if sy > 1e-8:
        pitch = math.atan2(float(matrix[2, 1]), float(matrix[2, 2]))
        yaw = math.atan2(-float(matrix[2, 0]), sy)
        roll = math.atan2(float(matrix[1, 0]), float(matrix[0, 0]))
    else:
        pitch = math.atan2(-float(matrix[1, 2]), float(matrix[1, 1]))
        yaw = math.atan2(-float(matrix[2, 0]), sy)
        roll = 0.0
    return tuple(math.degrees(value) for value in (yaw, pitch, roll))


@dataclass(frozen=True)
class CameraIntrinsics:
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    distortion: tuple[float, float, float, float, float] = (0.0, 0.0, 0.0, 0.0, 0.0)
    rms_error_px: float = 0.0

    def __post_init__(self) -> None:
        values = (self.fx, self.fy, self.cx, self.cy, *self.distortion, self.rms_error_px)
        if self.width <= 0 or self.height <= 0:
            raise ValueError("camera calibration dimensions must be positive")
        if self.fx <= 0.0 or self.fy <= 0.0 or not all(math.isfinite(value) for value in values):
            raise ValueError("camera calibration contains invalid intrinsics")

    @property
    def matrix(self) -> np.ndarray:
        return np.array(
            ((self.fx, 0.0, self.cx), (0.0, self.fy, self.cy), (0.0, 0.0, 1.0)),
            dtype=np.float64,
        )

    @property
    def distortion_array(self) -> np.ndarray:
        return np.asarray(self.distortion, dtype=np.float64).reshape(1, 5)

    def scaled(self, width: int, height: int) -> "CameraIntrinsics":
        if width <= 0 or height <= 0:
            raise ValueError("scaled calibration dimensions must be positive")
        scale_x = width / self.width
        scale_y = height / self.height
        return CameraIntrinsics(
            width=width,
            height=height,
            fx=self.fx * scale_x,
            fy=self.fy * scale_y,
            cx=self.cx * scale_x,
            cy=self.cy * scale_y,
            distortion=self.distortion,
            rms_error_px=self.rms_error_px,
        )

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "CameraIntrinsics | None":
        required = {name: _finite(mapping.get(name)) for name in ("fx", "fy", "cx", "cy")}
        width = int(_finite(mapping.get("width")) or 0)
        height = int(_finite(mapping.get("height")) or 0)
        if width <= 0 or height <= 0 or any(value is None for value in required.values()):
            return None
        distortion = _vector(mapping.get("distortion", ()), 5, (0.0,) * 5)
        rms = _finite(mapping.get("rms_error_px", 0.0)) or 0.0
        try:
            return cls(
                width=width,
                height=height,
                fx=float(required["fx"]),
                fy=float(required["fy"]),
                cx=float(required["cx"]),
                cy=float(required["cy"]),
                distortion=distortion,  # type: ignore[arg-type]
                rms_error_px=max(0.0, rms),
            )
        except (TypeError, ValueError):
            return None

    def to_mapping(self) -> dict[str, object]:
        return {
            "width": self.width,
            "height": self.height,
            "fx": self.fx,
            "fy": self.fy,
            "cx": self.cx,
            "cy": self.cy,
            "distortion": list(self.distortion),
            "rms_error_px": self.rms_error_px,
        }


@dataclass(frozen=True)
class CameraExtrinsics:
    rotation_camera_to_screen: tuple[float, ...] = (
        1.0, 0.0, 0.0,
        0.0, 1.0, 0.0,
        0.0, 0.0, 1.0,
    )
    translation_camera_origin_cm: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def __post_init__(self) -> None:
        if len(self.rotation_camera_to_screen) != 9:
            raise ValueError("camera-to-screen rotation requires nine values")
        rotation = self.rotation_matrix
        if not np.all(np.isfinite(rotation)) or abs(np.linalg.det(rotation)) < 0.5:
            raise ValueError("camera-to-screen rotation is invalid")
        if not all(math.isfinite(value) for value in self.translation_camera_origin_cm):
            raise ValueError("camera translation is invalid")

    @property
    def rotation_matrix(self) -> np.ndarray:
        return np.asarray(self.rotation_camera_to_screen, dtype=np.float64).reshape(3, 3)

    @property
    def translation_vector(self) -> np.ndarray:
        return np.asarray(self.translation_camera_origin_cm, dtype=np.float64)

    @classmethod
    def from_euler_and_translation(
        cls,
        *,
        yaw_deg: float = 0.0,
        pitch_deg: float = 0.0,
        roll_deg: float = 0.0,
        translation_cm: Sequence[float] = (0.0, 0.0, 0.0),
    ) -> "CameraExtrinsics":
        rotation = rotation_matrix_from_euler_degrees(yaw_deg, pitch_deg, roll_deg)
        translation = _vector(translation_cm, 3, (0.0, 0.0, 0.0))
        return cls(tuple(float(value) for value in rotation.ravel()), translation)  # type: ignore[arg-type]

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "CameraExtrinsics":
        rotation_values = mapping.get("rotation_camera_to_screen")
        if isinstance(rotation_values, Sequence) and len(rotation_values) >= 9:
            rotation = _vector(rotation_values, 9, tuple(np.eye(3).ravel()))
        else:
            rotation_deg = mapping.get("rotation_deg", {})
            if not isinstance(rotation_deg, Mapping):
                rotation_deg = {}
            rotation_matrix = rotation_matrix_from_euler_degrees(
                _finite(rotation_deg.get("yaw", 0.0)) or 0.0,
                _finite(rotation_deg.get("pitch", 0.0)) or 0.0,
                _finite(rotation_deg.get("roll", 0.0)) or 0.0,
            )
            rotation = tuple(float(value) for value in rotation_matrix.ravel())
        translation = _vector(
            mapping.get("translation_camera_origin_cm", mapping.get("translation_cm", ())),
            3,
            (0.0, 0.0, 0.0),
        )
        try:
            return cls(rotation, translation)  # type: ignore[arg-type]
        except ValueError:
            return cls()

    def to_mapping(self) -> dict[str, object]:
        yaw, pitch, roll = euler_degrees_from_rotation_matrix(self.rotation_matrix)
        return {
            "rotation_camera_to_screen": list(self.rotation_camera_to_screen),
            "rotation_deg": {"yaw": yaw, "pitch": pitch, "roll": roll},
            "translation_camera_origin_cm": list(self.translation_camera_origin_cm),
        }


@dataclass(frozen=True)
class CameraGeometry:
    intrinsics: CameraIntrinsics | None = None
    extrinsics: CameraExtrinsics = CameraExtrinsics()
    mirror_x: bool = True

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        fallback_width: int = 0,
        fallback_height: int = 0,
    ) -> "CameraGeometry | None":
        camera = config.get("camera", {})
        tracking = config.get("tracking", {})
        camera = camera if isinstance(camera, Mapping) else {}
        tracking = tracking if isinstance(tracking, Mapping) else {}
        calibration = tracking.get("camera_calibration", camera.get("calibration", {}))
        if not isinstance(calibration, Mapping):
            return None
        intrinsics_mapping = calibration.get("intrinsics", calibration)
        intrinsics = (
            CameraIntrinsics.from_mapping(intrinsics_mapping)
            if isinstance(intrinsics_mapping, Mapping)
            else None
        )
        extrinsics_mapping = calibration.get("extrinsics", {})
        extrinsics = (
            CameraExtrinsics.from_mapping(extrinsics_mapping)
            if isinstance(extrinsics_mapping, Mapping)
            else CameraExtrinsics()
        )
        mirror_x = bool(calibration.get("mirror_x", True))
        if intrinsics is None and fallback_width > 0 and fallback_height > 0:
            fov = _finite(tracking.get("camera_fov_deg", 90.0)) or 90.0
            if 0.0 < fov < 180.0:
                fx = fallback_width / (2.0 * math.tan(math.radians(fov / 2.0)))
                intrinsics = CameraIntrinsics(
                    width=fallback_width,
                    height=fallback_height,
                    fx=fx,
                    fy=fx,
                    cx=fallback_width / 2.0,
                    cy=fallback_height / 2.0,
                )
        if intrinsics is None and extrinsics == CameraExtrinsics():
            return None
        return cls(intrinsics=intrinsics, extrinsics=extrinsics, mirror_x=mirror_x)

    def rectified_pixels(
        self,
        points: Iterable[Sequence[float]],
        *,
        image_width: int,
        image_height: int,
    ) -> np.ndarray:
        array = np.asarray(list(points), dtype=np.float64).reshape(-1, 1, 2)
        if self.intrinsics is None:
            return array.reshape(-1, 2)
        scaled = self.intrinsics.scaled(image_width, image_height)
        if np.allclose(scaled.distortion_array, 0.0):
            return array.reshape(-1, 2)
        return cv2.undistortPoints(
            array,
            scaled.matrix,
            scaled.distortion_array,
            P=scaled.matrix,
        ).reshape(-1, 2)

    def focal_lengths(self, image_width: int, image_height: int) -> tuple[float, float]:
        if self.intrinsics is None:
            raise ValueError("camera intrinsics are unavailable")
        scaled = self.intrinsics.scaled(image_width, image_height)
        return scaled.fx, scaled.fy

    def pixel_depth_to_screen(
        self,
        pixel_x: float,
        pixel_y: float,
        depth_cm: float,
        *,
        image_width: int,
        image_height: int,
    ) -> tuple[float, float, float]:
        if depth_cm <= 0.0 or not math.isfinite(depth_cm):
            raise ValueError("depth_cm must be finite and positive")
        if self.intrinsics is None:
            raise ValueError("camera intrinsics are unavailable")
        scaled = self.intrinsics.scaled(image_width, image_height)
        rectified = self.rectified_pixels(
            ((pixel_x, pixel_y),), image_width=image_width, image_height=image_height
        )[0]
        x = (float(rectified[0]) - scaled.cx) / scaled.fx * depth_cm
        y = -(float(rectified[1]) - scaled.cy) / scaled.fy * depth_cm
        if self.mirror_x:
            x = -x
        camera_point = np.array((x, y, depth_cm), dtype=np.float64)
        screen_point = (
            self.extrinsics.rotation_matrix @ camera_point
            + self.extrinsics.translation_vector
        )
        return tuple(float(value) for value in screen_point)

    def orientation_to_screen(
        self,
        yaw_deg: float,
        pitch_deg: float,
        roll_deg: float,
    ) -> tuple[float, float, float]:
        face = rotation_matrix_from_euler_degrees(yaw_deg, pitch_deg, roll_deg)
        return euler_degrees_from_rotation_matrix(
            self.extrinsics.rotation_matrix @ face
        )

    def with_center_alignment(
        self,
        camera_points_cm: Iterable[Sequence[float]],
        *,
        desired_screen_position_cm: Sequence[float],
    ) -> "CameraGeometry":
        points = np.asarray(list(camera_points_cm), dtype=np.float64)
        if points.ndim != 2 or points.shape[0] < 3 or points.shape[1] != 3:
            raise ValueError("at least three 3D samples are required for center alignment")
        if not np.all(np.isfinite(points)):
            raise ValueError("center alignment samples contain non-finite values")
        median_camera = np.median(points, axis=0)
        desired = np.asarray(desired_screen_position_cm, dtype=np.float64).reshape(3)
        translation = desired - self.extrinsics.rotation_matrix @ median_camera
        extrinsics = CameraExtrinsics(
            rotation_camera_to_screen=self.extrinsics.rotation_camera_to_screen,
            translation_camera_origin_cm=tuple(float(value) for value in translation),
        )
        return CameraGeometry(
            intrinsics=self.intrinsics,
            extrinsics=extrinsics,
            mirror_x=self.mirror_x,
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "intrinsics": self.intrinsics.to_mapping() if self.intrinsics else None,
            "extrinsics": self.extrinsics.to_mapping(),
            "mirror_x": self.mirror_x,
        }
