# tracker/face_tracker.py
import math
import os
from dataclasses import dataclass

import cv2
import mediapipe as mp
import numpy as np
from mediapipe import tasks

# MediaPipe landmark indices (478 total with face landmarker v2)
_NOSE_TIP = 1
_LEFT_IRIS_CENTER = 468   # iris landmarks
_RIGHT_IRIS_CENTER = 473

_DEFAULT_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "models", "face_landmarker.task"
)


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
    z_cm: float,
    camera_fov_deg: float,
    image_width: int,
    image_height: int,
) -> tuple[float, float]:
    """Convert normalised nose position to physical cm offset from the camera optical axis.

    Uses the actual depth (z_cm) and horizontal camera FOV to triangulate the
    user's real lateral position instead of scaling by screen dimensions.
    This ensures the output is in true centimetres regardless of screen size,
    so headX/sw in the parallax shader gives the physically correct UV shift.

    Sign convention (for a non-mirroring webcam):
      x_cm positive = user's head to the right of the camera centre
      y_cm positive = user's head above    the camera centre
    """
    h_half_fov = math.radians(camera_fov_deg / 2.0)
    phys_half_w = z_cm * math.tan(h_half_fov)

    # Vertical half-FOV derived from sensor aspect ratio (square pixels assumed).
    aspect = image_width / max(image_height, 1)
    phys_half_h = phys_half_w / aspect

    x_cm = -((nose_x_norm - 0.5) * 2.0 * phys_half_w)   # negate: webcam x is mirrored
    y_cm = -((nose_y_norm - 0.5) * 2.0 * phys_half_h)   # negate: y=0 is top, up=+
    return x_cm, y_cm


class FaceTracker:
    """Wraps MediaPipe FaceLandmarker and converts landmarks to head pose in cm."""

    def __init__(
        self,
        real_ipd_cm: float,
        screen_width_cm: float,
        screen_height_cm: float,
        camera_fov_deg: float = 60.0,
        model_path: str = _DEFAULT_MODEL_PATH,
    ):
        if not (0.0 < camera_fov_deg < 180.0):
            raise ValueError(f"camera_fov_deg must be in (0, 180), got {camera_fov_deg}")
        self._real_ipd_cm = real_ipd_cm
        self._screen_width_cm = screen_width_cm
        self._screen_height_cm = screen_height_cm
        self._camera_fov_deg = camera_fov_deg
        # Try GPU first; fall back to CPU if the delegate fails to initialise.
        for delegate in (
            tasks.BaseOptions.Delegate.GPU,
            tasks.BaseOptions.Delegate.CPU,
        ):
            try:
                options = tasks.vision.FaceLandmarkerOptions(
                    base_options=tasks.BaseOptions(
                        model_asset_path=model_path,
                        delegate=delegate,
                    ),
                    running_mode=tasks.vision.RunningMode.IMAGE,
                    num_faces=1,
                    min_face_detection_confidence=0.5,
                    min_face_presence_confidence=0.5,
                )
                self._landmarker = tasks.vision.FaceLandmarker.create_from_options(options)
                break
            except Exception:
                if delegate == tasks.BaseOptions.Delegate.CPU:
                    raise

    def process_frame(self, frame_bgr: np.ndarray) -> HeadPosition | None:
        """Process one BGR camera frame. Returns None if no face detected."""
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect(mp_image)

        if not result.face_landmarks:
            return None

        lm = result.face_landmarks[0]  # list of NormalizedLandmark

        # Iris centres in pixel space
        left_iris = np.array([lm[_LEFT_IRIS_CENTER].x * w,
                               lm[_LEFT_IRIS_CENTER].y * h])
        right_iris = np.array([lm[_RIGHT_IRIS_CENTER].x * w,
                                lm[_RIGHT_IRIS_CENTER].y * h])
        ipd_px = float(np.linalg.norm(right_iris - left_iris))

        z_cm = estimate_z_cm(ipd_px, w, self._real_ipd_cm, self._camera_fov_deg)
        x_cm, y_cm = estimate_xy_cm(
            lm[_NOSE_TIP].x, lm[_NOSE_TIP].y,
            z_cm, self._camera_fov_deg, w, h,
        )
        return HeadPosition(x_cm=x_cm, y_cm=y_cm, z_cm=z_cm)

    def close(self) -> None:
        self._landmarker.close()

    def __enter__(self) -> "FaceTracker":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
