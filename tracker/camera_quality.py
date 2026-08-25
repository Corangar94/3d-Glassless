"""Webcam exposure, sharpness, cadence, and control-stability monitoring."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import statistics
from typing import Protocol

import cv2
import numpy as np


@dataclass(frozen=True)
class CameraQualitySample:
    timestamp_ms: int
    brightness: float
    dark_fraction: float
    clipped_fraction: float
    sharpness: float
    frame_interval_ms: float | None


@dataclass(frozen=True)
class CameraQualityStatus:
    quality: str
    brightness: float
    brightness_jitter: float
    sharpness: float
    fps: float | None
    dark_fraction: float
    clipped_fraction: float
    problems: tuple[str, ...]
    stable_for_lock: bool


class CameraLike(Protocol):
    def get(self, property_id: int) -> float:
        ...

    def set(self, property_id: int, value: float) -> bool:
        ...


class CameraQualityMonitor:
    """Compute inexpensive camera-health metrics on downscaled grayscale frames."""

    def __init__(
        self,
        *,
        window_size: int = 45,
        minimum_sharpness: float = 35.0,
        minimum_fps: float = 20.0,
        brightness_range: tuple[float, float] = (0.18, 0.88),
    ) -> None:
        self._samples: deque[CameraQualitySample] = deque(maxlen=max(8, window_size))
        self._minimum_sharpness = max(0.0, float(minimum_sharpness))
        self._minimum_fps = max(1.0, float(minimum_fps))
        self._brightness_range = (
            min(brightness_range),
            max(brightness_range),
        )
        self._last_timestamp_ms: int | None = None

    def update(self, frame_bgr: np.ndarray, timestamp_ms: int) -> CameraQualityStatus:
        if frame_bgr.ndim != 3 or frame_bgr.shape[0] <= 0 or frame_bgr.shape[1] <= 0:
            raise ValueError("camera quality requires a non-empty BGR frame")
        height, width = frame_bgr.shape[:2]
        target_width = min(320, width)
        target_height = max(1, int(round(height * target_width / max(1, width))))
        reduced = cv2.resize(
            frame_bgr,
            (target_width, target_height),
            interpolation=cv2.INTER_AREA,
        )
        gray = cv2.cvtColor(reduced, cv2.COLOR_BGR2GRAY)
        brightness = float(np.mean(gray) / 255.0)
        dark_fraction = float(np.mean(gray <= 10))
        clipped_fraction = float(np.mean(gray >= 245))
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        interval = None
        if self._last_timestamp_ms is not None:
            delta = (int(timestamp_ms) - self._last_timestamp_ms) & 0xFFFF_FFFF
            if 0 < delta < 10_000:
                interval = float(delta)
        self._last_timestamp_ms = int(timestamp_ms) & 0xFFFF_FFFF
        self._samples.append(
            CameraQualitySample(
                timestamp_ms=int(timestamp_ms) & 0xFFFF_FFFF,
                brightness=brightness,
                dark_fraction=dark_fraction,
                clipped_fraction=clipped_fraction,
                sharpness=sharpness,
                frame_interval_ms=interval,
            )
        )
        return self.status()

    def status(self) -> CameraQualityStatus:
        if not self._samples:
            return CameraQualityStatus(
                quality="UNKNOWN",
                brightness=0.0,
                brightness_jitter=0.0,
                sharpness=0.0,
                fps=None,
                dark_fraction=0.0,
                clipped_fraction=0.0,
                problems=("no camera frames measured",),
                stable_for_lock=False,
            )
        brightness_values = [sample.brightness for sample in self._samples]
        sharpness_values = [sample.sharpness for sample in self._samples]
        intervals = [
            sample.frame_interval_ms
            for sample in self._samples
            if sample.frame_interval_ms is not None and sample.frame_interval_ms > 0.0
        ]
        brightness = float(statistics.median(brightness_values))
        brightness_jitter = (
            float(statistics.pstdev(brightness_values))
            if len(brightness_values) >= 2
            else 0.0
        )
        sharpness = float(statistics.median(sharpness_values))
        interval = float(statistics.median(intervals)) if intervals else 0.0
        fps = 1000.0 / interval if interval > 0.0 else None
        dark_fraction = float(statistics.median(sample.dark_fraction for sample in self._samples))
        clipped_fraction = float(statistics.median(sample.clipped_fraction for sample in self._samples))
        problems: list[str] = []
        if brightness < self._brightness_range[0] or dark_fraction > 0.35:
            problems.append("underexposed")
        if brightness > self._brightness_range[1] or clipped_fraction > 0.18:
            problems.append("overexposed")
        if sharpness < self._minimum_sharpness:
            problems.append("soft or motion-blurred")
        if fps is not None and fps < self._minimum_fps:
            problems.append(f"camera cadence low ({fps:.1f} fps)")
        if len(self._samples) >= 12 and brightness_jitter > 0.045:
            problems.append("exposure is hunting")
        severe = any(
            problem.startswith(("underexposed", "overexposed", "soft", "camera cadence"))
            for problem in problems
        )
        quality = "DANGER" if severe else ("WARN" if problems else "GOOD")
        stable_for_lock = (
            len(self._samples) >= min(30, self._samples.maxlen or 30)
            and not problems
            and brightness_jitter <= 0.018
            and sharpness >= self._minimum_sharpness * 1.25
        )
        return CameraQualityStatus(
            quality=quality,
            brightness=brightness,
            brightness_jitter=brightness_jitter,
            sharpness=sharpness,
            fps=fps,
            dark_fraction=dark_fraction,
            clipped_fraction=clipped_fraction,
            problems=tuple(problems),
            stable_for_lock=stable_for_lock,
        )


def try_lock_camera_controls(cap: CameraLike) -> dict[str, object]:
    """Best-effort focus/exposure locking after a stable warm-up.

    Backends disagree on AUTO_EXPOSURE values, so this is opt-in and reports
    every attempted property instead of pretending unsupported controls worked.
    """
    result: dict[str, object] = {}
    autofocus = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
    focus = getattr(cv2, "CAP_PROP_FOCUS", None)
    auto_exposure = getattr(cv2, "CAP_PROP_AUTO_EXPOSURE", None)
    exposure = getattr(cv2, "CAP_PROP_EXPOSURE", None)
    if autofocus is not None:
        result["focus_value"] = cap.get(focus) if focus is not None else None
        result["autofocus_locked"] = bool(cap.set(autofocus, 0.0))
        if focus is not None and math.isfinite(float(result["focus_value"])):
            result["focus_preserved"] = bool(cap.set(focus, float(result["focus_value"])))
    if auto_exposure is not None:
        result["exposure_value"] = cap.get(exposure) if exposure is not None else None
        locked = bool(cap.set(auto_exposure, 0.25))
        if not locked:
            locked = bool(cap.set(auto_exposure, 0.0))
        result["auto_exposure_locked"] = locked
        if exposure is not None and math.isfinite(float(result["exposure_value"])):
            result["exposure_preserved"] = bool(
                cap.set(exposure, float(result["exposure_value"]))
            )
    return result
