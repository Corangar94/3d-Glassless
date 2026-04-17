# launcher/calibration.py
"""One-shot hardware calibration helpers."""
from __future__ import annotations

import ctypes
import logging
import math

_log = logging.getLogger(__name__)

_HORZSIZE   = 4
_VERTSIZE   = 6
_HORZRES    = 8
_VERTRES    = 10
_LOGPIXELSX = 88
_LOGPIXELSY = 90

_LEFT_IRIS  = 468
_RIGHT_IRIS = 473

# Typical webcam FOV — used for focal-length estimation in head distance calc.
_ASSUMED_FOV_DEG = 90.0


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
        wmm   = gdi32.GetDeviceCaps(hdc, _HORZSIZE)
        hmm   = gdi32.GetDeviceCaps(hdc, _VERTSIZE)
        px_w  = gdi32.GetDeviceCaps(hdc, _HORZRES)
        px_h  = gdi32.GetDeviceCaps(hdc, _VERTRES)
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


def _detect_face_distance(frame_bgr: "np.ndarray", ipd_mm: float) -> float | None:
    """Run MediaPipe on one BGR frame, return head distance in cm or None."""
    import cv2
    import numpy as np
    import mediapipe as mp
    from mediapipe import tasks
    import pathlib

    model_path = str(
        pathlib.Path(__file__).resolve().parent.parent
        / "models" / "face_landmarker.task"
    )
    options = tasks.vision.FaceLandmarkerOptions(
        base_options=tasks.BaseOptions(model_asset_path=model_path),
        running_mode=tasks.vision.RunningMode.IMAGE,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
    )
    with tasks.vision.FaceLandmarker.create_from_options(options) as lmk:
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        result = lmk.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))

    if not result.face_landmarks:
        return None

    lm = result.face_landmarks[0]
    left  = np.array([lm[_LEFT_IRIS].x * w,  lm[_LEFT_IRIS].y  * h])
    right = np.array([lm[_RIGHT_IRIS].x * w, lm[_RIGHT_IRIS].y * h])
    ipd_px = float(np.linalg.norm(right - left))
    if ipd_px < 1.0:
        return None

    focal_px = w / (2.0 * math.tan(math.radians(_ASSUMED_FOV_DEG / 2.0)))
    return (focal_px * (ipd_mm / 10.0)) / ipd_px


def measure_head_distance_or_none(ipd_mm: float = 64.0) -> float | None:
    """Return estimated head distance in cm, or None when measurement fails."""
    import cv2
    cap = cv2.VideoCapture(0)
    try:
        if not cap.isOpened():
            return None
        ok, frame = cap.read()
        if not ok:
            return None
        return _detect_face_distance(frame, ipd_mm)
    except Exception:  # noqa: BLE001
        _log.warning("measure_head_distance_or_none failed", exc_info=True)
        return None
    finally:
        cap.release()


def measure_head_distance(ipd_mm: float = 64.0) -> float:
    """Return estimated head distance in cm (60.0 fallback on any failure)."""
    result = measure_head_distance_or_none(ipd_mm)
    return result if result is not None else 60.0
