# tracker/main.py
import argparse
import threading
import time
from collections import deque
from typing import Optional

import cv2
import yaml

from tracker.face_tracker_cv2 import FaceTracker, HeadPosition
from tracker.freetrack import FreetracWriter
from tracker.shared_memory import SharedMemoryWriter
from tracker.smoother import HeadSmoother
from tracker.tilt import (
    _TILT_WINDOW,
    _TILT_MIN,
    _TILT_EVERY,
    _calibrate_tilt,
    _apply_camera_tilt,
    _save_tilt_to_config,
)


class TrackingLoop:
    """Reads webcam frames, tracks head pose, smooths, and writes to FT_SharedMem."""

    def __init__(
        self,
        tracker: FaceTracker,
        writer: FreetracWriter,
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
        self._stop_event = stop_event
        self._camera_tilt_deg: float = camera_tilt_deg
        self._config_path: Optional[str] = config_path

    def run(self, camera_index: int = 0, max_frames: Optional[int] = None) -> None:
        """Run the tracking loop. Blocks until max_frames reached or Ctrl+C."""
        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            raise RuntimeError(f"Could not open camera {camera_index}")
        frame_count = 0
        tilt_buf_y: deque[float] = deque(maxlen=_TILT_WINDOW)
        tilt_buf_z: deque[float] = deque(maxlen=_TILT_WINDOW)
        tilt_face_count = 0
        try:
            while not self._should_stop():
                ok, frame = cap.read()
                if not ok:
                    break

                self._on_frame(frame)

                pos: Optional[HeadPosition] = self._tracker.process_frame(frame)

                if pos is not None:
                    self._last_face_ms = time.monotonic() * 1000.0
                    # Continuous tilt calibration: collect raw pre-tilt samples
                    tilt_buf_y.append(pos.y_cm)
                    tilt_buf_z.append(pos.z_cm)
                    tilt_face_count += 1
                    if tilt_face_count % _TILT_EVERY == 0 and len(tilt_buf_y) >= _TILT_MIN:
                        new_tilt = _calibrate_tilt(list(tilt_buf_y), list(tilt_buf_z), _TILT_MIN)
                        if new_tilt is not None:
                            self._camera_tilt_deg = new_tilt
                            if self._config_path is not None:
                                _save_tilt_to_config(self._config_path, new_tilt)

                    smoothed = self._smoother.update(pos.x_cm, pos.y_cm, pos.z_cm)
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

                x, y, z = _apply_camera_tilt(x, y, z, self._camera_tilt_deg)
                self._writer.write(x=x, y=y, z=z)
                self._on_position(x, y, z, status)
                frame_count += 1
                if max_frames is not None and frame_count >= max_frames:
                    break
        finally:
            cap.release()

    def _should_stop(self) -> bool:
        if self._stop_event is not None:
            return self._stop_event.is_set()
        return False

    def _on_frame(self, frame: object) -> None:  # noqa: ARG002
        """Called with each captured frame before face detection."""

    def _on_position(self, x: float, y: float, z: float, status: str) -> None:  # noqa: ARG002
        """Called after each position is computed and written."""


def _load_config(path: str = "config.yaml") -> dict:
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        raise SystemExit(f"ERROR: Config file not found: {path}")
    except yaml.YAMLError as e:
        raise SystemExit(f"ERROR: Invalid YAML in {path}: {e}")


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
    args = parser.parse_args()

    cfg = _load_config(args.config)
    cam = cfg["camera"]
    scr = cfg["screen"]
    trk = cfg["tracking"]

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
    print("[G3D] Writing to shared memory (G3D protocol)")

    with FaceTracker(
        real_ipd_cm=trk["ipd_cm"],
        screen_width_cm=scr["width_cm"],
        screen_height_cm=scr["height_cm"],
    ) as tracker, FreetracWriter() as ft_writer, SharedMemoryWriter() as g3d_writer:
        # Combined writer: forwards every position to both sinks
        class _MultiWriter:
            def write(self, x: float, y: float, z: float) -> None:
                ft_writer.write(x=x, y=y, z=z)
                g3d_writer.write(x=x, y=y, z=z)

        loop = TrackingLoop(
            tracker=tracker,
            writer=_MultiWriter(),  # type: ignore[arg-type]
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
