"""Detect a camera that returns one byte-identical frame indefinitely."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import numbers


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


def _frame_signature(frame: object) -> tuple[object, ...] | None:
    """Return a full-buffer signature for one contiguous buffer object.

    The shape/format metadata prevents two differently shaped arrays with the
    same byte stream from being treated as one frame. Unsupported or
    non-contiguous objects deliberately return ``None`` so this optional safety
    gate never changes third-party frame compatibility.
    """
    try:
        view = memoryview(frame)
        if not view.c_contiguous:
            return None
        byte_view = (
            view
            if view.ndim == 1 and view.format == "B"
            else view.cast("B")
        )
        digest = hashlib.blake2b(byte_view, digest_size=16).digest()
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
            digest,
        )
    except Exception:
        return None


class FrameFreezeDetector:
    """Sample exact frame identity and declare a sustained freeze.

    Full-buffer hashing happens no more often than ``check_interval_ms``. Once a
    freeze is established, frames remain frozen between checks until a sampled
    frame proves that the buffer changed. All state is single-worker-thread
    owned; callers may read ``snapshot`` after externally synchronizing access.
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
        self._identical_since_s: float | None = None
        self._frozen = False
        self._fingerprint_count = 0
        self._freeze_episode_count = 0
        self._last_frozen_age_ms: int | None = None

    @property
    def enabled(self) -> bool:
        return self._freeze_timeout_ms > 0

    def reset(self) -> None:
        self._last_check_s = None
        self._last_signature = None
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
        signature = _frame_signature(frame)
        if signature is None:
            self._last_signature = None
            self._identical_since_s = None
            self._frozen = False
            self._last_frozen_age_ms = None
            return FrameFreezeObservation(
                checked=True,
                supported=False,
            )
        self._fingerprint_count += 1

        if self._last_signature != signature:
            self._last_signature = signature
            self._identical_since_s = now_s
            self._frozen = False
            self._last_frozen_age_ms = None
            return FrameFreezeObservation(checked=True)

        age_ms = self._age_ms(now_s)
        if age_ms is None:
            self._identical_since_s = now_s
            self._frozen = False
            return FrameFreezeObservation(checked=True)

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
        )
