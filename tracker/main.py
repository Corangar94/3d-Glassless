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
from tracker.shared_settings import SharedSettingsReader
from tracker.smoother import HeadSmoother
from tracker.tilt import _apply_camera_tilt, _calibrate_tilt

_CAMERA_READ_FAILURES_BEFORE_REOPEN = 3
_CAMERA_MAX_REOPEN_ATTEMPTS = 3


class FaceTrackerLike(Protocol):
    def process_frame(self, frame_bgr: object) -> HeadPosition | None:
        ...


class PoseWriterLike(Protocol):
    def write(self, *, x: float, y: float, z: float) -> None:
        ...


def _resolve_ipd_cm(cfg: dict[str, Any]) -> float:
    """Resolve the single runtime IPD source, preferring display calibration."""
    tracking_raw = cfg.get("tracking", {})
    overlay_raw = cfg.get("overlay", {})
    tracking = tracking_raw if isinstance(tracking_raw, dict) else {}
    overlay = overlay_raw if isinstance(overlay_raw, dict) else {}
    calibration_raw = overlay.get("display_calibration", {})
    calibration = calibration_raw if isinstance(calibration_raw, dict) else {}
    candidates_mm = (
        calibration.get("ipd_mm"),
        overlay.get("ipd_mm"),
    )
    for value in candidates_mm:
        if not isinstance(value, (int, float, str)):
            continue
        try:
            parsed = float(value)
        except ValueError:
            continue
        if parsed > 0.0:
            return parsed / 10.0
    tracking_ipd = tracking.get("ipd_cm", 6.4)
    if not isinstance(tracking_ipd, (int, float, str)):
        return 6.4
    try:
        return max(0.1, float(tracking_ipd))
    except ValueError:
        return 6.4


def _open_camera(index: int) -> cv2.VideoCapture:
    """Try multiple backends in order and return the first opened capture."""
    backends = [
        (cv2.CAP_DSHOW, "CAP_DSHOW"),
        (cv2.CAP_MSMF, "CAP_MSMF"),
    ]
    for backend_id, backend_name in backends:
        cap = cv2.VideoCapture(index, backend_id)
        if cap.isOpened():
            print(f"[G3D] Camera {index} opened via {backend_name}")
            return cap
        cap.release()

    cap = cv2.VideoCapture(index)
    if cap.isOpened():
        print(f"[G3D] Camera {index} opened via default backend")
        return cap
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


def _apply_deadzone(
    raw: tuple[float, float, float],
    prev: tuple[float, float, float] | None,
    deadzone_cm: float,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Suppress small XY movement while keeping Z responsive."""
    if prev is None:
        return raw, raw
    if math.hypot(raw[0] - prev[0], raw[1] - prev[1]) < deadzone_cm:
        return (prev[0], prev[1], raw[2]), prev
    return raw, raw


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

    def run(self, camera_index: int = 0, max_frames: Optional[int] = None) -> None:
        """Run the tracking loop. Blocks until max_frames reached or Ctrl+C."""
        cap = _open_camera(camera_index)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(f"Could not open camera {camera_index}")
        frame_count = 0
        consecutive_read_failures = 0
        reopen_attempts = 0
        settings_reader = SharedSettingsReader()
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
                    cap = _open_camera(camera_index)
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

                pos: Optional[HeadPosition] = self._tracker.process_frame(frame)

                if pos is not None:
                    measurement_s = time.monotonic()
                    self._last_face_ms = measurement_s * 1000.0
                    settings = settings_reader.read()
                    deadzone_cm = (settings.deadzone_mm / 10.0) if settings else 0.5
                    smoothing_r = settings.smoothing_alpha if settings else 0.1
                    self._smoother.set_measurement_noise(max(smoothing_r, 1e-6))
                    raw = _limit_pose_step(
                        (pos.x_cm, pos.y_cm, pos.z_cm),
                        self._last_raw_pos,
                    )
                    effective, self._last_raw_pos = _apply_deadzone(
                        raw, self._last_raw_pos, deadzone_cm
                    )
                    dt_s = (
                        measurement_s - self._last_measurement_s
                        if self._last_measurement_s is not None else None
                    )
                    self._last_measurement_s = measurement_s
                    if dt_s is None:
                        smoothed = self._smoother.update(effective[0], effective[1], effective[2])
                    else:
                        smoothed = self._smoother.update(
                            effective[0], effective[1], effective[2], dt_seconds=dt_s
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
                        x, y, z = self._last_smoothed  # replay last output, do not update filter
                        status = "hold"

                # A neutral fallback is screen-space neutral. Rotating (0, 0, 60)
                # creates a large false Y movement whenever the camera is tilted.
                if status != "paused":
                    x, y, z = _apply_camera_tilt(x, y, z, self._camera_tilt_deg)
                write_state = getattr(self._writer, "write_state", None)
                if callable(write_state):
                    write_state(status)
                # Publish the pose last.  TrackerProcess treats a new pose
                # timestamp as the commit marker and then reads G3D_State, so
                # writing state first keeps the two channels on the same sample.
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

    def _on_position(self, x: float, y: float, z: float, status: str) -> None:  # noqa: ARG002
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
    # Green circle = face detected indicator
    draw.ellipse([8, 8, 56, 56], fill=(60, 200, 60, 255))
    # White dot in centre
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

    # System tray icon (optional — skipped if pystray unavailable)
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

    with face_tracker_cls(
        real_ipd_cm=_resolve_ipd_cm(cfg),
        screen_width_cm=scr["width_cm"],
        screen_height_cm=scr["height_cm"],
        camera_fov_deg=float(trk.get("camera_fov_deg", cfg.get("overlay", {}).get("camera_fov_deg", 60.0))),
    ) as tracker, FreetracWriter() as ft_writer, SharedMemoryWriter() as g3d_writer, TrackingStateWriter() as state_writer:
        # Combined writer: forwards every position to both sinks
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
            loop.run(camera_index=cam["index"])
        except KeyboardInterrupt:
            pass

    if tray_icon is not None:
        tray_icon.stop()
    print("\n[G3D] Tracker stopped.")


if __name__ == "__main__":
    main()
