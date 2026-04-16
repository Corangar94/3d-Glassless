"""QThread that runs the head-tracking loop and emits Qt signals."""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Callable, Optional

import cv2
from PySide6.QtCore import QThread, Signal

# Heavy tracker deps (mediapipe) are imported lazily inside TrackerThread.run()
# so that importing this module doesn't block the launcher for 30+ seconds.
from tracker.freetrack import FreetracWriter
from tracker.shared_memory import SharedMemoryWriter
from tracker.shared_settings import SharedSettingsReader
from tracker.smoother import HeadSmoother
from tracker.tilt import (
    _TILT_EVERY,
    _TILT_MIN,
    _TILT_WINDOW,
    _apply_camera_tilt,
    _calibrate_tilt,
    _save_tilt_to_config,
)

if TYPE_CHECKING:
    from tracker.face_tracker import FaceTracker, HeadPosition


def _apply_deadzone(
    raw: tuple[float, float, float],
    prev: tuple[float, float, float] | None,
    deadzone_cm: float,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Return (effective_pos, new_prev).

    Suppresses XY movements smaller than deadzone_cm. Z (distance) is
    always passed through.
    """
    if prev is None:
        return raw, raw
    if math.hypot(raw[0] - prev[0], raw[1] - prev[1]) < deadzone_cm:
        # XY clamped to previous; Z always passes through
        effective = (prev[0], prev[1], raw[2])
        return effective, prev
    return raw, raw


class _SignallingLoop:
    """Tracking loop that checks a stop event and calls signal callbacks.

    Note: This class intentionally reimplements the hold/status state machine
    from tracker.main.TrackingLoop rather than subclassing it, so that
    cv2.VideoCapture is opened inside this module and can be patched in tests
    via launcher.tracker_thread.cv2.VideoCapture. If TrackingLoop's hold logic
    changes, update the corresponding block in _SignallingLoop.run() here.
    """

    def __init__(
        self,
        stop_event: threading.Event,
        on_frame: Callable[[bytes], None],
        on_position: Callable[[float, float, float], None],
        on_status: Callable[[str], None],
        on_camera_error: Callable[[], None],
        tracker: FaceTracker,
        writer: FreetracWriter,
        g3d_writer: SharedMemoryWriter,
        smoother: HeadSmoother,
        hold_ms: int = 500,
        camera_tilt_deg: float = 0.0,
        config_path: Optional[str] = None,
    ) -> None:
        self._stop_event = stop_event
        self._on_frame_cb = on_frame
        self._on_position_cb = on_position
        self._on_status_cb = on_status
        self._on_camera_error_cb = on_camera_error
        self._tracker = tracker
        self._writer = writer
        self._g3d_writer = g3d_writer
        self._smoother = smoother
        self._hold_ms = hold_ms
        self._camera_tilt_deg = camera_tilt_deg
        self._config_path = config_path
        self._settings_reader = SharedSettingsReader()
        self._last_raw_pos: tuple[float, float, float] | None = None
        self._last_face_ms: Optional[float] = None
        self._last_smoothed: tuple[float, float, float] = (0.0, 0.0, 60.0)

    def run(self, camera_index: int = 0) -> None:
        """Run the tracking loop. Raises RuntimeError if camera cannot open."""
        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            raise RuntimeError(f"Could not open camera {camera_index}")
        tilt_buf_y: deque[float] = deque(maxlen=_TILT_WINDOW)
        tilt_buf_z: deque[float] = deque(maxlen=_TILT_WINDOW)
        tilt_face_count = 0
        try:
            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    break

                self._emit_frame(frame)

                pos: Optional[HeadPosition] = self._tracker.process_frame(frame)

                if pos is not None:
                    self._last_face_ms = time.monotonic() * 1000.0
                    settings = self._settings_reader.read()
                    deadzone_cm = (settings.deadzone_mm / 10.0) if settings else 0.5
                    smoothing_r = settings.smoothing_alpha if settings else 0.1
                    self._smoother.set_measurement_noise(max(smoothing_r, 1e-6))
                    raw = (pos.x_cm, pos.y_cm, pos.z_cm)
                    effective, self._last_raw_pos = _apply_deadzone(
                        raw, self._last_raw_pos, deadzone_cm
                    )
                    smoothed = self._smoother.update(effective[0], effective[1], effective[2])
                    self._last_smoothed = smoothed
                    x, y, z = smoothed
                    status = "tracking"

                    # Continuous tilt calibration: collect raw pre-tilt samples
                    tilt_buf_y.append(pos.y_cm)
                    tilt_buf_z.append(pos.z_cm)
                    tilt_face_count += 1
                    if tilt_face_count % _TILT_EVERY == 0 and len(tilt_buf_y) >= _TILT_MIN:
                        new_tilt = _calibrate_tilt(
                            list(tilt_buf_y), list(tilt_buf_z), _TILT_MIN
                        )
                        if new_tilt is not None:
                            self._camera_tilt_deg = new_tilt
                            if self._config_path:
                                _save_tilt_to_config(self._config_path, new_tilt)
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

                x, y, z = _apply_camera_tilt(x, y, z, self._camera_tilt_deg)
                self._writer.write(x=x, y=y, z=z)
                # Also publish to the G3D shared memory that the overlay reads
                self._g3d_writer.write(x=x, y=y, z=z)
                self._on_position_cb(x, y, z)
                self._on_status_cb(status)
            if not self._stop_event.is_set():
                self._on_camera_error_cb()
        finally:
            cap.release()
            self._settings_reader.close()

    def _emit_frame(self, frame: object) -> None:
        try:
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
            if ok:
                self._on_frame_cb(bytes(buf))
        except Exception:  # noqa: BLE001
            pass  # Non-numpy frames (e.g. mocks in tests) are silently dropped


class TrackerThread(QThread):
    """Runs the tracking loop in a background thread, emitting Qt signals."""

    position_updated = Signal(float, float, float)  # x, y, z in cm
    frame_ready = Signal(bytes)                      # JPEG-encoded camera frame
    status_changed = Signal(str)                     # "tracking"|"hold"|"paused"|"error"

    def __init__(
        self,
        camera_index: int,
        config: dict,
        config_path: str = "",
        parent: object = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self._camera_index = camera_index
        self._config = config
        self._config_path = config_path
        self._stop_event = threading.Event()

    def run(self) -> None:
        # Lazy import: mediapipe loads here (in the QThread worker), not at module level.
        from tracker.face_tracker import FaceTracker  # noqa: PLC0415
        trk = self._config["tracking"]
        scr = self._config["screen"]
        smoother = HeadSmoother(
            process_noise=trk["smoothing_q"],
            measurement_noise=trk["smoothing_r"],
        )
        with SharedSettingsReader() as _r:
            _startup = _r.read()
        _ipd_cm = (
            (_startup.ipd_mm / 10.0)
            if _startup and _startup.ipd_mm > 0
            else trk["ipd_cm"]
        )
        _fov_deg = (
            _startup.camera_fov_deg
            if _startup and _startup.camera_fov_deg > 0
            else 60.0
        )
        try:
            with (
                FaceTracker(
                    real_ipd_cm=_ipd_cm,
                    screen_width_cm=scr["width_cm"],
                    screen_height_cm=scr["height_cm"],
                    camera_fov_deg=_fov_deg,
                ) as tracker,
                FreetracWriter() as writer,
                SharedMemoryWriter() as g3d_writer,
            ):
                loop = _SignallingLoop(
                    stop_event=self._stop_event,
                    on_frame=self.frame_ready.emit,
                    on_position=self.position_updated.emit,
                    on_status=self.status_changed.emit,
                    on_camera_error=lambda: self.status_changed.emit("error"),
                    tracker=tracker,
                    writer=writer,
                    g3d_writer=g3d_writer,
                    smoother=smoother,
                    hold_ms=trk["hold_ms"],
                    camera_tilt_deg=float(trk.get("camera_tilt_deg", 0.0)),
                    config_path=self._config_path,
                )
                loop.run(camera_index=self._camera_index)
        except RuntimeError:
            self.status_changed.emit("error")

    def stop(self) -> None:
        """Signal the loop to stop and block until the thread exits."""
        self._stop_event.set()
        self.wait()
