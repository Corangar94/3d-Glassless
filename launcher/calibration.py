# launcher/calibration.py
"""One-shot hardware calibration helpers."""
from __future__ import annotations

import ctypes
import logging
import math
import statistics
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

_log = logging.getLogger(__name__)

_HORZSIZE = 4
_VERTSIZE = 6
_HORZRES = 8
_VERTRES = 10
_LOGPIXELSX = 88
_LOGPIXELSY = 90

_LEFT_IRIS = 468
_RIGHT_IRIS = 473

# Typical webcam FOV — used for focal-length estimation in head distance calc.
_ASSUMED_FOV_DEG = 90.0


def _finite_positive_float(value: object) -> float | None:
    """Return a finite positive float, or None for malformed input."""
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed) or parsed <= 0.0:
        return None
    return parsed


def _validated_camera_parameters(
    ipd_mm: object,
    camera_fov_deg: object,
) -> tuple[float, float] | None:
    """Validate physical calibration values before using projection math."""
    ipd = _finite_positive_float(ipd_mm)
    fov = _finite_positive_float(camera_fov_deg)
    if ipd is None or fov is None or not (1.0 <= fov <= 179.0):
        return None
    return ipd, fov


def detect_screen_cm() -> tuple[float, float]:
    """Return (width_cm, height_cm) of the primary monitor via EDID/DPI.

    Returns (0.0, 0.0) on failure.
    """
    try:
        gdi32 = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        hdc = user32.GetDC(None)
        if not hdc:
            return 0.0, 0.0
        wmm = gdi32.GetDeviceCaps(hdc, _HORZSIZE)
        hmm = gdi32.GetDeviceCaps(hdc, _VERTSIZE)
        px_w = gdi32.GetDeviceCaps(hdc, _HORZRES)
        px_h = gdi32.GetDeviceCaps(hdc, _VERTRES)
        dpi_x = gdi32.GetDeviceCaps(hdc, _LOGPIXELSX)
        dpi_y = gdi32.GetDeviceCaps(hdc, _LOGPIXELSY)
        user32.ReleaseDC(None, hdc)
        edid_ok = wmm > 150 and hmm > 100 and not (wmm == 320 and hmm == 240)
        if edid_ok:
            return wmm / 10.0, hmm / 10.0
        if dpi_x > 0 and dpi_y > 0 and px_w > 0 and px_h > 0:
            return px_w / dpi_x * 2.54, px_h / dpi_y * 2.54
        return 0.0, 0.0
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


def _create_face_landmarker():
    """Create one IMAGE-mode landmarker for a calibration sampling session."""
    from mediapipe import tasks
    import pathlib

    model_path = str(
        pathlib.Path(__file__).resolve().parent.parent
        / "models"
        / "face_landmarker.task"
    )
    options = tasks.vision.FaceLandmarkerOptions(
        base_options=tasks.BaseOptions(model_asset_path=model_path),
        running_mode=tasks.vision.RunningMode.IMAGE,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
    )
    return tasks.vision.FaceLandmarker.create_from_options(options)


def _detect_face_distance_with_landmarker(
    frame_bgr: "np.ndarray",
    ipd_mm: float,
    landmarker: object,
    camera_fov_deg: float = _ASSUMED_FOV_DEG,
) -> float | None:
    calibration = _validated_camera_parameters(ipd_mm, camera_fov_deg)
    if calibration is None:
        return None
    valid_ipd_mm, valid_fov_deg = calibration

    import cv2
    import mediapipe as mp
    import numpy as np

    try:
        h, w = frame_bgr.shape[:2]
    except (AttributeError, TypeError, ValueError):
        return None
    if h <= 0 or w <= 0:
        return None

    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    result = landmarker.detect(  # type: ignore[attr-defined]
        mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    )

    if not result.face_landmarks:
        return None

    lm = result.face_landmarks[0]
    left = np.array([lm[_LEFT_IRIS].x * w, lm[_LEFT_IRIS].y * h])
    right = np.array([lm[_RIGHT_IRIS].x * w, lm[_RIGHT_IRIS].y * h])
    ipd_px = float(np.linalg.norm(right - left))
    if not math.isfinite(ipd_px) or ipd_px < 1.0:
        return None

    focal_px = w / (2.0 * math.tan(math.radians(valid_fov_deg / 2.0)))
    distance_cm = (focal_px * (valid_ipd_mm / 10.0)) / ipd_px
    if not math.isfinite(distance_cm) or distance_cm <= 0.0:
        return None
    return distance_cm


def _detect_face_distance(
    frame_bgr: "np.ndarray",
    ipd_mm: float,
    camera_fov_deg: float = _ASSUMED_FOV_DEG,
) -> float | None:
    """Run MediaPipe on one BGR frame, return head distance in cm or None."""
    with _create_face_landmarker() as landmarker:
        return _detect_face_distance_with_landmarker(
            frame_bgr,
            ipd_mm,
            landmarker,
            camera_fov_deg,
        )


def measure_head_distance_or_none(
    ipd_mm: float = 64.0,
    camera_index: int = 0,
    sample_count: int = 7,
    camera_fov_deg: float = _ASSUMED_FOV_DEG,
) -> float | None:
    """Return a robust multi-frame estimate from the selected camera."""
    calibration = _validated_camera_parameters(ipd_mm, camera_fov_deg)
    if calibration is None:
        return None
    valid_ipd_mm, valid_fov_deg = calibration

    import cv2

    cap = cv2.VideoCapture(camera_index)
    try:
        if not cap.isOpened():
            return None
        distances: list[float] = []
        required_samples = max(1, int(sample_count))
        with _create_face_landmarker() as landmarker:
            for _ in range(required_samples * 3):
                ok, frame = cap.read()
                if not ok:
                    continue
                distance = _detect_face_distance_with_landmarker(
                    frame,
                    valid_ipd_mm,
                    landmarker,
                    valid_fov_deg,
                )
                if distance is not None and 20.0 <= distance <= 200.0:
                    distances.append(distance)
                    if len(distances) >= required_samples:
                        break
        return statistics.median(distances) if distances else None
    except Exception:  # noqa: BLE001
        _log.warning("measure_head_distance_or_none failed", exc_info=True)
        return None
    finally:
        cap.release()


def measure_head_distance(
    ipd_mm: float = 64.0,
    camera_index: int = 0,
    camera_fov_deg: float = _ASSUMED_FOV_DEG,
) -> float:
    """Return estimated head distance in cm (60.0 fallback on failure)."""
    result = measure_head_distance_or_none(
        ipd_mm,
        camera_index=camera_index,
        camera_fov_deg=camera_fov_deg,
    )
    return result if result is not None else 60.0
