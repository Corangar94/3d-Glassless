# tracker/face_tracker_cv2.py
"""Pure-OpenCV fallback tracker with periodic detection and sparse flow."""
from __future__ import annotations

import math
import os
from typing import Optional

import cv2
import numpy as np

from tracker.camera_geometry import CameraGeometry
from tracker.cv2_temporal_tracker import (
    Cv2FallbackTrackingError,
    EyePair,
    FaceBox,
    FaceObservation,
    SparseFaceMotionTracker,
    blend_boxes,
    box_iou,
)
from tracker.pose import HeadPosition, monotonic_ms, normalize_wire_timestamp
from tracker.scheduled_cascade_detector import (
    CascadeDetectorCallAdapter,
    ScheduledCascadeFaceDetector,
)


class FaceTracker:
    """Fallback face tracker that avoids full-frame cascades on every frame.

    Cascades periodically correct the face/eye ROI. Shi-Tomasi features and
    pyramidal Lucas-Kanade optical flow carry that ROI between corrections. The
    cascade remains authoritative: flow failures immediately fall back to a
    detector pass, and repeated cascade misses retire the tracked ROI.
    """

    def __init__(
        self,
        real_ipd_cm: float,
        screen_width_cm: float,
        screen_height_cm: float,
        camera_fov_deg: float = 60.0,
        model_path: str = "",
        camera_geometry: CameraGeometry | None = None,
        *,
        detection_width_px: int = 640,
        detection_interval_frames: int = 5,
        full_scan_interval_frames: int = 30,
        eye_track_hold_frames: int = 18,
        maximum_cascade_misses: int = 2,
        minimum_flow_quality: float = 0.25,
        cascade_correction_alpha: float = 0.70,
        face_classifier: object | None = None,
        eye_classifier: object | None = None,
        detector: object | None = None,
        motion_tracker: SparseFaceMotionTracker | None = None,
        **_options: object,
    ) -> None:
        if not (0.0 < camera_fov_deg < 180.0):
            raise ValueError(
                f"camera_fov_deg must be in (0, 180), got {camera_fov_deg}"
            )
        if detection_width_px < 160:
            raise ValueError("detection_width_px must be at least 160")
        if detection_interval_frames < 1:
            raise ValueError("detection_interval_frames must be positive")
        if full_scan_interval_frames < detection_interval_frames:
            raise ValueError(
                "full_scan_interval_frames cannot be smaller than "
                "detection_interval_frames"
            )
        if eye_track_hold_frames < 0:
            raise ValueError("eye_track_hold_frames cannot be negative")
        if maximum_cascade_misses < 0:
            raise ValueError("maximum_cascade_misses cannot be negative")
        if not (0.0 <= minimum_flow_quality <= 1.0):
            raise ValueError("minimum_flow_quality must be in [0, 1]")
        if not (0.0 <= cascade_correction_alpha <= 1.0):
            raise ValueError("cascade_correction_alpha must be in [0, 1]")

        self._real_ipd_cm = float(real_ipd_cm)
        self._camera_fov_deg = float(camera_fov_deg)
        self._camera_geometry = camera_geometry
        self._detection_width_px = int(detection_width_px)
        self._detection_interval_frames = int(detection_interval_frames)
        self._full_scan_interval_frames = int(full_scan_interval_frames)
        self._eye_track_hold_frames = int(eye_track_hold_frames)
        self._maximum_cascade_misses = int(maximum_cascade_misses)
        self._minimum_flow_quality = float(minimum_flow_quality)
        self._cascade_correction_alpha = float(cascade_correction_alpha)
        self._frame_index = 0
        self._cascade_misses = 0
        self._last_motion_error = ""
        self._eye_ratio: float | None = None
        self._eye_center_ratio: tuple[float, float] | None = None
        self._eye_roll_deg = 0.0
        self._eye_age_frames = 0

        if detector is None:
            if face_classifier is None or eye_classifier is None:
                cv2_data = getattr(cv2, "data", None)
                cascade_root = str(getattr(cv2_data, "haarcascades", ""))
                if not cascade_root:
                    raise Cv2FallbackTrackingError(
                        "OpenCV Haar cascade directory is unavailable"
                    )
                if face_classifier is None:
                    face_classifier = cv2.CascadeClassifier(
                        os.path.join(
                            cascade_root,
                            "haarcascade_frontalface_default.xml",
                        )
                    )
                if eye_classifier is None:
                    eyeglasses_path = os.path.join(
                        cascade_root,
                        "haarcascade_eye_tree_eyeglasses.xml",
                    )
                    eye_path = (
                        eyeglasses_path
                        if os.path.exists(eyeglasses_path)
                        else os.path.join(cascade_root, "haarcascade_eye.xml")
                    )
                    eye_classifier = cv2.CascadeClassifier(eye_path)
            detector = ScheduledCascadeFaceDetector(
                face_classifier,
                eye_classifier,
            )
        self._detector = CascadeDetectorCallAdapter(detector)
        self._motion = motion_tracker or SparseFaceMotionTracker()

    @property
    def cascade_misses(self) -> int:
        return self._cascade_misses

    @property
    def last_motion_error(self) -> str:
        return self._last_motion_error

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

    def reset_session(self) -> None:
        self._motion.reset()
        self._frame_index = 0
        self._cascade_misses = 0
        self._last_motion_error = ""
        self._eye_ratio = None
        self._eye_center_ratio = None
        self._eye_roll_deg = 0.0
        self._eye_age_frames = 0

    def _tracking_gray(
        self,
        frame_bgr: np.ndarray,
    ) -> tuple[np.ndarray, float]:
        height, width = frame_bgr.shape[:2]
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        longest_edge = max(width, height)
        scale = min(
            1.0,
            self._detection_width_px / max(1, longest_edge),
        )
        if scale < 1.0:
            gray = cv2.resize(
                gray,
                (
                    max(1, int(round(width * scale))),
                    max(1, int(round(height * scale))),
                ),
                interpolation=cv2.INTER_AREA,
            )
        # Optical flow receives a stable raw luminance image. Histogram
        # equalization is deferred until a cascade pass is actually due, saving
        # that full-frame operation on the common flow-only frames and avoiding
        # frame-to-frame photometric changes in Lucas-Kanade tracking.
        return np.ascontiguousarray(gray), scale

    @staticmethod
    def _boxes_are_compatible(
        predicted: FaceBox,
        detected: FaceBox,
    ) -> bool:
        if box_iou(predicted, detected) >= 0.08:
            return True
        distance = math.dist(predicted.center, detected.center)
        return distance <= max(predicted.width, predicted.height) * 0.75

    @staticmethod
    def _propagated_eyes(
        predicted: FaceObservation,
        corrected_box: FaceBox,
    ) -> EyePair | None:
        eyes = predicted.eyes
        if eyes is None:
            return None
        scale_x = corrected_box.width / max(predicted.box.width, 1e-6)
        scale_y = corrected_box.height / max(predicted.box.height, 1e-6)
        scale = min(1.25, max(0.75, math.sqrt(scale_x * scale_y)))
        return eyes.transformed(
            predicted.box.center,
            corrected_box.center,
            scale,
        )

    def _corrected_detection(
        self,
        predicted: FaceObservation | None,
        detected: FaceObservation,
        gray: np.ndarray,
    ) -> FaceObservation:
        compatible = predicted is not None and self._boxes_are_compatible(
            predicted.box,
            detected.box,
        )
        if not compatible:
            corrected_box = detected.box
        else:
            assert predicted is not None
            corrected_box = blend_boxes(
                predicted.box,
                detected.box,
                self._cascade_correction_alpha,
            )
            clipped = corrected_box.clipped(gray.shape[1], gray.shape[0])
            corrected_box = detected.box if clipped is None else clipped

        if detected.eyes is not None:
            eyes = detected.eyes
            source = "cascade"
        elif compatible and predicted is not None:
            eyes = self._propagated_eyes(predicted, corrected_box)
            source = "cascade_flow_eyes" if eyes is not None else "cascade"
        else:
            eyes = None
            source = "cascade"
        return FaceObservation(
            corrected_box,
            eyes,
            source,
            detected.quality,
        )

    def _observe(self, gray: np.ndarray) -> FaceObservation | None:
        try:
            predicted = self._motion.track(gray)
        except Cv2FallbackTrackingError as error:
            self._last_motion_error = f"{type(error).__name__}: {error}"
            self._motion.reset()
            predicted = None

        detect_due = (
            predicted is None
            or predicted.quality < self._minimum_flow_quality
            or self._frame_index % self._detection_interval_frames == 0
        )
        if not detect_due:
            return predicted

        prior = predicted.box if predicted is not None else self._motion.current_box
        force_full_scan = (
            predicted is None
            or predicted.quality < self._minimum_flow_quality * 0.5
            or self._frame_index % self._full_scan_interval_frames == 0
        )
        cascade_gray = np.ascontiguousarray(cv2.equalizeHist(gray))
        detected = self._detector.detect(
            cascade_gray,
            prior=prior,
            # A scheduled ROI miss should reacquire on the same frame rather
            # than waiting for the next full-scan interval.
            allow_full_scan=True,
            force_full_scan=force_full_scan,
        )
        if detected is None:
            self._cascade_misses += 1
            if (
                predicted is not None
                and self._cascade_misses <= self._maximum_cascade_misses
            ):
                return predicted
            self._motion.reset()
            return None

        self._cascade_misses = 0
        corrected = self._corrected_detection(predicted, detected, gray)
        try:
            # Seed optical flow from the raw grayscale image, not the
            # cascade-only equalized copy.
            self._motion.initialize(
                gray,
                corrected.box,
                corrected.eyes,
            )
        except Cv2FallbackTrackingError as error:
            self._last_motion_error = f"{type(error).__name__}: {error}"
            self._motion.reset()
        else:
            self._last_motion_error = ""
        return corrected

    def _clear_eye_memory(self) -> None:
        self._eye_ratio = None
        self._eye_center_ratio = None
        self._eye_roll_deg = 0.0

    def _remember_fresh_eyes(
        self,
        observation: FaceObservation,
        eyes: EyePair,
    ) -> None:
        box = observation.box
        midpoint = eyes.midpoint
        self._eye_ratio = eyes.separation_px / max(box.width, 1.0)
        self._eye_center_ratio = (
            (midpoint[0] - box.x) / max(box.width, 1.0),
            (midpoint[1] - box.y) / max(box.height, 1.0),
        )
        self._eye_roll_deg = eyes.roll_deg
        self._eye_age_frames = 0

    def _synthetic_eyes(self, box: FaceBox) -> EyePair | None:
        if self._eye_ratio is None or self._eye_center_ratio is None:
            return None
        center_x = box.x + self._eye_center_ratio[0] * box.width
        center_y = box.y + self._eye_center_ratio[1] * box.height
        separation = self._eye_ratio * box.width
        angle = math.radians(self._eye_roll_deg)
        half_x = math.cos(angle) * separation * 0.5
        half_y = math.sin(angle) * separation * 0.5
        return EyePair(
            (center_x - half_x, center_y - half_y),
            (center_x + half_x, center_y + half_y),
        )

    def _usable_eyes(self, observation: FaceObservation) -> EyePair | None:
        eyes = observation.eyes
        if eyes is not None and observation.source == "cascade":
            ratio = eyes.separation_px / max(observation.box.width, 1.0)
            if 0.18 <= ratio <= 0.78:
                self._remember_fresh_eyes(observation, eyes)
                return eyes

        self._eye_age_frames += 1
        if self._eye_age_frames > self._eye_track_hold_frames:
            self._clear_eye_memory()
            return None
        if eyes is not None:
            return eyes
        return self._synthetic_eyes(observation.box)

    @staticmethod
    def _original_box(box: FaceBox, scale: float) -> FaceBox:
        inverse = 1.0 / max(scale, 1e-9)
        return FaceBox(
            box.x * inverse,
            box.y * inverse,
            box.width * inverse,
            box.height * inverse,
        )

    @staticmethod
    def _original_eyes(eyes: EyePair, scale: float) -> EyePair:
        inverse = 1.0 / max(scale, 1e-9)
        return EyePair(
            (eyes.left[0] * inverse, eyes.left[1] * inverse),
            (eyes.right[0] * inverse, eyes.right[1] * inverse),
        )

    def _pose_from_observation(
        self,
        observation: FaceObservation,
        image_width: int,
        image_height: int,
        scale: float,
        timestamp_ms: int,
    ) -> HeadPosition:
        box = self._original_box(observation.box, scale)
        tracked_eyes = self._usable_eyes(observation)
        eyes = (
            self._original_eyes(tracked_eyes, scale)
            if tracked_eyes is not None
            else None
        )
        geometry = self._camera_geometry
        focal_px = (
            geometry.focal_lengths(image_width, image_height)[0]
            if geometry is not None and geometry.intrinsics is not None
            else image_width
            / (2.0 * math.tan(math.radians(self._camera_fov_deg / 2.0)))
        )

        roll_deg = 0.0
        ipd_px: float | None = None
        if eyes is not None and eyes.separation_px > 10.0:
            ipd_px = eyes.separation_px
            center_x_px, center_y_px = eyes.midpoint
            roll_deg = max(-45.0, min(45.0, eyes.roll_deg))
            confidence = (
                0.70
                if observation.source == "cascade"
                else 0.55 + 0.12 * observation.quality
            )
        else:
            center_x_px, center_y_px = box.center
            confidence = 0.43 if observation.source == "cascade" else 0.38

        if ipd_px is not None and ipd_px > 10.0:
            z_camera_cm = focal_px * self._real_ipd_cm / ipd_px
        else:
            z_camera_cm = (
                focal_px * self._real_ipd_cm * 2.5 / max(box.width, 1.0)
            )

        if geometry is not None and geometry.intrinsics is not None:
            x_cm, y_cm, z_cm = geometry.pixel_depth_to_screen(
                center_x_px,
                center_y_px,
                z_camera_cm,
                image_width=image_width,
                image_height=image_height,
            )
        else:
            aspect = image_width / max(image_height, 1)
            cx_norm = center_x_px / max(image_width, 1)
            cy_norm = center_y_px / max(image_height, 1)
            phys_half_w = z_camera_cm * math.tan(
                math.radians(self._camera_fov_deg / 2.0)
            )
            phys_half_h = phys_half_w / aspect
            x_cm = -((cx_norm - 0.5) * 2.0 * phys_half_w)
            y_cm = -((cy_norm - 0.5) * 2.0 * phys_half_h)
            z_cm = z_camera_cm

        yaw_deg = pitch_deg = 0.0
        if geometry is not None:
            yaw_deg, pitch_deg, roll_deg = geometry.orientation_to_screen(
                yaw_deg,
                pitch_deg,
                roll_deg,
            )
        return HeadPosition(
            x_cm=x_cm,
            y_cm=y_cm,
            z_cm=z_cm,
            yaw_deg=yaw_deg,
            pitch_deg=pitch_deg,
            roll_deg=roll_deg,
            confidence=min(1.0, max(0.05, confidence)),
            capture_timestamp_ms=normalize_wire_timestamp(timestamp_ms),
        )

    def process_frame(
        self,
        frame_bgr: np.ndarray,
        capture_timestamp_ms: int | None = None,
    ) -> Optional[HeadPosition]:
        height, width = frame_bgr.shape[:2]
        timestamp_ms = (
            monotonic_ms()
            if capture_timestamp_ms is None
            else normalize_wire_timestamp(capture_timestamp_ms)
        )
        gray, scale = self._tracking_gray(frame_bgr)
        observation = self._observe(gray)
        self._frame_index += 1
        if observation is None:
            self._eye_age_frames += 1
            if self._eye_age_frames > self._eye_track_hold_frames:
                self._clear_eye_memory()
            return None
        return self._pose_from_observation(
            observation,
            width,
            height,
            scale,
            timestamp_ms,
        )

    def close(self) -> None:
        self.reset_session()

    def __enter__(self) -> "FaceTracker":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
