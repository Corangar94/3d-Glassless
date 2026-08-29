# tracker/main.py
import argparse
import importlib
import math
import threading
import time
from typing import Any, Optional, Protocol

import cv2
import yaml

from tracker.camera_geometry import CameraGeometry
from tracker.camera_quality import CameraQualityMonitor, try_lock_camera_controls
from tracker.face_tracker_cv2 import HeadPosition
from tracker.freetrack import FreetracWriter
from tracker.pose import FilteredPose, elapsed_u32_ms, monotonic_ms
from tracker.pose_filter import AdaptivePoseFilter
from tracker.pose_shared_memory import PoseStateWriter
from tracker.shared_memory import SharedMemoryWriter, TrackingStateWriter
from tracker.shared_settings import OverlaySettings, SharedSettingsReader
from tracker.smoother import HeadSmoother
from tracker.tilt import _apply_camera_tilt, _calibrate_tilt

_CAMERA_READ_FAILURES_BEFORE_REOPEN = 3
_CAMERA_MAX_REOPEN_ATTEMPTS = 3
_DEFAULT_CAMERA_FOV_DEG = 90.0
_DEFAULT_SMOOTHING_R = 0.1


class FaceTrackerLike(Protocol):
    def process_frame(
        self,
        frame_bgr: object,
        capture_timestamp_ms: int | None = None,
    ) -> HeadPosition | None:
        ...

    def reset_session(self) -> None:
        ...


class PoseWriterLike(Protocol):
    def write(self, *, x: float, y: float, z: float) -> None:
        ...


def _finite_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _resolve_ipd_cm(cfg: dict[str, Any]) -> float:
    tracking_raw, overlay_raw = cfg.get("tracking", {}), cfg.get("overlay", {})
    tracking = tracking_raw if isinstance(tracking_raw, dict) else {}
    overlay = overlay_raw if isinstance(overlay_raw, dict) else {}
    calibration_raw = overlay.get("display_calibration", {})
    calibration = calibration_raw if isinstance(calibration_raw, dict) else {}
    for value in (calibration.get("ipd_mm"), overlay.get("ipd_mm")):
        parsed = _finite_float(value)
        if parsed is not None and parsed > 0.0:
            return parsed / 10.0
    parsed = _finite_float(tracking.get("ipd_cm", 6.4))
    return 6.4 if parsed is None else max(0.1, parsed)


def _resolve_camera_fov_deg(cfg: dict[str, Any]) -> float:
    tracking_raw, overlay_raw = cfg.get("tracking", {}), cfg.get("overlay", {})
    tracking = tracking_raw if isinstance(tracking_raw, dict) else {}
    overlay = overlay_raw if isinstance(overlay_raw, dict) else {}
    for value in (tracking.get("camera_fov_deg"), overlay.get("camera_fov_deg")):
        parsed = _finite_float(value)
        if parsed is not None and 0.0 < parsed < 180.0:
            return parsed
    return _DEFAULT_CAMERA_FOV_DEG


def _configure_camera(
    cap: cv2.VideoCapture,
    width: int = 0,
    height: int = 0,
    fps: float = 0.0,
) -> None:
    requested = (
        (getattr(cv2, "CAP_PROP_BUFFERSIZE", -1), 1),
        (cv2.CAP_PROP_FRAME_WIDTH, width),
        (cv2.CAP_PROP_FRAME_HEIGHT, height),
        (cv2.CAP_PROP_FPS, fps),
    )
    for property_id, value in requested:
        parsed = _finite_float(value)
        if property_id < 0 or parsed is None or parsed <= 0.0:
            continue
        try:
            cap.set(property_id, parsed)
        except (cv2.error, TypeError, ValueError):
            continue


def _capture_property(cap: cv2.VideoCapture, property_id: int) -> float:
    try:
        value = _finite_float(cap.get(property_id))
    except (cv2.error, TypeError, ValueError):
        return 0.0
    return 0.0 if value is None or value < 0.0 else value


def _camera_mode(cap: cv2.VideoCapture) -> tuple[int, int, float]:
    return (
        int(round(_capture_property(cap, cv2.CAP_PROP_FRAME_WIDTH))),
        int(round(_capture_property(cap, cv2.CAP_PROP_FRAME_HEIGHT))),
        _capture_property(cap, cv2.CAP_PROP_FPS),
    )


def _log_camera_opened(
    index: int,
    backend_name: str,
    cap: cv2.VideoCapture,
) -> None:
    width, height, fps = _camera_mode(cap)
    print(
        f"[G3D] Camera {index} opened via {backend_name} "
        f"at {width}x{height} @ {fps:.1f} fps"
    )


def _open_camera(
    index: int,
    width: int = 0,
    height: int = 0,
    fps: float = 0.0,
) -> cv2.VideoCapture:
    for backend_id, backend_name in (
        (cv2.CAP_DSHOW, "CAP_DSHOW"),
        (cv2.CAP_MSMF, "CAP_MSMF"),
    ):
        cap = cv2.VideoCapture(index, backend_id)
        if cap.isOpened():
            _configure_camera(cap, width, height, fps)
            _log_camera_opened(index, backend_name, cap)
            return cap
        cap.release()
    cap = cv2.VideoCapture(index)
    if cap.isOpened():
        _configure_camera(cap, width, height, fps)
        _log_camera_opened(index, "default backend", cap)
    return cap


def _load_face_tracker_class(backend: str):
    backend_id = str(backend or "auto").strip().lower()
    if backend_id not in {"auto", "mediapipe", "cv2"}:
        raise SystemExit(
            "ERROR: tracking backend must be one of: auto, mediapipe, cv2"
        )
    if backend_id in {"auto", "mediapipe"}:
        try:
            module = importlib.import_module("tracker.face_tracker")
            return module.FaceTracker, "mediapipe"
        except Exception:
            if backend_id == "mediapipe":
                raise
    module = importlib.import_module("tracker.face_tracker_cv2")
    return module.FaceTracker, "cv2"


def _validated_live_calibration(
    settings: OverlaySettings | None,
) -> tuple[float | None, float | None]:
    if settings is None:
        return None, None
    ipd_mm = _finite_float(settings.ipd_mm)
    ipd_cm = ipd_mm / 10.0 if ipd_mm is not None and ipd_mm > 0.0 else None
    fov = _finite_float(settings.camera_fov_deg)
    if fov is None or not (0.0 < fov < 180.0):
        fov = None
    return ipd_cm, fov


def _measurement_noise(settings: OverlaySettings | None) -> float:
    if settings is None:
        return _DEFAULT_SMOOTHING_R
    parsed = _finite_float(settings.smoothing_alpha)
    return (
        _DEFAULT_SMOOTHING_R
        if parsed is None or parsed <= 0.0
        else max(parsed, 1e-6)
    )


def _apply_live_calibration(
    tracker: FaceTrackerLike,
    settings: OverlaySettings | None,
    previous: tuple[float | None, float | None] | None,
) -> tuple[float | None, float | None] | None:
    calibration = _validated_live_calibration(settings)
    if calibration == previous:
        return previous
    real_ipd_cm, camera_fov_deg = calibration
    values: dict[str, float] = {}
    if real_ipd_cm is not None:
        values["real_ipd_cm"] = real_ipd_cm
    if camera_fov_deg is not None:
        values["camera_fov_deg"] = camera_fov_deg
    set_calibration = getattr(tracker, "set_calibration", None)
    if not values or not callable(set_calibration):
        return previous
    set_calibration(**values)
    return calibration


def _validated_pose(position: HeadPosition | None) -> HeadPosition | None:
    if position is None:
        return None
    x = _finite_float(position.x_cm)
    y = _finite_float(position.y_cm)
    z = _finite_float(position.z_cm)
    if x is None or y is None or z is None or z <= 0.0:
        return None
    yaw = _finite_float(getattr(position, "yaw_deg", 0.0)) or 0.0
    pitch = _finite_float(getattr(position, "pitch_deg", 0.0)) or 0.0
    roll = _finite_float(getattr(position, "roll_deg", 0.0)) or 0.0
    confidence = _finite_float(getattr(position, "confidence", 1.0))
    return HeadPosition(
        x_cm=x,
        y_cm=y,
        z_cm=z,
        yaw_deg=yaw,
        pitch_deg=pitch,
        roll_deg=roll,
        confidence=min(
            1.0,
            max(0.0, confidence if confidence is not None else 0.0),
        ),
        capture_timestamp_ms=(
            int(getattr(position, "capture_timestamp_ms", 0) or monotonic_ms())
            & 0xFFFF_FFFF
        ),
    )


def _limit_pose_step(
    raw: tuple[float, float, float],
    prev: tuple[float, float, float] | None,
    max_xy_step_cm: float = 10.0,
    max_z_step_cm: float = 12.0,
) -> tuple[float, float, float]:
    if prev is None:
        return raw
    dx = raw[0] - prev[0]
    dy = raw[1] - prev[1]
    length = math.hypot(dx, dy)
    if max_xy_step_cm > 0.0 and length > max_xy_step_cm:
        scale = max_xy_step_cm / length
        x = prev[0] + dx * scale
        y = prev[1] + dy * scale
    else:
        x, y = raw[0], raw[1]
    dz = raw[2] - prev[2]
    if max_z_step_cm > 0.0:
        dz = max(-max_z_step_cm, min(max_z_step_cm, dz))
    return x, y, prev[2] + dz


def _tilt_filtered_pose(pose: FilteredPose, tilt_deg: float) -> FilteredPose:
    if abs(tilt_deg) < 1e-6:
        return pose
    x, y, z = _apply_camera_tilt(
        pose.x_cm,
        pose.y_cm,
        pose.z_cm,
        tilt_deg,
    )
    vx, vy, vz = _apply_camera_tilt(
        pose.vx_cm_s,
        pose.vy_cm_s,
        pose.vz_cm_s,
        tilt_deg,
    )
    return FilteredPose(
        x_cm=x,
        y_cm=y,
        z_cm=z,
        vx_cm_s=vx,
        vy_cm_s=vy,
        vz_cm_s=vz,
        yaw_deg=pose.yaw_deg,
        pitch_deg=pose.pitch_deg - tilt_deg,
        roll_deg=pose.roll_deg,
        confidence=pose.confidence,
        capture_timestamp_ms=pose.capture_timestamp_ms,
        publish_timestamp_ms=pose.publish_timestamp_ms,
        prediction_target_timestamp_ms=pose.prediction_target_timestamp_ms,
        predicted=pose.predicted,
    )


class TrackingLoop:
    def __init__(
        self,
        tracker: FaceTrackerLike,
        writer: PoseWriterLike,
        smoother: HeadSmoother | AdaptivePoseFilter,
        hold_ms: int = 500,
        stop_event: Optional[threading.Event] = None,
        camera_tilt_deg: float = 0.0,
        config_path: Optional[str] = None,
        camera_quality_monitor: CameraQualityMonitor | None = None,
        lock_camera_controls: bool = False,
    ) -> None:
        self._tracker = tracker
        self._writer = writer
        self._smoother = smoother
        self._hold_ms = hold_ms
        self._last_face_ms: Optional[float] = None
        self._last_smoothed = (0.0, 0.0, 60.0)
        self._last_output_pose = FilteredPose(
            x_cm=0.0,
            y_cm=0.0,
            z_cm=60.0,
        )
        self._last_raw_pos: tuple[float, float, float] | None = None
        self._last_measurement_s: float | None = None
        self._stop_event = stop_event
        self._camera_tilt_deg = camera_tilt_deg
        self._config_path = config_path
        self._camera_quality_monitor = camera_quality_monitor
        self._lock_camera_controls = bool(lock_camera_controls)

    def _process_frame(
        self,
        frame: object,
        capture_timestamp_ms: int,
    ) -> HeadPosition | None:
        try:
            return self._tracker.process_frame(
                frame,
                capture_timestamp_ms=capture_timestamp_ms,
            )
        except TypeError:
            return self._tracker.process_frame(frame)

    def _supports_pose_filter(self) -> bool:
        return callable(getattr(type(self._smoother), "update_pose", None))

    def _update_filter(self, pose: HeadPosition) -> FilteredPose:
        if self._supports_pose_filter():
            return self._smoother.update_pose(pose)
        measurement_s = time.monotonic()
        dt_s = (
            measurement_s - self._last_measurement_s
            if self._last_measurement_s is not None
            else None
        )
        self._last_measurement_s = measurement_s
        xyz = (
            self._smoother.update(pose.x_cm, pose.y_cm, pose.z_cm)
            if dt_s is None
            else self._smoother.update(
                pose.x_cm,
                pose.y_cm,
                pose.z_cm,
                dt_seconds=dt_s,
            )
        )
        publish_timestamp_ms = monotonic_ms()
        return FilteredPose(
            x_cm=xyz[0],
            y_cm=xyz[1],
            z_cm=xyz[2],
            yaw_deg=pose.yaw_deg,
            pitch_deg=pose.pitch_deg,
            roll_deg=pose.roll_deg,
            confidence=pose.confidence,
            capture_timestamp_ms=pose.capture_timestamp_ms,
            publish_timestamp_ms=publish_timestamp_ms,
            prediction_target_timestamp_ms=publish_timestamp_ms,
        )

    def _predict_filter(self) -> FilteredPose:
        if callable(getattr(type(self._smoother), "predict", None)):
            return self._smoother.predict()
        return self._last_output_pose

    @staticmethod
    def _neutral_pose(timestamp_ms: int | None = None) -> FilteredPose:
        timestamp = monotonic_ms() if timestamp_ms is None else timestamp_ms
        return FilteredPose(
            x_cm=0.0,
            y_cm=0.0,
            z_cm=60.0,
            publish_timestamp_ms=timestamp,
            prediction_target_timestamp_ms=timestamp,
        )

    def _publish(self, pose: FilteredPose, status: str) -> None:
        write_state = getattr(self._writer, "write_state", None)
        if callable(write_state):
            write_state(status)
        if callable(getattr(type(self._writer), "write_pose", None)):
            self._writer.write_pose(pose, valid=status != "paused")
        else:
            self._writer.write(x=pose.x_cm, y=pose.y_cm, z=pose.z_cm)
        self._on_position(pose.x_cm, pose.y_cm, pose.z_cm, status)

    def _reset_capture_session(self) -> int:
        """Clear every stateful input derived from the retired webcam handle."""
        reset_tracker = getattr(self._tracker, "reset_session", None)
        if callable(reset_tracker):
            reset_tracker()
        reset_smoother = getattr(self._smoother, "reset", None)
        if callable(reset_smoother):
            reset_smoother()
        if self._camera_quality_monitor is not None:
            self._camera_quality_monitor.reset()

        self._last_face_ms = None
        self._last_smoothed = (0.0, 0.0, 60.0)
        self._last_raw_pos = None
        self._last_measurement_s = None
        timestamp_ms = monotonic_ms()
        neutral = self._neutral_pose(timestamp_ms)
        self._last_output_pose = neutral

        # Fail closed while the new camera warms up. The V2 packet is marked
        # invalid and G3D_State is paused before a fresh result can arrive.
        self._publish(neutral, "paused")
        print(
            "[G3D] Camera capture session reset — cleared pose, filter, "
            "quality, and control-lock history"
        )
        return timestamp_ms

    def run(
        self,
        camera_index: int = 0,
        camera_width: int = 0,
        camera_height: int = 0,
        camera_fps: float = 0.0,
        max_frames: Optional[int] = None,
    ) -> None:
        cap = _open_camera(
            camera_index,
            camera_width,
            camera_height,
            camera_fps,
        )
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"Could not open camera {camera_index}")
        frame_count = 0
        consecutive_read_failures = 0
        reopen_attempts = 0
        settings_reader = SharedSettingsReader()
        applied_calibration: tuple[float | None, float | None] | None = None
        last_quality_log_ms = 0
        controls_lock_attempted = False
        try:
            while not self._should_stop():
                ok, frame = cap.read()
                capture_timestamp_ms = monotonic_ms()
                if not ok:
                    consecutive_read_failures += 1
                    if (
                        consecutive_read_failures
                        < _CAMERA_READ_FAILURES_BEFORE_REOPEN
                    ):
                        time.sleep(0.02)
                        continue

                    # A new VideoCapture handle is a new measurement session.
                    # Publish paused immediately, then forget every old temporal
                    # assumption before the replacement camera starts producing.
                    last_quality_log_ms = self._reset_capture_session()
                    controls_lock_attempted = False
                    applied_calibration = None
                    cap.release()
                    reopen_attempts += 1
                    if reopen_attempts > _CAMERA_MAX_REOPEN_ATTEMPTS:
                        raise RuntimeError(
                            f"Camera {camera_index} stopped delivering frames"
                        )
                    print(
                        "[G3D] Camera read stalled; reopening "
                        f"({reopen_attempts}/{_CAMERA_MAX_REOPEN_ATTEMPTS})"
                    )
                    cap = _open_camera(
                        camera_index,
                        camera_width,
                        camera_height,
                        camera_fps,
                    )
                    if not cap.isOpened():
                        cap.release()
                        raise RuntimeError(f"Could not reopen camera {camera_index}")
                    consecutive_read_failures = 0
                    continue

                consecutive_read_failures = 0
                reopen_attempts = 0
                self._on_frame(frame)
                if self._camera_quality_monitor is not None:
                    camera_quality = self._camera_quality_monitor.update(
                        frame,
                        capture_timestamp_ms,
                    )
                    if (
                        elapsed_u32_ms(
                            capture_timestamp_ms,
                            last_quality_log_ms,
                        )
                        >= 2000
                    ):
                        problems = ", ".join(camera_quality.problems) or "none"
                        fps_text = (
                            f"{camera_quality.fps:.1f}"
                            if camera_quality.fps is not None
                            else "unknown"
                        )
                        print(
                            "[G3D] Camera quality "
                            f"{camera_quality.quality}: "
                            f"brightness={camera_quality.brightness:.2f} "
                            f"jitter={camera_quality.brightness_jitter:.3f} "
                            f"sharpness={camera_quality.sharpness:.1f} "
                            f"fps={fps_text} problems={problems}"
                        )
                        last_quality_log_ms = capture_timestamp_ms
                    if (
                        self._lock_camera_controls
                        and not controls_lock_attempted
                        and camera_quality.stable_for_lock
                    ):
                        controls_lock_attempted = True
                        result = try_lock_camera_controls(cap)
                        print(f"[G3D] Camera control lock result: {result}")

                settings = settings_reader.read()
                applied_calibration = _apply_live_calibration(
                    self._tracker,
                    settings,
                    applied_calibration,
                )
                self._smoother.set_measurement_noise(
                    _measurement_noise(settings)
                )
                measured = _validated_pose(
                    self._process_frame(frame, capture_timestamp_ms)
                )

                if measured is not None:
                    self._last_face_ms = time.monotonic() * 1000.0
                    limited = _limit_pose_step(
                        measured.xyz,
                        self._last_raw_pos,
                    )
                    self._last_raw_pos = limited
                    measured = HeadPosition(
                        x_cm=limited[0],
                        y_cm=limited[1],
                        z_cm=limited[2],
                        yaw_deg=measured.yaw_deg,
                        pitch_deg=measured.pitch_deg,
                        roll_deg=measured.roll_deg,
                        confidence=measured.confidence,
                        capture_timestamp_ms=measured.capture_timestamp_ms,
                    )
                    output = self._update_filter(measured)
                    status = "tracking"
                else:
                    now_ms = time.monotonic() * 1000.0
                    expired = (
                        self._last_face_ms is None
                        or now_ms - self._last_face_ms > self._hold_ms
                    )
                    if expired:
                        output = self._neutral_pose()
                        status = "paused"
                    else:
                        # Preserve the public state contract: a predicted/replayed
                        # pose during an async result gap is still a hold sample.
                        output = self._predict_filter()
                        status = "hold"

                if status != "paused":
                    output = _tilt_filtered_pose(
                        output,
                        self._camera_tilt_deg,
                    )
                self._last_output_pose = output
                self._last_smoothed = output.xyz
                self._publish(output, status)
                frame_count += 1
                if max_frames is not None and frame_count >= max_frames:
                    break
        finally:
            cap.release()
            settings_reader.close()

    def _should_stop(self) -> bool:
        return (
            self._stop_event.is_set()
            if self._stop_event is not None
            else False
        )

    def _on_frame(self, frame: object) -> None:  # noqa: ARG002
        pass

    def _on_position(
        self,
        x: float,
        y: float,
        z: float,
        status: str,
    ) -> None:  # noqa: ARG002
        pass


def _load_config(path: str = "config.yaml") -> dict[str, Any]:
    try:
        with open(path) as config_file:
            loaded = yaml.safe_load(config_file)
    except FileNotFoundError:
        raise SystemExit(f"ERROR: Config file not found: {path}")
    except yaml.YAMLError as error:
        raise SystemExit(f"ERROR: Invalid YAML in {path}: {error}")
    if not isinstance(loaded, dict):
        raise SystemExit(
            f"ERROR: Config top-level YAML must be a mapping: {path}"
        )
    return loaded


def _make_tray_image():
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (30, 30, 30, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse([8, 8, 56, 56], fill=(60, 200, 60, 255))
    draw.ellipse([26, 26, 38, 38], fill=(255, 255, 255, 255))
    return image


def main() -> None:
    parser = argparse.ArgumentParser(description="Glassless3D head tracker")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument(
        "--tracking-backend",
        choices=("auto", "mediapipe", "cv2"),
        default=None,
        help=(
            "face tracking backend; auto prefers MediaPipe and falls back "
            "to OpenCV"
        ),
    )
    args = parser.parse_args()
    cfg = _load_config(args.config)
    cam = cfg["camera"]
    scr = cfg["screen"]
    trk = cfg["tracking"]
    tracker_backend = args.tracking_backend or trk.get("tracker_backend", "auto")
    face_tracker_cls, selected_backend = _load_face_tracker_class(tracker_backend)
    camera_geometry = CameraGeometry.from_config(
        cfg,
        fallback_width=int(cam.get("width", 0)),
        fallback_height=int(cam.get("height", 0)),
    )
    camera_quality_monitor = CameraQualityMonitor(
        window_size=int(cam.get("quality_window_frames", 45)),
        minimum_sharpness=float(cam.get("minimum_sharpness", 35.0)),
        minimum_fps=float(cam.get("minimum_fps", 20.0)),
    )
    smoother = AdaptivePoseFilter(
        process_noise=float(trk.get("smoothing_q", 2.0)),
        measurement_noise=float(trk.get("smoothing_r", 0.1)),
        prediction_horizon_ms=float(
            trk.get("prediction_horizon_ms", 0.0)
        ),
        max_prediction_ms=float(trk.get("max_prediction_ms", 80.0)),
    )

    stop_event = threading.Event()
    tray_icon = None
    try:
        import pystray

        def _on_quit(icon, _item):
            stop_event.set()
            icon.stop()

        tray_icon = pystray.Icon(
            "G3D Tracker",
            _make_tray_image(),
            "Glassless3D Tracker",
            menu=pystray.Menu(
                pystray.MenuItem("Quit Tracker", _on_quit)
            ),
        )
        threading.Thread(target=tray_icon.run, daemon=True).start()
        print("[G3D] Tray icon active — right-click it to quit.")
    except Exception:
        print("[G3D] Tray icon unavailable — press Ctrl+C to stop.")

    print(f"[G3D] Starting tracker — camera {cam['index']}")
    print(f"[G3D] Tracking backend: {selected_backend}")
    print("[G3D] Latest-frame tracking + display-time prediction enabled")
    print("[G3D] Writing G3D legacy pose, G3D_PoseV2, and FT_SharedMem")

    with (
        face_tracker_cls(
            real_ipd_cm=_resolve_ipd_cm(cfg),
            screen_width_cm=scr["width_cm"],
            screen_height_cm=scr["height_cm"],
            camera_fov_deg=_resolve_camera_fov_deg(cfg),
            async_mode=bool(trk.get("live_stream", True)),
            min_tracking_confidence=float(
                trk.get("min_tracking_confidence", 0.5)
            ),
            camera_geometry=camera_geometry,
        ) as tracker,
        FreetracWriter() as ft_writer,
        SharedMemoryWriter() as g3d_writer,
        PoseStateWriter() as pose_writer,
        TrackingStateWriter() as state_writer,
    ):
        class _MultiWriter:
            def write(self, x: float, y: float, z: float) -> None:
                ft_writer.write(x=x, y=y, z=z)
                g3d_writer.write(x=x, y=y, z=z)

            def write_pose(self, pose: FilteredPose, *, valid: bool) -> None:
                pose_writer.write(pose, valid=valid)
                self.write(x=pose.x_cm, y=pose.y_cm, z=pose.z_cm)

            def write_state(self, state: str) -> None:
                state_writer.write(state)

        loop = TrackingLoop(
            tracker=tracker,
            writer=_MultiWriter(),
            smoother=smoother,
            hold_ms=int(trk["hold_ms"]),
            stop_event=stop_event,
            camera_tilt_deg=float(trk.get("camera_tilt_deg", 0.0)),
            config_path=args.config,
            camera_quality_monitor=camera_quality_monitor,
            lock_camera_controls=bool(
                cam.get("lock_controls_after_warmup", False)
            ),
        )
        try:
            loop.run(
                camera_index=int(cam["index"]),
                camera_width=int(cam.get("width", 0)),
                camera_height=int(cam.get("height", 0)),
                camera_fps=float(cam.get("fps", 0.0)),
            )
        except KeyboardInterrupt:
            pass
    if tray_icon is not None:
        tray_icon.stop()
    print("\n[G3D] Tracker stopped.")


if __name__ == "__main__":
    main()
