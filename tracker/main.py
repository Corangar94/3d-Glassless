# tracker/main.py
import argparse
import importlib
import math
import threading
import time
from typing import Any, Optional, Protocol

import cv2
import yaml

from tracker.face_tracker_cv2 import HeadPosition
from tracker.freetrack import FreetracWriter
from tracker.shared_memory import SharedMemoryWriter, TrackingStateWriter
from tracker.shared_settings import OverlaySettings, SharedSettingsReader
from tracker.smoother import HeadSmoother
from tracker.tilt import _apply_camera_tilt, _calibrate_tilt

_CAMERA_READ_FAILURES_BEFORE_REOPEN = 3
_CAMERA_MAX_REOPEN_ATTEMPTS = 3
_DEFAULT_CAMERA_FOV_DEG = 90.0
_DEFAULT_SMOOTHING_R = 0.1


class FaceTrackerLike(Protocol):
    def process_frame(self, frame_bgr: object) -> HeadPosition | None:
        ...


class PoseWriterLike(Protocol):
    def write(self, *, x: float, y: float, z: float) -> None:
        ...


def _finite_float(value: object) -> float | None:
    """Return a finite float, or None for malformed/non-finite input."""
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _resolve_ipd_cm(cfg: dict[str, Any]) -> float:
    """Resolve the single runtime IPD source, preferring display calibration."""
    tracking_raw = cfg.get("tracking", {})
    overlay_raw = cfg.get("overlay", {})
    tracking = tracking_raw if isinstance(tracking_raw, dict) else {}
    overlay = overlay_raw if isinstance(overlay_raw, dict) else {}
    calibration_raw = overlay.get("display_calibration", {})
    calibration = calibration_raw if isinstance(calibration_raw, dict) else {}
    for value in (calibration.get("ipd_mm"), overlay.get("ipd_mm")):
        parsed = _finite_float(value)
        if parsed is not None and parsed > 0.0:
            return parsed / 10.0
    parsed_tracking_ipd = _finite_float(tracking.get("ipd_cm", 6.4))
    if parsed_tracking_ipd is None:
        return 6.4
    return max(0.1, parsed_tracking_ipd)


def _resolve_camera_fov_deg(cfg: dict[str, Any]) -> float:
    """Resolve a finite horizontal camera FOV, falling back to 90 degrees."""
    tracking_raw = cfg.get("tracking", {})
    overlay_raw = cfg.get("overlay", {})
    tracking = tracking_raw if isinstance(tracking_raw, dict) else {}
    overlay = overlay_raw if isinstance(overlay_raw, dict) else {}
    for value in (
        tracking.get("camera_fov_deg"),
        overlay.get("camera_fov_deg"),
    ):
        parsed = _finite_float(value)
        if parsed is not None and 0.0 < parsed < 180.0:
            return parsed
    return _DEFAULT_CAMERA_FOV_DEG


def _configure_camera(
    cap: cv2.VideoCapture, width: int = 0, height: int = 0, fps: float = 0.0
) -> None:
    """Apply requested capture properties; unsupported values degrade safely."""
    requested = (
        (cv2.CAP_PROP_FRAME_WIDTH, width),
        (cv2.CAP_PROP_FRAME_HEIGHT, height),
        (cv2.CAP_PROP_FPS, fps),
    )
    for property_id, value in requested:
        parsed = _finite_float(value)
        if parsed is None or parsed <= 0.0:
            continue
        try:
            cap.set(property_id, parsed)
        except (cv2.error, TypeError, ValueError):
            # Some backends raise instead of returning False for unsupported modes.
            continue


def _capture_property(cap: cv2.VideoCapture, property_id: int) -> float:
    """Read a non-negative finite camera property for diagnostics."""
    try:
        value = _finite_float(cap.get(property_id))
    except (cv2.error, TypeError, ValueError):
        return 0.0
    if value is None or value < 0.0:
        return 0.0
    return value


def _camera_mode(cap: cv2.VideoCapture) -> tuple[int, int, float]:
    """Return the actual capture mode without trusting backend sentinels."""
    width = int(round(_capture_property(cap, cv2.CAP_PROP_FRAME_WIDTH)))
    height = int(round(_capture_property(cap, cv2.CAP_PROP_FRAME_HEIGHT)))
    fps = _capture_property(cap, cv2.CAP_PROP_FPS)
    return width, height, fps


def _log_camera_opened(
    index: int, backend_name: str, cap: cv2.VideoCapture
) -> None:
    width, height, fps = _camera_mode(cap)
    print(
        f"[G3D] Camera {index} opened via {backend_name} "
        f"at {width}x{height} @ {fps:.1f} fps"
    )


def _open_camera(
    index: int, width: int = 0, height: int = 0, fps: float = 0.0
) -> cv2.VideoCapture:
    """Try multiple backends and apply configured camera properties."""
    backends = [
        (cv2.CAP_DSHOW, "CAP_DSHOW"),
        (cv2.CAP_MSMF, "CAP_MSMF"),
    ]
    for backend_id, backend_name in backends:
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
    """Return (FaceTracker class, selected backend id)."""
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
    """Return safe (IPD cm, FOV degrees) values from live shared settings."""
    if settings is None:
        return None, None

    ipd_mm = _finite_float(settings.ipd_mm)
    ipd_cm = ipd_mm / 10.0 if ipd_mm is not None and ipd_mm > 0.0 else None

    camera_fov_deg = _finite_float(settings.camera_fov_deg)
    if camera_fov_deg is None or not (0.0 < camera_fov_deg < 180.0):
        camera_fov_deg = None
    return ipd_cm, camera_fov_deg


def _measurement_noise(settings: OverlaySettings | None) -> float:
    """Return finite positive Kalman measurement noise from live settings."""
    if settings is None:
        return _DEFAULT_SMOOTHING_R
    parsed = _finite_float(settings.smoothing_alpha)
    if parsed is None or parsed <= 0.0:
        return _DEFAULT_SMOOTHING_R
    return max(parsed, 1e-6)


def _apply_live_calibration(
    tracker: FaceTrackerLike,
    settings: OverlaySettings | None,
    previous: tuple[float | None, float | None] | None,
) -> tuple[float | None, float | None] | None:
    """Apply changed, validated calibration before measuring the next frame."""
    calibration = _validated_live_calibration(settings)
    if calibration == previous:
        return previous

    real_ipd_cm, camera_fov_deg = calibration
    values: dict[str, float] = {}
    if real_ipd_cm is not None:
        values["real_ipd_cm"] = real_ipd_cm
    if camera_fov_deg is not None:
        values["camera_fov_deg"] = camera_fov_deg
    if not values:
        return previous

    set_calibration = getattr(tracker, "set_calibration", None)
    if not callable(set_calibration):
        return previous
    set_calibration(**values)
    return calibration


def _validated_pose(
    position: HeadPosition | None,
) -> tuple[float, float, float] | None:
    """Reject malformed tracker output before it can poison smoothing/parallax."""
    if position is None:
        return None
    x = _finite_float(position.x_cm)
    y = _finite_float(position.y_cm)
    z = _finite_float(position.z_cm)
    if x is None or y is None or z is None or z <= 0.0:
        return None
    return x, y, z


def _limit_pose_step(
    raw: tuple[float, float, float],
    prev: tuple[float, float, float] | None,
    max_xy_step_cm: float = 10.0,
    max_z_step_cm: float = 12.0,
) -> tuple[float, float, float]:
    """Clamp single-frame tracker spikes before they reach parallax math."""
    if prev is None:
        return raw

    dx = raw[0] - prev[0]
    dy = raw[1] - prev[1]
    xy_len = math.hypot(dx, dy)
    if max_xy_step_cm > 0.0 and xy_len > max_xy_step_cm:
        scale = max_xy_step_cm / xy_len
        x = prev[0] + dx * scale
        y = prev[1] + dy * scale
    else:
        x, y = raw[0], raw[1]

    dz = raw[2] - prev[2]
    if max_z_step_cm > 0.0:
        dz = max(-max_z_step_cm, min(max_z_step_cm, dz))
    return x, y, prev[2] + dz


class TrackingLoop:
    """Reads webcam frames, tracks head pose, smooths, and writes pose output."""

    def __init__(
        self,
        tracker: FaceTrackerLike,
        writer: PoseWriterLike,
        smoother: HeadSmoother,
        hold_ms: int = 500,
        stop_event: Optional[threading.Event] = None,
        camera_tilt_deg: float = 0.0,
        config_path: Optional[str] = None,
    ) -> None:
        self._tracker = tracker
        self._writer = writer
        self._smoother = smoother
        self._hold_ms = hold_ms
        self._last_face_ms: Optional[float] = None
        self._last_smoothed: tuple[float, float, float] = (0.0, 0.0, 60.0)
        self._last_raw_pos: tuple[float, float, float] | None = None
        self._last_measurement_s: float | None = None
        self._stop_event = stop_event
        self._camera_tilt_deg: float = camera_tilt_deg
        self._config_path: Optional[str] = config_path

    def run(
        self,
        camera_index: int = 0,
        camera_width: int = 0,
        camera_height: int = 0,
        camera_fps: float = 0.0,
        max_frames: Optional[int] = None,
    ) -> None:
        """Run the tracking loop. Blocks until max_frames reached or Ctrl+C."""
        cap = _open_camera(camera_index, camera_width, camera_height, camera_fps)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"Could not open camera {camera_index}")
        frame_count = 0
        consecutive_read_failures = 0
        reopen_attempts = 0
        settings_reader = SharedSettingsReader()
        applied_calibration: tuple[float | None, float | None] | None = None
        try:
            while not self._should_stop():
                ok, frame = cap.read()
                if not ok:
                    consecutive_read_failures += 1
                    if consecutive_read_failures < _CAMERA_READ_FAILURES_BEFORE_REOPEN:
                        time.sleep(0.02)
                        continue

                    cap.release()
                    reopen_attempts += 1
                    if reopen_attempts > _CAMERA_MAX_REOPEN_ATTEMPTS:
                        raise RuntimeError(
                            f"Camera {camera_index} stopped delivering frames"
                        )
                    print(
                        f"[G3D] Camera read stalled; reopening "
                        f"({reopen_attempts}/{_CAMERA_MAX_REOPEN_ATTEMPTS})"
                    )
                    cap = _open_camera(
                        camera_index, camera_width, camera_height, camera_fps
                    )
                    if not cap.isOpened():
                        cap.release()
                        raise RuntimeError(
                            f"Could not reopen camera {camera_index}"
                        )
                    consecutive_read_failures = 0
                    continue

                consecutive_read_failures = 0
                reopen_attempts = 0

                self._on_frame(frame)

                # Apply GUI calibration before processing this frame so the
                # depth estimate never lags a live calibration update by one frame.
                settings = settings_reader.read()
                applied_calibration = _apply_live_calibration(
                    self._tracker, settings, applied_calibration
                )
                raw_position = _validated_pose(self._tracker.process_frame(frame))

                if raw_position is not None:
                    measurement_s = time.monotonic()
                    self._last_face_ms = measurement_s * 1000.0
                    self._smoother.set_measurement_noise(
                        _measurement_noise(settings)
                    )
                    raw = _limit_pose_step(raw_position, self._last_raw_pos)
                    self._last_raw_pos = raw
                    effective = raw
                    dt_s = (
                        measurement_s - self._last_measurement_s
                        if self._last_measurement_s is not None
                        else None
                    )
                    self._last_measurement_s = measurement_s
                    if dt_s is None:
                        smoothed = self._smoother.update(
                            effective[0], effective[1], effective[2]
                        )
                    else:
                        smoothed = self._smoother.update(
                            effective[0],
                            effective[1],
                            effective[2],
                            dt_seconds=dt_s,
                        )
                    self._last_smoothed = smoothed
                    x, y, z = smoothed
                    status = "tracking"
                else:
                    now_ms = time.monotonic() * 1000.0
                    hold_expired = (
                        self._last_face_ms is None
                        or now_ms - self._last_face_ms > self._hold_ms
                    )
                    if hold_expired:
                        x, y, z = 0.0, 0.0, 60.0
                        status = "paused"
                    else:
                        x, y, z = self._last_smoothed
                        status = "hold"

                # A neutral fallback is screen-space neutral. Rotating (0, 0, 60)
                # creates a large false Y movement whenever the camera is tilted.
                if status != "paused":
                    x, y, z = _apply_camera_tilt(
                        x, y, z, self._camera_tilt_deg
                    )
                write_state = getattr(self._writer, "write_state", None)
                if callable(write_state):
                    write_state(status)
                # Publish the pose last. TrackerProcess treats a new pose timestamp
                # as the commit marker and then reads G3D_State.
                self._writer.write(x=x, y=y, z=z)
                self._on_position(x, y, z, status)
                frame_count += 1
                if max_frames is not None and frame_count >= max_frames:
                    break
        finally:
            cap.release()
            settings_reader.close()

    def _should_stop(self) -> bool:
        if self._stop_event is not None:
            return self._stop_event.is_set()
        return False

    def _on_frame(self, frame: object) -> None:  # noqa: ARG002
        """Called with each captured frame before face detection."""

    def _on_position(
        self, x: float, y: float, z: float, status: str
    ) -> None:  # noqa: ARG002
        """Called after each position is computed and written."""


def _load_config(path: str = "config.yaml") -> dict[str, Any]:
    try:
        with open(path) as f:
            loaded = yaml.safe_load(f)
    except FileNotFoundError:
        raise SystemExit(f"ERROR: Config file not found: {path}")
    except yaml.YAMLError as e:
        raise SystemExit(f"ERROR: Invalid YAML in {path}: {e}")
    if not isinstance(loaded, dict):
        raise SystemExit(f"ERROR: Config top-level YAML must be a mapping: {path}")
    return loaded


def _make_tray_image():
    """Create a simple 64x64 icon for the system tray."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (30, 30, 30, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill=(60, 200, 60, 255))
    draw.ellipse([26, 26, 38, 38], fill=(255, 255, 255, 255))
    return img


def main() -> None:
    parser = argparse.ArgumentParser(description="Glassless3D head tracker")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument(
        "--tracking-backend",
        choices=("auto", "mediapipe", "cv2"),
        default=None,
        help="face tracking backend; auto prefers MediaPipe and falls back to OpenCV",
    )
    args = parser.parse_args()

    cfg = _load_config(args.config)
    cam = cfg["camera"]
    scr = cfg["screen"]
    trk = cfg["tracking"]
    tracker_backend = args.tracking_backend or trk.get("tracker_backend", "auto")
    face_tracker_cls, selected_backend = _load_face_tracker_class(tracker_backend)

    smoother = HeadSmoother(
        process_noise=trk["smoothing_q"],
        measurement_noise=trk["smoothing_r"],
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
            menu=pystray.Menu(pystray.MenuItem("Quit Tracker", _on_quit)),
        )
        tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
        tray_thread.start()
        print("[G3D] Tray icon active — right-click it to quit.")
    except Exception:
        print("[G3D] Tray icon unavailable — press Ctrl+C to stop.")

    print(f"[G3D] Starting tracker — camera {cam['index']}")
    print(f"[G3D] Tracking backend: {selected_backend}")
    print("[G3D] Writing to shared memory (G3D + FT_SharedMem)")

    with (
        face_tracker_cls(
            real_ipd_cm=_resolve_ipd_cm(cfg),
            screen_width_cm=scr["width_cm"],
            screen_height_cm=scr["height_cm"],
            camera_fov_deg=_resolve_camera_fov_deg(cfg),
        ) as tracker,
        FreetracWriter() as ft_writer,
        SharedMemoryWriter() as g3d_writer,
        TrackingStateWriter() as state_writer,
    ):
        class _MultiWriter:
            def write(self, x: float, y: float, z: float) -> None:
                ft_writer.write(x=x, y=y, z=z)
                g3d_writer.write(x=x, y=y, z=z)

            def write_state(self, state: str) -> None:
                state_writer.write(state)

        loop = TrackingLoop(
            tracker=tracker,
            writer=_MultiWriter(),
            smoother=smoother,
            hold_ms=trk["hold_ms"],
            stop_event=stop_event,
            camera_tilt_deg=float(trk.get("camera_tilt_deg", 0.0)),
            config_path=args.config,
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
