"""Webcam exposure, sharpness, cadence, and control-stability monitoring."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import statistics
from typing import Iterable, Protocol

import cv2
import numpy as np

from tracker.pose import elapsed_u32_ms, normalize_wire_timestamp


@dataclass(frozen=True)
class CameraQualitySample:
    timestamp_ms: int
    brightness: float
    dark_fraction: float
    clipped_fraction: float
    sharpness: float
    frame_interval_ms: float | None
    analyzed: bool = True


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
    """Measure frame cadence continuously and image quality at a lower rate.

    Cadence must use every delivered frame to remain trustworthy. Brightness,
    clipping, and Laplacian sharpness change far more slowly and are relatively
    expensive, so those image metrics are sampled on a wrap-safe interval while
    the most recent values are carried through the frame-rate window.
    """

    def __init__(
        self,
        *,
        window_size: int = 45,
        minimum_sharpness: float = 35.0,
        minimum_fps: float = 20.0,
        brightness_range: tuple[float, float] = (0.18, 0.88),
        analysis_interval_ms: int = 80,
    ) -> None:
        interval = int(analysis_interval_ms)
        if interval < 0:
            raise ValueError("analysis_interval_ms cannot be negative")
        self._samples: deque[CameraQualitySample] = deque(
            maxlen=max(8, window_size)
        )
        self._minimum_sharpness = max(0.0, float(minimum_sharpness))
        self._minimum_fps = max(1.0, float(minimum_fps))
        self._brightness_range = (
            min(brightness_range),
            max(brightness_range),
        )
        self._analysis_interval_ms = interval
        self._last_timestamp_ms: int | None = None
        self._last_analysis_timestamp_ms: int | None = None
        self._last_image_metrics: tuple[float, float, float, float] | None = None
        self._analysis_count = 0

    @property
    def image_analysis_count(self) -> int:
        return self._analysis_count

    @property
    def analysis_interval_ms(self) -> int:
        return self._analysis_interval_ms

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
        self._last_analysis_timestamp_ms = None
        self._last_image_metrics = None
        self._analysis_count = 0

    @staticmethod
    def _reduced_analysis_frame(frame_bgr: np.ndarray) -> np.ndarray:
        """Return an aspect-preserving BGR frame with longest edge <= 320 px."""
        height, width = frame_bgr.shape[:2]
        longest_edge = max(width, height)
        scale = min(1.0, 320.0 / max(1, longest_edge))
        if scale >= 1.0:
            return frame_bgr
        target_width = max(1, int(round(width * scale)))
        target_height = max(1, int(round(height * scale)))
        return cv2.resize(
            frame_bgr,
            (target_width, target_height),
            interpolation=cv2.INTER_AREA,
        )

    @staticmethod
    def _analyze_frame(
        frame_bgr: np.ndarray,
    ) -> tuple[float, float, float, float]:
        reduced = CameraQualityMonitor._reduced_analysis_frame(frame_bgr)
        gray = cv2.cvtColor(reduced, cv2.COLOR_BGR2GRAY)
        return (
            float(np.mean(gray) / 255.0),
            float(np.mean(gray <= 10)),
            float(np.mean(gray >= 245)),
            float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        )

    def _analysis_due(self, timestamp_ms: int) -> bool:
        previous = self._last_analysis_timestamp_ms
        return (
            self._last_image_metrics is None
            or previous is None
            or self._analysis_interval_ms == 0
            or elapsed_u32_ms(timestamp_ms, previous)
            >= self._analysis_interval_ms
        )

    def update(
        self,
        frame_bgr: np.ndarray,
        timestamp_ms: int,
    ) -> CameraQualityStatus:
        if (
            frame_bgr.ndim != 3
            or frame_bgr.shape[0] <= 0
            or frame_bgr.shape[1] <= 0
        ):
            raise ValueError("camera quality requires a non-empty BGR frame")
        timestamp = normalize_wire_timestamp(timestamp_ms)
        interval = None
        if self._last_timestamp_ms is not None:
            delta = elapsed_u32_ms(timestamp, self._last_timestamp_ms)
            if 0 < delta < 10_000:
                interval = float(delta)
        self._last_timestamp_ms = timestamp

        analyzed = self._analysis_due(timestamp)
        if analyzed:
            self._last_image_metrics = self._analyze_frame(frame_bgr)
            self._last_analysis_timestamp_ms = timestamp
            self._analysis_count += 1
        metrics = self._last_image_metrics
        assert metrics is not None
        brightness, dark_fraction, clipped_fraction, sharpness = metrics

        self._samples.append(
            CameraQualitySample(
                timestamp_ms=timestamp,
                brightness=brightness,
                dark_fraction=dark_fraction,
                clipped_fraction=clipped_fraction,
                sharpness=sharpness,
                frame_interval_ms=interval,
                analyzed=analyzed,
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
            if sample.frame_interval_ms is not None
            and sample.frame_interval_ms > 0.0
        ]
        analysis_samples_in_window = sum(
            1 for sample in self._samples if sample.analyzed
        )
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
        if (
            len(self._samples) >= 12
            and analysis_samples_in_window >= 3
            and brightness_jitter > 0.045
        ):
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
            and self._analysis_count >= 10
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
    *,
    report_rejection: bool = True,
) -> bool:
    if property_id is None:
        if report_rejection:
            errors.append(f"{name} control is unavailable")
        return False
    try:
        accepted = bool(cap.set(property_id, float(value)))
    except Exception as error:  # OpenCV backends may throw instead of returning False
        errors.append(f"{name} write failed: {type(error).__name__}")
        return False
    if not accepted and report_rejection:
        errors.append(f"{name} write was rejected")
    return accepted


def _finite_unique(values: Iterable[float | None]) -> tuple[float, ...]:
    result: list[float] = []
    for value in values:
        if value is None:
            continue
        parsed = float(value)
        if not math.isfinite(parsed):
            continue
        if not any(abs(parsed - existing) <= 1e-6 for existing in result):
            result.append(parsed)
    return tuple(result)


def _write_first_accepted(
    cap: CameraLike,
    property_id: int | None,
    values: Iterable[float | None],
    name: str,
    errors: list[str],
) -> tuple[bool, float | None]:
    candidates = _finite_unique(values)
    if property_id is None:
        errors.append(f"{name} control is unavailable")
        return False, None
    for value in candidates:
        if _write_camera_control(
            cap,
            property_id,
            value,
            name,
            errors,
            report_rejection=False,
        ):
            return True, value
    errors.append(f"{name} write was rejected")
    return False, None


def _approximately(value: float | None, target: float) -> bool:
    return value is not None and abs(float(value) - float(target)) <= 1e-3


def _lock_focus_controls(
    cap: CameraLike,
    autofocus: int,
    focus: int | None,
    result: dict[str, object],
    errors: list[str],
) -> None:
    focus_value = _read_camera_control(cap, focus, "focus", errors)
    autofocus_value = _read_camera_control(
        cap,
        autofocus,
        "autofocus",
        errors,
    )
    result["focus_value"] = focus_value
    result["autofocus_value"] = autofocus_value

    if focus is None:
        errors.append("focus control is unavailable")
    if focus_value is None:
        # Disabling autofocus without a restorable manual value can leave the
        # camera permanently soft. Do not cross that hardware state boundary.
        result["autofocus_locked"] = False
        result["focus_preserved"] = False
        return
    if _approximately(autofocus_value, 0.0):
        result["autofocus_locked"] = True
        result["focus_preserved"] = True
        return

    locked = _write_camera_control(
        cap,
        autofocus,
        0.0,
        "autofocus manual mode",
        errors,
    )
    preserved = bool(
        locked
        and _write_camera_control(
            cap,
            focus,
            focus_value,
            "focus restore",
            errors,
        )
    )
    rollback = False
    if locked and not preserved:
        rollback, _value = _write_first_accepted(
            cap,
            autofocus,
            (autofocus_value, 1.0),
            "autofocus rollback",
            errors,
        )
        result["autofocus_rollback"] = rollback

    # Report final safe lock state, not merely whether the temporary mode write
    # was accepted before restoration or rollback.
    result["autofocus_locked"] = bool(locked and preserved)
    result["focus_preserved"] = preserved


def _lock_exposure_controls(
    cap: CameraLike,
    auto_exposure: int,
    exposure: int | None,
    result: dict[str, object],
    errors: list[str],
) -> None:
    exposure_value = _read_camera_control(cap, exposure, "exposure", errors)
    auto_exposure_value = _read_camera_control(
        cap,
        auto_exposure,
        "auto exposure",
        errors,
    )
    result["exposure_value"] = exposure_value
    result["auto_exposure_value"] = auto_exposure_value

    if exposure is None:
        errors.append("exposure control is unavailable")
    if exposure_value is None:
        # As with focus, never disable the automatic controller unless the
        # current manual value can be restored transactionally.
        result["auto_exposure_locked"] = False
        result["exposure_preserved"] = False
        return
    if _approximately(auto_exposure_value, 0.0) or _approximately(
        auto_exposure_value,
        0.25,
    ):
        result["auto_exposure_locked"] = True
        result["exposure_preserved"] = True
        return

    locked, manual_mode = _write_first_accepted(
        cap,
        auto_exposure,
        (0.25, 0.0),
        "auto exposure manual mode",
        errors,
    )
    if manual_mode is not None:
        result["auto_exposure_manual_value"] = manual_mode
    preserved = bool(
        locked
        and _write_camera_control(
            cap,
            exposure,
            exposure_value,
            "exposure restore",
            errors,
        )
    )
    rollback = False
    if locked and not preserved:
        rollback, rollback_value = _write_first_accepted(
            cap,
            auto_exposure,
            (auto_exposure_value, 0.75, 1.0),
            "auto exposure rollback",
            errors,
        )
        result["auto_exposure_rollback"] = rollback
        if rollback_value is not None:
            result["auto_exposure_rollback_value"] = rollback_value

    result["auto_exposure_locked"] = bool(locked and preserved)
    result["exposure_preserved"] = preserved


def try_lock_camera_controls(cap: CameraLike) -> dict[str, object]:
    """Best-effort transactional focus/exposure locking after warm-up.

    Automatic mode is disabled only when the current manual value has been read
    successfully. If restoring that value fails, the function immediately tries
    to roll the corresponding automatic mode back on. This optional optimization
    must never leave the camera in a worse state merely because a backend
    accepted the first half of a control transaction.
    """
    result: dict[str, object] = {}
    errors: list[str] = []
    autofocus = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
    focus = getattr(cv2, "CAP_PROP_FOCUS", None)
    auto_exposure = getattr(cv2, "CAP_PROP_AUTO_EXPOSURE", None)
    exposure = getattr(cv2, "CAP_PROP_EXPOSURE", None)

    if autofocus is not None:
        _lock_focus_controls(
            cap,
            autofocus,
            focus,
            result,
            errors,
        )

    if auto_exposure is not None:
        _lock_exposure_controls(
            cap,
            auto_exposure,
            exposure,
            result,
            errors,
        )

    if errors:
        result["errors"] = tuple(errors)
    return result
