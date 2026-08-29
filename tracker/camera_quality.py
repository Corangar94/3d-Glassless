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

    def reset(self) -> None:
        """Forget samples from a retired capture session.

        A reopened webcam may have a different cadence, exposure state, focus,
        or even a different physical device behind the same index. Carrying the
        previous deque across that boundary can report false exposure hunting,
        calculate a bogus FPS interval, and immediately lock controls from stale
        evidence. Start the warm-up window again for every capture session.
        """
        self._samples.clear()
        self._last_timestamp_ms = None

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
        dark_fraction = float(
            statistics.median(sample.dark_fraction for sample in self._samples)
        )
        clipped_fraction = float(
            statistics.median(sample.clipped_fraction for sample in self._samples)
        )
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
            problem.startswith(
                ("underexposed", "overexposed", "soft", "camera cadence")
            )
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


def _read_camera_control(
    cap: CameraLike,
    property_id: int | None,
    name: str,
    errors: list[str],
) -> float | None:
    if property_id is None:
        return None
    try:
        parsed = float(cap.get(property_id))
    except Exception as error:  # hardware/backend boundary must remain fail-safe
        errors.append(f"{name} read failed: {type(error).__name__}")
        return None
    if not math.isfinite(parsed):
        errors.append(f"{name} read returned a non-finite value")
        return None
    return parsed


def _write_camera_control(
    cap: CameraLike,
    property_id: int | None,
    value: float,
    name: str,
    errors: list[str],
) -> bool:
    if property_id is None:
        return False
    try:
        return bool(cap.set(property_id, float(value)))
    except Exception as error:  # OpenCV backends may throw instead of returning False
        errors.append(f"{name} write failed: {type(error).__name__}")
        return False


def try_lock_camera_controls(cap: CameraLike) -> dict[str, object]:
    """Best-effort focus/exposure locking after a stable warm-up.

    Camera backends disagree on supported properties, AUTO_EXPOSURE values, and
    whether unsupported access returns ``False``/``None`` or raises. This
    optional optimization must never terminate tracking. Values are restored
    only after the corresponding automatic mode was successfully disabled.
    """
    result: dict[str, object] = {}
    errors: list[str] = []
    autofocus = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
    focus = getattr(cv2, "CAP_PROP_FOCUS", None)
    auto_exposure = getattr(cv2, "CAP_PROP_AUTO_EXPOSURE", None)
    exposure = getattr(cv2, "CAP_PROP_EXPOSURE", None)

    if autofocus is not None:
        focus_value = _read_camera_control(cap, focus, "focus", errors)
        autofocus_locked = _write_camera_control(
            cap,
            autofocus,
            0.0,
            "autofocus",
            errors,
        )
        result["focus_value"] = focus_value
        result["autofocus_locked"] = autofocus_locked
        result["focus_preserved"] = bool(
            autofocus_locked
            and focus_value is not None
            and _write_camera_control(
                cap,
                focus,
                focus_value,
                "focus",
                errors,
            )
        )

    if auto_exposure is not None:
        exposure_value = _read_camera_control(cap, exposure, "exposure", errors)
        # DirectShow commonly uses 0.25 for manual mode; some MSMF/backends use 0.
        auto_exposure_locked = _write_camera_control(
            cap,
            auto_exposure,
            0.25,
            "auto exposure",
            errors,
        )
        if not auto_exposure_locked:
            auto_exposure_locked = _write_camera_control(
                cap,
                auto_exposure,
                0.0,
                "auto exposure fallback",
                errors,
            )
        result["exposure_value"] = exposure_value
        result["auto_exposure_locked"] = auto_exposure_locked
        result["exposure_preserved"] = bool(
            auto_exposure_locked
            and exposure_value is not None
            and _write_camera_control(
                cap,
                exposure,
                exposure_value,
                "exposure",
                errors,
            )
        )

    if errors:
        result["errors"] = tuple(errors)
    return result
