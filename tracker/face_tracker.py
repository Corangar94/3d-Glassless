# tracker/face_tracker.py
"""Low-latency MediaPipe face tracker with camera-time pose metadata."""
from __future__ import annotations

import math
import os
import threading
from typing import Any

import cv2
import mediapipe as mp
import numpy as np
from mediapipe import tasks

from tracker.async_inference_watchdog import AsyncInferenceWatchdog
from tracker.async_result_freshness import (
    AsyncResultFreshnessGate,
    AsyncResultFreshnessPolicy,
)
from tracker.camera_geometry import CameraGeometry, euler_degrees_from_rotation_matrix
from tracker.pose import (
    HeadPosition,
    elapsed_u32_ms,
    monotonic_ms,
    normalize_wire_timestamp,
)
from tracker.timestamp_expansion import expand_u32_timestamp

_LEFT_IRIS_CENTER = 468
_RIGHT_IRIS_CENTER = 473
_UINT32_HALF_RANGE = 0x8000_0000
_DEFAULT_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "models", "face_landmarker.task"
)


def estimate_z_cm(
    ipd_px: float,
    image_width: int,
    real_ipd_cm: float,
    camera_fov_deg: float,
    yaw_deg: float = 0.0,
) -> float:
    """Estimate distance from iris separation, correcting yaw foreshortening."""
    focal_px = image_width / (2.0 * math.tan(math.radians(camera_fov_deg / 2.0)))
    yaw_scale = max(0.45, abs(math.cos(math.radians(yaw_deg))))
    return (focal_px * real_ipd_cm * yaw_scale) / max(ipd_px, 1.0)


def estimate_xy_cm(
    nose_x_norm: float,
    nose_y_norm: float,
    z_cm: float,
    camera_fov_deg: float,
    image_width: int,
    image_height: int,
) -> tuple[float, float]:
    h_half_fov = math.radians(camera_fov_deg / 2.0)
    phys_half_w = z_cm * math.tan(h_half_fov)
    aspect = image_width / max(image_height, 1)
    phys_half_h = phys_half_w / aspect
    return (
        -((nose_x_norm - 0.5) * 2.0 * phys_half_w),
        -((nose_y_norm - 0.5) * 2.0 * phys_half_h),
    )


def _normalized_rotation(matrix: np.ndarray) -> np.ndarray | None:
    if matrix.size < 16:
        return None
    candidate = np.asarray(matrix, dtype=np.float64).reshape(4, 4)[:3, :3]
    norms = np.linalg.norm(candidate, axis=0)
    if np.any(~np.isfinite(norms)) or np.any(norms < 1e-6):
        return None
    rotation = candidate / norms
    u, _singular, vt = np.linalg.svd(rotation)
    rotation = u @ vt
    if np.linalg.det(rotation) < 0.0:
        u[:, -1] *= -1.0
        rotation = u @ vt
    return rotation


def matrix_to_euler_degrees(matrix: object) -> tuple[float, float, float]:
    """Return yaw, pitch and roll using the calibrated camera convention.

    MediaPipe's facial transform is first projected onto the nearest proper
    rotation matrix, then decomposed with the same Rz(roll) @ Rx(pitch) @
    Ry(yaw) convention used by ``CameraGeometry``. Keeping one convention is
    important for compound head rotations because yaw also drives iris
    foreshortening correction in the distance estimate.
    """
    try:
        rotation = _normalized_rotation(np.asarray(matrix))
    except (TypeError, ValueError, np.linalg.LinAlgError):
        rotation = None
    if rotation is None:
        return 0.0, 0.0, 0.0
    return euler_degrees_from_rotation_matrix(rotation)


def _landmark_confidence(landmarks: list[Any]) -> float:
    selected: list[float] = []
    for index in (_LEFT_IRIS_CENTER, _RIGHT_IRIS_CENTER):
        landmark = landmarks[index]
        for value in (
            getattr(landmark, "presence", None),
            getattr(landmark, "visibility", None),
        ):
            if value is not None:
                parsed = float(value)
                if math.isfinite(parsed):
                    selected.append(parsed)
    return min(1.0, max(0.05, min(selected) if selected else 1.0))


class FaceTracker:
    """Asynchronous latest-result MediaPipe tracker.

    LIVE_STREAM mode returns immediately and drops stale work rather than
    building camera-to-display latency. Each completed pose is delivered once.
    """

    def __init__(
        self,
        real_ipd_cm: float,
        screen_width_cm: float,
        screen_height_cm: float,
        camera_fov_deg: float = 60.0,
        model_path: str = _DEFAULT_MODEL_PATH,
        *,
        async_mode: bool = True,
        min_tracking_confidence: float = 0.5,
        camera_geometry: CameraGeometry | None = None,
        async_stall_timeout_ms: int = 5000,
        async_max_consecutive_errors: int = 3,
        async_max_backlog_ms: int = 150,
        async_max_result_age_ms: int = 250,
        async_max_consecutive_stale_results: int = 3,
        async_stale_result_window_ms: int = 1000,
    ) -> None:
        if not (0.0 < camera_fov_deg < 180.0):
            raise ValueError(f"camera_fov_deg must be in (0, 180), got {camera_fov_deg}")
        backlog_ms = int(async_max_backlog_ms)
        if backlog_ms < 0:
            raise ValueError("async_max_backlog_ms cannot be negative")
        freshness_policy = AsyncResultFreshnessPolicy(
            max_result_age_ms=int(async_max_result_age_ms),
            max_consecutive_stale_results=int(
                async_max_consecutive_stale_results
            ),
            stale_result_window_ms=int(async_stale_result_window_ms),
        )
        self._real_ipd_cm = float(real_ipd_cm)
        self._screen_width_cm = float(screen_width_cm)
        self._screen_height_cm = float(screen_height_cm)
        self._camera_fov_deg = float(camera_fov_deg)
        self._camera_geometry = camera_geometry
        self._async_mode = bool(async_mode)
        self._async_max_backlog_ms = backlog_ms
        self._async_max_result_age_ms = freshness_policy.max_result_age_ms
        self._lock = threading.Lock()
        self._latest_pose: HeadPosition | None = None
        self._last_delivered_timestamp_ms: int | None = None
        # Retained as a compatibility fallback for bare ``__new__`` test doubles
        # and downstream subclasses that predate AsyncResultFreshnessGate.
        self._stale_result_count = 0
        self._last_stale_result_age_ms: int | None = None
        self._last_submitted_wire_timestamp_ms: int | None = None
        self._last_submitted_media_timestamp_ms: int | None = None
        self._minimum_result_media_timestamp_ms: int | None = None
        self._async_watchdog = (
            AsyncInferenceWatchdog(
                max_consecutive_errors=async_max_consecutive_errors,
                stall_timeout_ms=async_stall_timeout_ms,
            )
            if self._async_mode
            else None
        )
        self._async_result_freshness = (
            AsyncResultFreshnessGate(freshness_policy)
            if self._async_mode
            else None
        )
        self._closed = False

        options = tasks.vision.FaceLandmarkerOptions(
            base_options=tasks.BaseOptions(
                model_asset_path=model_path,
                delegate=tasks.BaseOptions.Delegate.CPU,
            ),
            running_mode=(
                tasks.vision.RunningMode.LIVE_STREAM
                if self._async_mode
                else tasks.vision.RunningMode.IMAGE
            ),
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=min_tracking_confidence,
            output_facial_transformation_matrixes=True,
            result_callback=self._on_result if self._async_mode else None,
        )
        self._landmarker = tasks.vision.FaceLandmarker.create_from_options(options)

    def async_result_freshness_snapshot(self):
        gate = getattr(self, "_async_result_freshness", None)
        return None if gate is None else gate.snapshot()

    @property
    def stale_result_count(self) -> int:
        snapshot = self.async_result_freshness_snapshot()
        if snapshot is not None:
            return int(snapshot.total_stale_results)
        with self._lock:
            return int(getattr(self, "_stale_result_count", 0))

    @property
    def consecutive_stale_result_count(self) -> int:
        snapshot = self.async_result_freshness_snapshot()
        return 0 if snapshot is None else int(snapshot.consecutive_stale_results)

    @property
    def last_stale_result_age_ms(self) -> int | None:
        snapshot = self.async_result_freshness_snapshot()
        if snapshot is not None:
            return snapshot.last_stale_age_ms
        with self._lock:
            return getattr(self, "_last_stale_result_age_ms", None)

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
        """Forget poses that belong to a retired camera capture session.

        The MediaPipe landmarker must keep its monotonically increasing private
        timestamp timeline for its full lifetime, so submission timestamps are
        deliberately not reset. Instead, the latest submitted timestamp becomes
        a result floor. Any callback still in flight from the old webcam session
        is discarded when it eventually arrives.
        """
        with self._lock:
            self._latest_pose = None
            self._last_delivered_timestamp_ms = None
            self._stale_result_count = 0
            self._last_stale_result_age_ms = None
            submitted = self._last_submitted_media_timestamp_ms
            if submitted is not None:
                current = self._minimum_result_media_timestamp_ms
                self._minimum_result_media_timestamp_ms = (
                    submitted if current is None else max(current, submitted)
                )
        freshness = getattr(self, "_async_result_freshness", None)
        if freshness is not None:
            freshness.reset()
        if self._async_watchdog is not None:
            self._async_watchdog.reset_session()

    def async_health_snapshot(self):
        """Return callback progress used by the automatic backend controller."""
        watchdog = self._async_watchdog
        return None if watchdog is None else watchdog.snapshot()

    def ready_for_promotion(self) -> bool:
        """Return True once a shadow instance proves healthy result progress."""
        if not self._async_mode:
            return True
        snapshot = self.async_health_snapshot()
        return bool(
            snapshot is not None
            and snapshot.last_callback_ms is not None
            and snapshot.consecutive_submission_errors == 0
            and snapshot.consecutive_callback_errors == 0
        )

    def _result_is_current_locked(self, timestamp_ms: int) -> bool:
        if self._closed:
            return False
        floor = self._minimum_result_media_timestamp_ms
        return floor is None or int(timestamp_ms) > floor

    def _pose_from_result(
        self,
        result: object,
        image_width: int,
        image_height: int,
        timestamp_ms: int,
    ) -> HeadPosition | None:
        face_landmarks = getattr(result, "face_landmarks", None)
        if not face_landmarks:
            return None
        landmarks = face_landmarks[0]
        matrices = getattr(result, "facial_transformation_matrixes", None)
        yaw_deg = pitch_deg = roll_deg = 0.0
        if matrices:
            yaw_deg, pitch_deg, roll_deg = matrix_to_euler_degrees(matrices[0])

        left = landmarks[_LEFT_IRIS_CENTER]
        right = landmarks[_RIGHT_IRIS_CENTER]
        left_iris = np.array(
            [left.x * image_width, left.y * image_height], dtype=np.float64
        )
        right_iris = np.array(
            [right.x * image_width, right.y * image_height], dtype=np.float64
        )
        geometry = self._camera_geometry
        if geometry is not None and geometry.intrinsics is not None:
            rectified = geometry.rectified_pixels(
                (left_iris, right_iris),
                image_width=image_width,
                image_height=image_height,
            )
            ipd_px = float(np.linalg.norm(rectified[1] - rectified[0]))
            focal_x, _focal_y = geometry.focal_lengths(image_width, image_height)
            yaw_scale = max(0.45, abs(math.cos(math.radians(yaw_deg))))
            z_camera_cm = (
                focal_x * self._real_ipd_cm * yaw_scale / max(ipd_px, 1.0)
            )
            center_px = (left_iris + right_iris) * 0.5
            x_cm, y_cm, z_cm = geometry.pixel_depth_to_screen(
                float(center_px[0]),
                float(center_px[1]),
                z_camera_cm,
                image_width=image_width,
                image_height=image_height,
            )
            yaw_deg, pitch_deg, roll_deg = geometry.orientation_to_screen(
                yaw_deg, pitch_deg, roll_deg
            )
        else:
            ipd_px = float(np.linalg.norm(right_iris - left_iris))
            if not math.isfinite(ipd_px) or ipd_px < 1.0:
                return None
            z_cm = estimate_z_cm(
                ipd_px,
                image_width,
                self._real_ipd_cm,
                self._camera_fov_deg,
                yaw_deg,
            )
            center_x = float((left.x + right.x) * 0.5)
            center_y = float((left.y + right.y) * 0.5)
            x_cm, y_cm = estimate_xy_cm(
                center_x,
                center_y,
                z_cm,
                self._camera_fov_deg,
                image_width,
                image_height,
            )
        return HeadPosition(
            x_cm=x_cm,
            y_cm=y_cm,
            z_cm=z_cm,
            yaw_deg=yaw_deg,
            pitch_deg=pitch_deg,
            roll_deg=roll_deg,
            confidence=_landmark_confidence(landmarks),
            capture_timestamp_ms=normalize_wire_timestamp(timestamp_ms),
        )

    def _on_result(self, result: object, image: object, timestamp_ms: int) -> None:
        timestamp = int(timestamp_ms)
        with self._lock:
            if not self._result_is_current_locked(timestamp):
                return

        try:
            width = int(getattr(image, "width", 0))
            height = int(getattr(image, "height", 0))
            pose = (
                self._pose_from_result(result, width, height, timestamp)
                if width > 0 and height > 0
                else None
            )
        except Exception as error:
            with self._lock:
                current = self._result_is_current_locked(timestamp)
            if current and self._async_watchdog is not None:
                self._async_watchdog.record_callback(
                    timestamp,
                    error=error,
                )
            return

        with self._lock:
            if not self._result_is_current_locked(timestamp):
                return
            self._latest_pose = pose
        freshness = getattr(self, "_async_result_freshness", None)
        if pose is None and freshness is not None:
            freshness.record_result_without_pose()
        if self._async_watchdog is not None:
            self._async_watchdog.record_callback(timestamp)

    def _poll_latest(
        self,
        current_timestamp_ms: int | None = None,
    ) -> HeadPosition | None:
        with self._lock:
            pose = self._latest_pose
            if (
                pose is None
                or pose.capture_timestamp_ms == self._last_delivered_timestamp_ms
            ):
                return None
            # Retire the callback before applying the age gate. A stale result
            # must not be reconsidered on every subsequent camera frame.
            self._last_delivered_timestamp_ms = pose.capture_timestamp_ms

        freshness = getattr(self, "_async_result_freshness", None)
        if freshness is not None:
            if current_timestamp_ms is None:
                freshness.record_fresh_result()
                return pose
            return (
                pose
                if freshness.accept_result(
                    pose.capture_timestamp_ms,
                    current_timestamp_ms,
                )
                else None
            )

        # Compatibility fallback for bare downstream subclasses/test doubles
        # constructed without the freshness gate.
        maximum_age_ms = int(getattr(self, "_async_max_result_age_ms", 0))
        if (
            current_timestamp_ms is not None
            and maximum_age_ms > 0
            and pose.capture_timestamp_ms != 0
        ):
            age_ms = elapsed_u32_ms(
                normalize_wire_timestamp(current_timestamp_ms),
                pose.capture_timestamp_ms,
            )
            if age_ms < _UINT32_HALF_RANGE and age_ms > maximum_age_ms:
                with self._lock:
                    self._stale_result_count = int(
                        getattr(self, "_stale_result_count", 0)
                    ) + 1
                    self._last_stale_result_age_ms = age_ms
                return None
        return pose

    @staticmethod
    def _mediapipe_image(frame_bgr: np.ndarray) -> mp.Image:
        rgb = np.ascontiguousarray(
            cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        )
        return mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

    def process_frame(
        self,
        frame_bgr: np.ndarray,
        capture_timestamp_ms: int | None = None,
    ) -> HeadPosition | None:
        h, w = frame_bgr.shape[:2]
        wire_timestamp_ms = (
            monotonic_ms()
            if capture_timestamp_ms is None
            else normalize_wire_timestamp(capture_timestamp_ms)
        )

        if self._async_mode:
            watchdog = self._async_watchdog
            with self._lock:
                media_timestamp_ms = expand_u32_timestamp(
                    wire_timestamp_ms,
                    self._last_submitted_wire_timestamp_ms,
                    self._last_submitted_media_timestamp_ms,
                )
            if media_timestamp_ms is None:
                return self._poll_latest(wire_timestamp_ms)
            if watchdog is not None:
                watchdog.raise_if_unhealthy(media_timestamp_ms)
                if not watchdog.should_submit(
                    media_timestamp_ms,
                    max_backlog_ms=self._async_max_backlog_ms,
                ):
                    watchdog.record_throttled_submission()
                    return self._poll_latest(wire_timestamp_ms)

            # Conversion/allocation is deliberately after timestamp and backlog
            # admission so duplicate, out-of-order, and overloaded inputs stay
            # cheap. MediaPipe LIVE_STREAM may drop busy inputs itself; avoiding
            # the work before that boundary reduces camera-thread CPU and GC.
            image = self._mediapipe_image(frame_bgr)
            try:
                self._landmarker.detect_async(image, media_timestamp_ms)
            except (RuntimeError, ValueError) as error:
                if watchdog is not None:
                    watchdog.record_submission_error(error)
                    watchdog.raise_if_unhealthy(media_timestamp_ms)
            else:
                with self._lock:
                    self._last_submitted_wire_timestamp_ms = wire_timestamp_ms
                    self._last_submitted_media_timestamp_ms = media_timestamp_ms
                if watchdog is not None:
                    watchdog.record_submission(media_timestamp_ms)
                    watchdog.raise_if_unhealthy(media_timestamp_ms)
            return self._poll_latest(wire_timestamp_ms)

        image = self._mediapipe_image(frame_bgr)
        result = self._landmarker.detect(image)
        return self._pose_from_result(result, w, h, wire_timestamp_ms)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._landmarker.close()

    def __enter__(self) -> "FaceTracker":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
