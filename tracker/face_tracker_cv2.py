# tracker/face_tracker_cv2.py
"""Pure-OpenCV face tracker — no mediapipe, no DirectML, no GPU initialisation.

Identical HeadPosition / FaceTracker interface to face_tracker.py so
tracker/main.py can import either interchangeably.

Detection strategy:
  1. Haar-cascade face detector (ships with OpenCV, no download needed).
  2. Haar-cascade eye detector inside the face ROI.
  3. If two eyes are found, use their pixel separation for IPD-based depth
     (same math as the mediapipe tracker).
  4. If only the face bounding box is available, estimate depth from face
     width (face_width ≈ 2.5 × IPD empirically).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


@dataclass(frozen=True)
class HeadPosition:
    x_cm: float
    y_cm: float
    z_cm: float


class FaceTracker:
    """Wraps OpenCV Haar cascades and converts detections to head pose in cm."""

    def __init__(
        self,
        real_ipd_cm: float,
        screen_width_cm: float,
        screen_height_cm: float,
        camera_fov_deg: float = 60.0,
        model_path: str = "",          # accepted for API compat, unused
    ) -> None:
        if not (0.0 < camera_fov_deg < 180.0):
            raise ValueError(f"camera_fov_deg must be in (0, 180), got {camera_fov_deg}")
        self._real_ipd_cm = real_ipd_cm
        self._camera_fov_deg = camera_fov_deg

        data = cv2.data.haarcascades
        self._face_cc = cv2.CascadeClassifier(
            data + "haarcascade_frontalface_default.xml"
        )
        self._eye_cc = cv2.CascadeClassifier(
            data + "haarcascade_eye.xml"
        )

    def process_frame(self, frame_bgr: np.ndarray) -> Optional[HeadPosition]:
        """Process one BGR frame. Returns None if no face detected."""
        h, w = frame_bgr.shape[:2]
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        cv2.equalizeHist(gray, gray)  # normalise brightness in-place

        faces = self._face_cc.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)
        )
        if not len(faces):
            return None

        # Use the largest detected face
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])

        focal_px = w / (2.0 * math.tan(math.radians(self._camera_fov_deg / 2.0)))

        # --- eye detection inside face ROI --------------------------------
        roi = gray[fy : fy + fh, fx : fx + fw]
        eyes = self._eye_cc.detectMultiScale(
            roi, scaleFactor=1.1, minNeighbors=5, minSize=(20, 20)
        )

        ipd_px: Optional[float] = None
        cx_norm: float
        cy_norm: float

        if len(eyes) >= 2:
            eyes_by_x = sorted(eyes, key=lambda e: e[0])
            el, er = eyes_by_x[0], eyes_by_x[-1]
            lx = fx + el[0] + el[2] // 2
            ly = fy + el[1] + el[3] // 2
            rx = fx + er[0] + er[2] // 2
            ry = fy + er[1] + er[3] // 2
            ipd_px = float(abs(rx - lx))
            cx_norm = ((lx + rx) / 2.0) / w
            cy_norm = ((ly + ry) / 2.0) / h
        else:
            cx_norm = (fx + fw / 2.0) / w
            cy_norm = (fy + fh / 2.0) / h

        # --- depth estimation -----------------------------------------
        if ipd_px and ipd_px > 10.0:
            z_cm = (focal_px * self._real_ipd_cm) / ipd_px
        else:
            # face_width ≈ 2.5 × IPD for typical adult faces
            z_cm = (focal_px * self._real_ipd_cm * 2.5) / max(fw, 1.0)

        # --- X/Y offset from screen centre ----------------------------
        aspect = w / max(h, 1)
        phys_half_w = z_cm * math.tan(math.radians(self._camera_fov_deg / 2.0))
        phys_half_h = phys_half_w / aspect
        x_cm = -((cx_norm - 0.5) * 2.0 * phys_half_w)
        y_cm = -((cy_norm - 0.5) * 2.0 * phys_half_h)

        return HeadPosition(x_cm=x_cm, y_cm=y_cm, z_cm=z_cm)

    def close(self) -> None:
        pass

    def __enter__(self) -> "FaceTracker":
        return self

    def __exit__(self, *_: object) -> None:
        pass
