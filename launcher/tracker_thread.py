"""QThread that runs the head-tracking loop and emits Qt signals."""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import cv2
from PySide6.QtCore import QThread, Signal

from tracker.face_tracker import FaceTracker, HeadPosition
from tracker.freetrack import FreetracWriter
from tracker.smoother import HeadSmoother


class _SignallingLoop:
    """Tracking loop that checks a stop event and calls signal callbacks."""

    def __init__(
        self,
        stop_event: threading.Event,
        on_frame: Callable[[bytes], None],
        on_position: Callable[[float, float, float], None],
        on_status: Callable[[str], None],
        tracker: FaceTracker,
        writer: FreetracWriter,
        smoother: HeadSmoother,
        hold_ms: int = 500,
    ) -> None:
        self._stop_event = stop_event
        self._on_frame_cb = on_frame
        self._on_position_cb = on_position
        self._on_status_cb = on_status
        self._tracker = tracker
        self._writer = writer
        self._smoother = smoother
        self._hold_ms = hold_ms
        self._last_face_ms: Optional[float] = None
        self._last_smoothed: tuple[float, float, float] = (0.0, 0.0, 60.0)

    def run(self, camera_index: int = 0) -> None:
        """Run the tracking loop. Raises RuntimeError if camera cannot open."""
        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            raise RuntimeError(f"Could not open camera {camera_index}")
        try:
            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    break

                self._emit_frame(frame)

                pos: Optional[HeadPosition] = self._tracker.process_frame(frame)

                if pos is not None:
                    self._last_face_ms = time.monotonic() * 1000.0
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
                        x, y, z = self._last_smoothed
                        status = "hold"

                self._writer.write(x=x, y=y, z=z)
                self._on_position_cb(x, y, z)
                self._on_status_cb(status)
        finally:
            cap.release()

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

    def __init__(self, camera_index: int, config: dict, parent: object = None) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self._camera_index = camera_index
        self._config = config
        self._stop_event = threading.Event()

    def run(self) -> None:
        trk = self._config["tracking"]
        scr = self._config["screen"]
        smoother = HeadSmoother(
            process_noise=trk["smoothing_q"],
            measurement_noise=trk["smoothing_r"],
        )
        try:
            with (
                FaceTracker(
                    real_ipd_cm=trk["ipd_cm"],
                    screen_width_cm=scr["width_cm"],
                    screen_height_cm=scr["height_cm"],
                ) as tracker,
                FreetracWriter() as writer,
            ):
                loop = _SignallingLoop(
                    stop_event=self._stop_event,
                    on_frame=self.frame_ready.emit,
                    on_position=self.position_updated.emit,
                    on_status=self.status_changed.emit,
                    tracker=tracker,
                    writer=writer,
                    smoother=smoother,
                    hold_ms=trk["hold_ms"],
                )
                loop.run(camera_index=self._camera_index)
        except RuntimeError:
            self.status_changed.emit("error")

    def stop(self) -> None:
        """Signal the loop to stop and block until the thread exits."""
        self._stop_event.set()
        self.wait()
