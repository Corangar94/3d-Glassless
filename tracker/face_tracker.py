# tracker/face_tracker.py
import math
from dataclasses import dataclass

import cv2
import mediapipe as mp
import numpy as np

# MediaPipe landmark indices (with refine_landmarks=True, 478 total)
_NOSE_TIP = 1
_LEFT_IRIS_CENTER = 468   # available only with refine_landmarks=True
_RIGHT_IRIS_CENTER = 473  # available only with refine_landmarks=True


@dataclass(frozen=True)
class HeadPosition:
    x_cm: float   # right = positive, left = negative
    y_cm: float   # up = positive, down = negative (flipped from image coords)
    z_cm: float   # distance from screen (always positive)


def estimate_z_cm(
    ipd_px: float,
    image_width: int,
    real_ipd_cm: float,
    camera_fov_deg: float,
) -> float:
    """Estimate head Z distance using inter-iris pixel distance."""
    focal_px = image_width / (2.0 * math.tan(math.radians(camera_fov_deg / 2.0)))
    return (focal_px * real_ipd_cm) / max(ipd_px, 1.0)


def estimate_xy_cm(
    nose_x_norm: float,
    nose_y_norm: float,
    screen_width_cm: float,
    screen_height_cm: float,
) -> tuple[float, float]:
    """Convert normalised nose position to cm offset from screen centre."""
    x_cm = (nose_x_norm - 0.5) * screen_width_cm
    y_cm = -((nose_y_norm - 0.5) * screen_height_cm)  # flip Y: up is positive
    return x_cm, y_cm


class FaceTracker:
    """Wraps MediaPipe FaceMesh and converts landmarks to head pose in cm."""

    def __init__(
        self,
        real_ipd_cm: float,
        screen_width_cm: float,
        screen_height_cm: float,
        camera_fov_deg: float = 60.0,
    ):
        if not (0.0 < camera_fov_deg < 180.0):
            raise ValueError(f"camera_fov_deg must be in (0, 180), got {camera_fov_deg}")
        self._real_ipd_cm = real_ipd_cm
        self._screen_width_cm = screen_width_cm
        self._screen_height_cm = screen_height_cm
        self._camera_fov_deg = camera_fov_deg
        self._face_mesh = mp.solutions.face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,   # enables iris detection (landmarks 468-477)
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

    def process_frame(self, frame_bgr: np.ndarray) -> HeadPosition | None:
        """Process one BGR camera frame. Returns None if no face detected."""
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        result = self._face_mesh.process(rgb)

        if not result.multi_face_landmarks:
            return None

        lm = result.multi_face_landmarks[0].landmark

        # Iris centres in pixel space
        left_iris = np.array([lm[_LEFT_IRIS_CENTER].x * w,
                               lm[_LEFT_IRIS_CENTER].y * h])
        right_iris = np.array([lm[_RIGHT_IRIS_CENTER].x * w,
                                lm[_RIGHT_IRIS_CENTER].y * h])
        ipd_px = float(np.linalg.norm(right_iris - left_iris))

        z_cm = estimate_z_cm(ipd_px, w, self._real_ipd_cm, self._camera_fov_deg)
        x_cm, y_cm = estimate_xy_cm(
            lm[_NOSE_TIP].x, lm[_NOSE_TIP].y,
            self._screen_width_cm, self._screen_height_cm,
        )
        return HeadPosition(x_cm=x_cm, y_cm=y_cm, z_cm=z_cm)

    def close(self) -> None:
        self._face_mesh.close()

    def __enter__(self) -> "FaceTracker":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
