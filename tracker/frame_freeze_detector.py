"""Detect a camera that repeatedly returns the same captured frame."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import math
import numbers

import numpy as np


_SAMPLE_ROWS = 180
_SAMPLE_COLUMNS = 320
_FULL_SAMPLE_THRESHOLD_BYTES = 256 * 1024


@dataclass(frozen=True)
class FrameFreezeObservation:
    """Result of observing one successfully captured frame."""

    checked: bool = False
    supported: bool = True
    frozen: bool = False
    frozen_age_ms: int | None = None
    episode_started: bool = False


@dataclass(frozen=True)
class FrameFreezeDetectorSnapshot:
    fingerprint_count: int
    freeze_episode_count: int
    frozen: bool
    last_frozen_age_ms: int | None
    full_fingerprint_count: int = 0


@dataclass(frozen=True)
class _FrameFingerprint:
    signature: tuple[object, ...]
    exact: bool


def _byte_view(view: memoryview) -> memoryview:
    return view if view.ndim == 1 and view.format == "B" else view.cast("B")


def _frame_metadata(frame: object, view: memoryview) -> tuple[object, ...]:
    shape = tuple(view.shape) if view.shape is not None else ()
    strides = tuple(view.strides) if view.strides is not None else ()
    return (
        type(frame).__module__,
        type(frame).__qualname__,
        view.format,
        int(view.itemsize),
        shape,
        strides,
        int(view.nbytes),
    )


@lru_cache(maxsize=32)
def _sample_indices(length: int, maximum: int) -> np.ndarray:
    count = min(maximum, length)
    if count == length:
        indices = np.arange(length, dtype=np.intp)
    else:
        indices = np.linspace(0, length - 1, count, dtype=np.intp)
    indices.flags.writeable = False
    return indices


def _sampled_frame_fingerprint(frame: object) -> _FrameFingerprint | None:
    """Return a bounded spatial fingerprint and whether it covers every byte."""
    try:
        view = memoryview(frame)
        if not view.c_contiguous:
            return None
        metadata = _frame_metadata(frame, view)
        if (
            not isinstance(frame, np.ndarray)
            or frame.ndim < 2
            or view.nbytes <= _FULL_SAMPLE_THRESHOLD_BYTES
        ):
            digest = hashlib.blake2b(
                _byte_view(view),
                digest_size=16,
            ).digest()
            return _FrameFingerprint(
                signature=metadata + ("full", digest),
                exact=True,
            )

        height = int(frame.shape[0])
        width = int(frame.shape[1])
        if height <= 0 or width <= 0:
            return None
        rows = _sample_indices(height, _SAMPLE_ROWS)
        columns = _sample_indices(width, _SAMPLE_COLUMNS)
        sample = np.ascontiguousarray(
            frame[rows[:, None], columns[None, :], ...]
        )
        digest = hashlib.blake2b(
            memoryview(sample).cast("B"),
            digest_size=16,
        ).digest()
        return _FrameFingerprint(
            signature=metadata
            + (
                "spatial-grid",
                int(rows.size),
                int(columns.size),
                int(sample.nbytes),
                digest,
            ),
            exact=False,
        )
    except Exception:
        return None


def _frame_signature(frame: object) -> tuple[object, ...] | None:
    """Retain the historical exact signature for focused direct callers."""
    return _full_frame_signature(frame)


def _full_frame_signature(frame: object) -> tuple[object, ...] | None:
    """Return an exact full-buffer signature for freeze confirmation."""
    try:
        view = memoryview(frame)
        if not view.c_contiguous:
            return None
        digest = hashlib.blake2b(
            _byte_view(view),
            digest_size=16,
        ).digest()
        return _frame_metadata(frame, view) + ("full", digest)
    except Exception:
        return None


class FrameFreezeDetector:
    """Sample frame identity cheaply, then verify a sustained freeze exactly.

    Large NumPy camera frames use a deterministic 320x180 spatial grid for the
    regular check. A full-buffer hash is captured only after two sampled frames
    match and again when the timeout is reached. Freeze declaration therefore
    still requires exact byte identity, while a changing high-resolution camera
    avoids repeatedly hashing its entire frame.
    """

    def __init__(
        self,
        *,
        check_interval_ms: int = 250,
        freeze_timeout_ms: int = 3_000,
    ) -> None:
        for name, value in (
            ("check_interval_ms", check_interval_ms),
            ("freeze_timeout_ms", freeze_timeout_ms),
        ):
            if isinstance(value, bool) or not isinstance(
                value,
                numbers.Integral,
            ):
                raise ValueError(f"{name} must be an integer")
            if int(value) < 0:
                raise ValueError(f"{name} cannot be negative")
        self._check_interval_ms = int(check_interval_ms)
        self._freeze_timeout_ms = int(freeze_timeout_ms)
        self._last_check_s: float | None = None
        self._last_signature: tuple[object, ...] | None = None
        self._full_baseline_signature: tuple[object, ...] | None = None
        self._identical_since_s: float | None = None
        self._frozen = False
        self._fingerprint_count = 0
        self._full_fingerprint_count = 0
        self._freeze_episode_count = 0
        self._last_frozen_age_ms: int | None = None

    @property
    def enabled(self) -> bool:
        return self._freeze_timeout_ms > 0

    def reset(self) -> None:
        self._last_check_s = None
        self._last_signature = None
        self._full_baseline_signature = None
        self._identical_since_s = None
        self._frozen = False
        self._last_frozen_age_ms = None

    @staticmethod
    def _time(value: float) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return parsed if math.isfinite(parsed) else None

    def _age_ms(self, observed_at_s: float) -> int | None:
        started = self._identical_since_s
        if started is None or observed_at_s < started:
            return None
        return max(0, int(math.floor((observed_at_s - started) * 1000.0)))

    def _observation(
        self,
        *,
        checked: bool,
        supported: bool = True,
        episode_started: bool = False,
        observed_at_s: float,
    ) -> FrameFreezeObservation:
        age_ms = self._age_ms(observed_at_s) if self._frozen else None
        if age_ms is not None:
            self._last_frozen_age_ms = age_ms
        return FrameFreezeObservation(
            checked=checked,
            supported=supported,
            frozen=self._frozen,
            frozen_age_ms=age_ms,
            episode_started=episode_started,
        )

    def _full_signature(self, frame: object) -> tuple[object, ...] | None:
        signature = _full_frame_signature(frame)
        if signature is not None:
            self._full_fingerprint_count += 1
        return signature

    def _reset_identity(
        self,
        signature: tuple[object, ...],
        observed_at_s: float,
        *,
        full_signature: tuple[object, ...] | None = None,
    ) -> FrameFreezeObservation:
        self._last_signature = signature
        self._full_baseline_signature = full_signature
        self._identical_since_s = observed_at_s
        self._frozen = False
        self._last_frozen_age_ms = None
        return FrameFreezeObservation(checked=True)

    def observe(
        self,
        frame: object,
        observed_at_s: float,
    ) -> FrameFreezeObservation:
        """Observe one successful capture result."""
        now_s = self._time(observed_at_s)
        if now_s is None:
            self.reset()
            return FrameFreezeObservation(supported=False)
        if not self.enabled:
            self.reset()
            return FrameFreezeObservation(checked=False)

        previous_check_s = self._last_check_s
        if previous_check_s is not None:
            elapsed_ms = (now_s - previous_check_s) * 1000.0
            if elapsed_ms < 0.0:
                self.reset()
                return FrameFreezeObservation(supported=False)
            if elapsed_ms < self._check_interval_ms:
                return self._observation(
                    checked=False,
                    supported=self._last_signature is not None,
                    observed_at_s=now_s,
                )

        self._last_check_s = now_s
        fingerprint = _sampled_frame_fingerprint(frame)
        if fingerprint is None:
            self._last_signature = None
            self._full_baseline_signature = None
            self._identical_since_s = None
            self._frozen = False
            self._last_frozen_age_ms = None
            return FrameFreezeObservation(
                checked=True,
                supported=False,
            )
        self._fingerprint_count += 1
        if fingerprint.exact:
            self._full_fingerprint_count += 1

        signature = fingerprint.signature
        if self._last_signature != signature:
            return self._reset_identity(
                signature,
                now_s,
                full_signature=signature if fingerprint.exact else None,
            )

        age_ms = self._age_ms(now_s)
        if age_ms is None:
            return self._reset_identity(
                signature,
                now_s,
                full_signature=signature if fingerprint.exact else None,
            )

        # Capture an exact baseline only after the cheap fingerprint repeats.
        # Dynamic video normally changes the spatial grid every check and never
        # pays this full-buffer cost.
        if self._full_baseline_signature is None:
            full_signature = self._full_signature(frame)
            if full_signature is None:
                self._last_signature = None
                self._identical_since_s = None
                self._frozen = False
                return FrameFreezeObservation(
                    checked=True,
                    supported=False,
                )
            self._full_baseline_signature = full_signature
            if age_ms >= self._freeze_timeout_ms:
                # There is no earlier exact baseline to compare after a sparse
                # caller jumps directly to the timeout. One later check confirms
                # exact repetition; normal periodic capture established it much
                # earlier on the first repeated sample.
                return FrameFreezeObservation(checked=True)

        if age_ms >= self._freeze_timeout_ms or self._frozen:
            current_full_signature = (
                signature
                if fingerprint.exact
                else self._full_signature(frame)
            )
            if current_full_signature is None:
                self._last_signature = None
                self._full_baseline_signature = None
                self._identical_since_s = None
                self._frozen = False
                self._last_frozen_age_ms = None
                return FrameFreezeObservation(
                    checked=True,
                    supported=False,
                )
            if current_full_signature != self._full_baseline_signature:
                # The bounded grid collided while bytes elsewhere changed.
                # Restart from the current exact frame rather than declaring a
                # false freeze.
                return self._reset_identity(
                    signature,
                    now_s,
                    full_signature=current_full_signature,
                )

        episode_started = False
        if age_ms >= self._freeze_timeout_ms and not self._frozen:
            self._frozen = True
            self._freeze_episode_count += 1
            episode_started = True
        if self._frozen:
            self._last_frozen_age_ms = age_ms
        return FrameFreezeObservation(
            checked=True,
            frozen=self._frozen,
            frozen_age_ms=age_ms if self._frozen else None,
            episode_started=episode_started,
        )

    def snapshot(self) -> FrameFreezeDetectorSnapshot:
        return FrameFreezeDetectorSnapshot(
            fingerprint_count=self._fingerprint_count,
            freeze_episode_count=self._freeze_episode_count,
            frozen=self._frozen,
            last_frozen_age_ms=self._last_frozen_age_ms,
            full_fingerprint_count=self._full_fingerprint_count,
        )
