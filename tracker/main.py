# tracker/main.py
import argparse
import time
from typing import Optional

import cv2
import yaml

from tracker.face_tracker import FaceTracker, HeadPosition
from tracker.freetrack import FreetracWriter
from tracker.smoother import HeadSmoother


class TrackingLoop:
    """Reads webcam frames, tracks head pose, smooths, and writes to FT_SharedMem."""

    def __init__(
        self,
        tracker: FaceTracker,
        writer: FreetracWriter,
        smoother: HeadSmoother,
        hold_ms: int = 500,
    ) -> None:
        self._tracker = tracker
        self._writer = writer
        self._smoother = smoother
        self._hold_ms = hold_ms
        self._last_face_ms: Optional[float] = None
        self._last_smoothed: tuple[float, float, float] = (0.0, 0.0, 60.0)

    def run(self, camera_index: int = 0, max_frames: Optional[int] = None) -> None:
        """Run the tracking loop. Blocks until max_frames reached or Ctrl+C."""
        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            raise RuntimeError(f"Could not open camera {camera_index}")
        frame_count = 0
        try:
            while not self._should_stop():
                ok, frame = cap.read()
                if not ok:
                    break

                self._on_frame(frame)

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
                        x, y, z = self._last_smoothed  # replay last output, do not update filter
                        status = "hold"

                self._writer.write(x=x, y=y, z=z)
                self._on_position(x, y, z, status)
                frame_count += 1
                if max_frames is not None and frame_count >= max_frames:
                    break
        finally:
            cap.release()

    def _should_stop(self) -> bool:
        """Return True to exit the loop. Base class never stops early."""
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

    print(f"[G3D] Starting tracker — camera {cam['index']}")
    print("[G3D] Writing to FT_SharedMem (FreeTrack protocol)")
    print("[G3D] Press Ctrl+C to stop.")

    with FaceTracker(
        real_ipd_cm=trk["ipd_cm"],
        screen_width_cm=scr["width_cm"],
        screen_height_cm=scr["height_cm"],
    ) as tracker, FreetracWriter() as writer:
        loop = TrackingLoop(
            tracker=tracker,
            writer=writer,
            smoother=smoother,
            hold_ms=trk["hold_ms"],
        )
        try:
            loop.run(camera_index=cam["index"])
        except KeyboardInterrupt:
            print("\n[G3D] Stopped.")


if __name__ == "__main__":
    main()
