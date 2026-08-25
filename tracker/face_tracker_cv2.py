# tracker/face_tracker_cv2.py
"""Pure-OpenCV fallback tracker with the timestamped pose interface."""
from __future__ import annotations

import math
from typing import Optional

import cv2
import numpy as np

from tracker.camera_geometry import CameraGeometry
from tracker.pose import HeadPosition, monotonic_ms


class FaceTracker:
    def __init__(
        self,
        real_ipd_cm: float,
        screen_width_cm: float,
        screen_height_cm: float,
        camera_fov_deg: float = 60.0,
        model_path: str = "",
        camera_geometry: CameraGeometry | None = None,
        **_options: object,
    ) -> None:
        if not (0.0 < camera_fov_deg < 180.0):
            raise ValueError(f"camera_fov_deg must be in (0, 180), got {camera_fov_deg}")
        self._real_ipd_cm = float(real_ipd_cm)
        self._camera_fov_deg = float(camera_fov_deg)
        self._camera_geometry = camera_geometry
        cv2_data = getattr(cv2, "data", None)
        data = str(getattr(cv2_data, "haarcascades", ""))
        self._face_cc = cv2.CascadeClassifier(
            data + "haarcascade_frontalface_default.xml"
        )
        self._eye_cc = cv2.CascadeClassifier(data + "haarcascade_eye.xml")

    def set_calibration(
        self,
        *,
        real_ipd_cm: float | None = None,
        camera_fov_deg: float | None = None,
    ) -> None:
        if real_ipd_cm is not None and real_ipd_cm > 0.0:
            self._real_ipd_cm = float(real_ipd_cm)
        if camera_fov_deg is not None:
            if not (0.0 < camera_fov_deg < 180.0):
                raise ValueError(
                    f"camera_fov_deg must be in (0, 180), got {camera_fov_deg}"
                )
            self._camera_fov_deg = float(camera_fov_deg)

    def process_frame(
        self,
        frame_bgr: np.ndarray,
        capture_timestamp_ms: int | None = None,
    ) -> Optional[HeadPosition]:
        h, w = frame_bgr.shape[:2]
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        cv2.equalizeHist(gray, gray)
        faces = self._face_cc.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)
        )
        if not len(faces):
            return None

        fx, fy, fw, fh = max(faces, key=lambda face: face[2] * face[3])
        geometry = self._camera_geometry
        focal_px = (
            geometry.focal_lengths(w, h)[0]
            if geometry is not None and geometry.intrinsics is not None
            else w / (2.0 * math.tan(math.radians(self._camera_fov_deg / 2.0)))
        )
        roi = gray[fy : fy + fh, fx : fx + fw]
        eyes = self._eye_cc.detectMultiScale(
            roi, scaleFactor=1.1, minNeighbors=5, minSize=(20, 20)
        )

        ipd_px: Optional[float] = None
        confidence = 0.45
        if len(eyes) >= 2:
            eyes_by_x = sorted(eyes, key=lambda eye: eye[0])
            left, right = eyes_by_x[0], eyes_by_x[-1]
            lx = fx + left[0] + left[2] // 2
            ly = fy + left[1] + left[3] // 2
            rx = fx + right[0] + right[2] // 2
            ry = fy + right[1] + right[3] // 2
            ipd_px = float(abs(rx - lx))
            cx_norm = ((lx + rx) / 2.0) / w
            cy_norm = ((ly + ry) / 2.0) / h
            confidence = 0.70
        else:
            cx_norm = (fx + fw / 2.0) / w
            cy_norm = (fy + fh / 2.0) / h

        if ipd_px and ipd_px > 10.0:
            z_cm = (focal_px * self._real_ipd_cm) / ipd_px
        else:
            z_cm = (focal_px * self._real_ipd_cm * 2.5) / max(fw, 1.0)
        if geometry is not None and geometry.intrinsics is not None:
            x_cm, y_cm, screen_z_cm = geometry.pixel_depth_to_screen(
                cx_norm * w,
                cy_norm * h,
                z_cm,
                image_width=w,
                image_height=h,
            )
        else:
            aspect = w / max(h, 1)
            phys_half_w = z_cm * math.tan(math.radians(self._camera_fov_deg / 2.0))
            phys_half_h = phys_half_w / aspect
            x_cm = -((cx_norm - 0.5) * 2.0 * phys_half_w)
            y_cm = -((cy_norm - 0.5) * 2.0 * phys_half_h)
            screen_z_cm = z_cm
        return HeadPosition(
            x_cm=x_cm,
            y_cm=y_cm,
            z_cm=screen_z_cm,
            confidence=confidence,
            capture_timestamp_ms=(
                monotonic_ms()
                if capture_timestamp_ms is None
                else int(capture_timestamp_ms) & 0xFFFF_FFFF
            ),
        )

    def close(self) -> None:
        pass

    def __enter__(self) -> "FaceTracker":
        return self

    def __exit__(self, *_: object) -> None:
        pass
