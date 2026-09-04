"""Coalesce imperceptible live auto-tune changes before shared publication."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
import numbers
import threading
import time
from typing import Any, Callable, Iterator


@dataclass(frozen=True)
class AutoTunePublicationPolicy:
    """Minimum visible changes plus a bounded eventual-convergence interval."""

    minimum_head_distance_change_cm: float = 0.25
    minimum_smoothing_change: float = 0.005
    minimum_deadzone_change_mm: float = 0.10
    maximum_silence_s: float = 2.0

    def __post_init__(self) -> None:
        for name, maximum in (
            ("minimum_head_distance_change_cm", 1000.0),
            ("minimum_smoothing_change", 1.0),
            ("minimum_deadzone_change_mm", 1000.0),
            ("maximum_silence_s", 60.0),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, numbers.Real):
                raise ValueError(f"{name} must be a finite non-negative number")
            parsed = float(value)
            if not math.isfinite(parsed) or not 0.0 <= parsed <= maximum:
                raise ValueError(
                    f"{name} must be between 0 and {maximum:g}"
                )
            object.__setattr__(self, name, parsed)


@dataclass(frozen=True)
class AutoTunePublicationValues:
    head_dist_cm: float
    smoothing_alpha: float
    deadzone_mm: float

    @classmethod
    def from_settings(
        cls,
        settings: object,
    ) -> "AutoTunePublicationValues | None":
        try:
            raw = (
                getattr(settings, "head_dist_cm"),
                getattr(settings, "smoothing_alpha"),
                getattr(settings, "deadzone_mm"),
            )
        except Exception:
            return None
        if any(isinstance(value, bool) for value in raw):
            return None
        try:
            values = tuple(float(value) for value in raw)
        except (TypeError, ValueError, OverflowError):
            return None
        if not all(math.isfinite(value) for value in values):
            return None
        return cls(*values)


@dataclass(frozen=True)
class AutoTunePublicationDecision:
    publish: bool
    reason: str
    forced: bool = False
    fail_open: bool = False


@dataclass(frozen=True)
class AutoTunePublicationSnapshot:
    published_count: int
    suppressed_count: int
    forced_count: int
    fail_open_count: int
    external_seed_count: int
    reset_count: int
    last_published_at_s: float | None
    last_values: AutoTunePublicationValues | None
    last_decision_reason: str


def _finite_timestamp(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed) or parsed < 0.0:
        return None
    return parsed


class AutoTunePublicationGate:
    """Decide when a tuned value change deserves a shared-settings write."""

    def __init__(
        self,
        policy: AutoTunePublicationPolicy = AutoTunePublicationPolicy(),
    ) -> None:
        self._policy = policy
        self._lock = threading.RLock()
        self._last_values: AutoTunePublicationValues | None = None
        self._last_published_at_s: float | None = None
        self._published_count = 0
        self._suppressed_count = 0
        self._forced_count = 0
        self._fail_open_count = 0
        self._external_seed_count = 0
        self._reset_count = 0
        self._last_decision_reason = ""

    @property
    def policy(self) -> AutoTunePublicationPolicy:
        return self._policy

    def decide(
        self,
        settings: object,
        timestamp_s: object,
    ) -> AutoTunePublicationDecision:
        values = AutoTunePublicationValues.from_settings(settings)
        now_s = _finite_timestamp(timestamp_s)
        with self._lock:
            if values is None or now_s is None:
                return AutoTunePublicationDecision(
                    publish=True,
                    reason="invalid candidate or clock; publish fail-open",
                    fail_open=True,
                )
            previous = self._last_values
            previous_time = self._last_published_at_s
            if previous is None or previous_time is None:
                return AutoTunePublicationDecision(
                    publish=True,
                    reason="first value after publication reset",
                )
            if values == previous:
                return AutoTunePublicationDecision(
                    publish=False,
                    reason="tuned values unchanged",
                )
            if now_s < previous_time:
                return AutoTunePublicationDecision(
                    publish=True,
                    reason="launcher monotonic clock moved backward; re-anchor",
                    fail_open=True,
                )

            policy = self._policy
            if (
                abs(values.head_dist_cm - previous.head_dist_cm)
                >= policy.minimum_head_distance_change_cm
                or abs(values.smoothing_alpha - previous.smoothing_alpha)
                >= policy.minimum_smoothing_change
                or abs(values.deadzone_mm - previous.deadzone_mm)
                >= policy.minimum_deadzone_change_mm
            ):
                return AutoTunePublicationDecision(
                    publish=True,
                    reason="tuned value crossed publication threshold",
                )
            if now_s - previous_time >= policy.maximum_silence_s:
                return AutoTunePublicationDecision(
                    publish=True,
                    reason="bounded silence expired; force convergence",
                    forced=True,
                )
            return AutoTunePublicationDecision(
                publish=False,
                reason="tuned drift below publication thresholds",
            )

    def record_published(
        self,
        settings: object,
        timestamp_s: object,
        decision: AutoTunePublicationDecision,
    ) -> None:
        values = AutoTunePublicationValues.from_settings(settings)
        now_s = _finite_timestamp(timestamp_s)
        with self._lock:
            self._published_count += 1
            self._forced_count += int(decision.forced)
            self._fail_open_count += int(decision.fail_open)
            self._last_decision_reason = decision.reason
            if values is None or now_s is None:
                # Do not establish a corrupt baseline. The next valid candidate
                # is published immediately rather than compared with bad data.
                self._last_values = None
                self._last_published_at_s = None
                return
            self._last_values = values
            self._last_published_at_s = now_s

    def record_suppressed(
        self,
        decision: AutoTunePublicationDecision,
    ) -> None:
        with self._lock:
            self._suppressed_count += 1
            self._last_decision_reason = decision.reason

    def seed(
        self,
        settings: object,
        timestamp_s: object,
    ) -> bool:
        """Align the baseline after a non-auto-tune settings publication."""
        values = AutoTunePublicationValues.from_settings(settings)
        now_s = _finite_timestamp(timestamp_s)
        with self._lock:
            self._last_decision_reason = "external settings publication"
            if values is None or now_s is None:
                self._last_values = None
                self._last_published_at_s = None
                return False
            self._last_values = values
            self._last_published_at_s = now_s
            self._external_seed_count += 1
            return True

    def reset(self) -> None:
        """Start a new publication episode while retaining lifetime counters."""
        with self._lock:
            self._last_values = None
            self._last_published_at_s = None
            self._last_decision_reason = "publication episode reset"
            self._reset_count += 1

    def snapshot(self) -> AutoTunePublicationSnapshot:
        with self._lock:
            return AutoTunePublicationSnapshot(
                published_count=self._published_count,
                suppressed_count=self._suppressed_count,
                forced_count=self._forced_count,
                fail_open_count=self._fail_open_count,
                external_seed_count=self._external_seed_count,
                reset_count=self._reset_count,
                last_published_at_s=self._last_published_at_s,
                last_values=self._last_values,
                last_decision_reason=self._last_decision_reason,
            )


class AutoTunePublicationWriter:
    """Pass manual writes through and gate only explicitly armed auto-tune writes."""

    def __init__(
        self,
        delegate: object,
        *,
        gate: AutoTunePublicationGate | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(getattr(delegate, "write", None)):
            raise TypeError("settings writer must expose write(settings)")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._delegate = delegate
        self._gate = gate or AutoTunePublicationGate()
        self._clock = clock
        self._local = threading.local()
        # Keep decision, publication, and baseline commit atomic relative to
        # manual writes arriving from another thread.
        self._write_lock = threading.RLock()

    @property
    def delegate(self) -> object:
        return self._delegate

    @property
    def gate(self) -> AutoTunePublicationGate:
        return self._gate

    def _now(self) -> float | None:
        try:
            return _finite_timestamp(self._clock())
        except Exception:
            return None

    def _armed(self) -> bool:
        return int(getattr(self._local, "auto_tune_depth", 0)) > 0

    @contextmanager
    def auto_tune_write(self) -> Iterator[None]:
        depth = int(getattr(self._local, "auto_tune_depth", 0))
        self._local.auto_tune_depth = depth + 1
        try:
            yield
        finally:
            remaining = int(getattr(self._local, "auto_tune_depth", 1)) - 1
            if remaining > 0:
                self._local.auto_tune_depth = remaining
            else:
                try:
                    del self._local.auto_tune_depth
                except AttributeError:
                    pass

    def seed(self, settings: object) -> bool:
        with self._write_lock:
            return self._gate.seed(settings, self._now())

    def reset_publication(self) -> None:
        with self._write_lock:
            self._gate.reset()

    def publication_snapshot(self) -> AutoTunePublicationSnapshot:
        return self._gate.snapshot()

    def write(self, settings: object) -> Any:
        with self._write_lock:
            now_s = self._now()
            if not self._armed():
                result = self._delegate.write(settings)
                self._gate.seed(settings, now_s)
                return result

            decision = self._gate.decide(settings, now_s)
            if not decision.publish:
                self._gate.record_suppressed(decision)
                return None
            result = self._delegate.write(settings)
            self._gate.record_published(settings, now_s, decision)
            return result

    def close(self) -> Any:
        close = getattr(self._delegate, "close")
        return close()

    def __enter__(self) -> "AutoTunePublicationWriter":
        enter = getattr(self._delegate, "__enter__", None)
        if callable(enter):
            enter()
        return self

    def __exit__(self, *args: object) -> Any:
        exit_method = getattr(self._delegate, "__exit__", None)
        if callable(exit_method):
            return exit_method(*args)
        self.close()
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)
