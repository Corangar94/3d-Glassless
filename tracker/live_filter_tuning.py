"""Apply live shared-settings smoothing to the producer pose filter."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import numbers
import time
from typing import Callable, Protocol


_UINT32_MAX = 0xFFFF_FFFF


class SettingsReaderLike(Protocol):
    def read(self) -> object | None:
        ...


class MeasurementNoiseTargetLike(Protocol):
    def set_measurement_noise(self, value: float) -> None:
        ...


@dataclass(frozen=True)
class LiveFilterTuningPolicy:
    """Polling and admission bounds for live Kalman measurement noise."""

    poll_interval_s: float = 0.10
    minimum_measurement_noise: float = 0.01
    maximum_measurement_noise: float = 1.0
    change_epsilon: float = 0.001

    def __post_init__(self) -> None:
        for name in (
            "poll_interval_s",
            "minimum_measurement_noise",
            "maximum_measurement_noise",
            "change_epsilon",
        ):
            raw = getattr(self, name)
            if isinstance(raw, bool) or not isinstance(raw, numbers.Real):
                raise ValueError(f"{name} must be a finite number")
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
            object.__setattr__(self, name, value)

        if self.poll_interval_s < 0.0:
            raise ValueError("poll_interval_s must be non-negative")
        if self.minimum_measurement_noise <= 0.0:
            raise ValueError(
                "minimum_measurement_noise must be positive"
            )
        if (
            self.maximum_measurement_noise
            < self.minimum_measurement_noise
        ):
            raise ValueError(
                "maximum_measurement_noise must be at least the minimum"
            )
        if self.change_epsilon < 0.0:
            raise ValueError("change_epsilon must be non-negative")


@dataclass(frozen=True)
class LiveFilterTuningSnapshot:
    # Preserve the original public positional field order. New diagnostics are
    # appended with defaults so older direct construction remains compatible.
    poll_count: int
    skipped_poll_count: int
    unavailable_count: int
    invalid_value_count: int
    unchanged_count: int
    applied_count: int
    read_error_count: int
    apply_error_count: int
    clock_error_count: int
    clock_reset_count: int
    close_error_count: int
    last_applied_measurement_noise: float | None
    last_poll_s: float | None
    closed: bool
    last_error: str
    version_fast_path_count: int = 0
    unchanged_version_count: int = 0
    invalid_version_sample_count: int = 0
    last_seen_settings_version: int | None = None


@dataclass(frozen=True)
class _VersionedMeasurementNoise:
    version: int
    value: float


_MISSING = object()
_INVALID = object()


class LiveFilterTuningController:
    """Poll ``G3D_Settings`` and tune one filter without disrupting tracking.

    Readers may additionally provide ``read_smoothing_alpha()`` returning
    ``(committed_version, smoothing_alpha)``. That fast path reads only the
    seqlock marker and one float, so an unchanged settings version avoids two
    full-struct copies, unpacking, validation, and dataclass construction.

    The controller remains independent from the Windows shared-memory
    implementation. Any optional reader or setter failure leaves the filter on
    its last valid measurement-noise value.
    """

    def __init__(
        self,
        reader: SettingsReaderLike,
        target: MeasurementNoiseTargetLike,
        policy: LiveFilterTuningPolicy = LiveFilterTuningPolicy(),
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(getattr(reader, "read", None)):
            raise TypeError("live settings reader must provide read()")
        if not callable(getattr(target, "set_measurement_noise", None)):
            raise TypeError(
                "live filter target must provide set_measurement_noise()"
            )
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._reader = reader
        self._target = target
        self._policy = policy
        self._clock = clock
        self._last_poll_s: float | None = None
        self._last_seen_settings_version: int | None = None
        self._last_applied_measurement_noise: float | None = None
        self._poll_count = 0
        self._skipped_poll_count = 0
        self._unavailable_count = 0
        self._invalid_value_count = 0
        self._unchanged_count = 0
        self._applied_count = 0
        self._read_error_count = 0
        self._apply_error_count = 0
        self._clock_error_count = 0
        self._clock_reset_count = 0
        self._close_error_count = 0
        self._version_fast_path_count = 0
        self._unchanged_version_count = 0
        self._invalid_version_sample_count = 0
        self._closed = False
        self._last_error = ""

    @property
    def policy(self) -> LiveFilterTuningPolicy:
        return self._policy

    @staticmethod
    def _finite_time(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            return None
        parsed = float(value)
        if not math.isfinite(parsed) or parsed < 0.0:
            return None
        return parsed

    @staticmethod
    def _settings_value(settings: object) -> object:
        if isinstance(settings, Mapping):
            return settings.get("smoothing_alpha", _MISSING)
        return getattr(settings, "smoothing_alpha", _MISSING)

    def _measurement_noise_value(self, raw: object) -> float | None:
        if (
            raw is _MISSING
            or isinstance(raw, bool)
            or not isinstance(raw, numbers.Real)
        ):
            return None
        value = float(raw)
        if not math.isfinite(value):
            return None
        if not (
            self._policy.minimum_measurement_noise
            <= value
            <= self._policy.maximum_measurement_noise
        ):
            return None
        return value

    def _measurement_noise(self, settings: object) -> float | None:
        return self._measurement_noise_value(
            self._settings_value(settings)
        )

    @staticmethod
    def _versioned_measurement_noise(
        sample: object,
    ) -> _VersionedMeasurementNoise | None:
        if not isinstance(sample, (tuple, list)) or len(sample) != 2:
            return None
        version, value = sample
        if (
            isinstance(version, bool)
            or not isinstance(version, numbers.Integral)
        ):
            return None
        parsed_version = int(version)
        if (
            not 0 <= parsed_version <= _UINT32_MAX
            or parsed_version & 1
            or isinstance(value, bool)
            or not isinstance(value, numbers.Real)
        ):
            return None
        parsed_value = float(value)
        if not math.isfinite(parsed_value):
            return None
        return _VersionedMeasurementNoise(
            version=parsed_version,
            value=parsed_value,
        )

    def _poll_time(self, now_s: object | None) -> float | None:
        if now_s is not None:
            parsed = self._finite_time(now_s)
            if parsed is None:
                self._last_error = "live smoothing received an invalid clock"
            return parsed
        try:
            parsed = self._finite_time(self._clock())
        except Exception as error:
            self._last_error = (
                f"live smoothing clock failed: {type(error).__name__}"
            )
            return None
        if parsed is None:
            self._last_error = "live smoothing received an invalid clock"
        return parsed

    def _read_measurement_noise(
        self,
    ) -> tuple[float | None, int | None, object]:
        fast_read = getattr(self._reader, "read_smoothing_alpha", None)
        if callable(fast_read):
            self._version_fast_path_count += 1
            sample = fast_read()
            if sample is None:
                return None, None, None
            parsed = self._versioned_measurement_noise(sample)
            if parsed is None:
                self._invalid_version_sample_count += 1
                return None, None, _INVALID
            if parsed.version == self._last_seen_settings_version:
                self._unchanged_version_count += 1
                return None, parsed.version, _MISSING
            value = self._measurement_noise_value(parsed.value)
            if value is None:
                return None, parsed.version, _INVALID
            return value, parsed.version, parsed

        settings = self._reader.read()
        if settings is None:
            return None, None, None
        value = self._measurement_noise(settings)
        if value is None:
            return None, None, _INVALID
        return value, None, settings

    def poll(self, now_s: object | None = None) -> bool:
        """Apply one admitted change and return whether the target changed."""
        if self._closed:
            self._skipped_poll_count += 1
            return False

        timestamp_s = self._poll_time(now_s)
        if timestamp_s is None:
            self._clock_error_count += 1
            return False

        if (
            self._last_poll_s is not None
            and timestamp_s < self._last_poll_s
        ):
            # A substituted or test clock can move backwards. Start a fresh poll
            # window instead of suppressing updates until the old time catches up.
            self._last_poll_s = None
            self._clock_reset_count += 1

        if (
            self._last_poll_s is not None
            and timestamp_s - self._last_poll_s
            < self._policy.poll_interval_s
        ):
            self._skipped_poll_count += 1
            return False

        # Consume the interval before touching an optional process boundary so a
        # failing reader cannot be hammered at camera frame rate.
        self._last_poll_s = timestamp_s
        self._poll_count += 1
        try:
            measurement_noise, version, source = (
                self._read_measurement_noise()
            )
        except Exception as error:
            self._read_error_count += 1
            self._last_error = (
                "live smoothing settings read failed: "
                f"{type(error).__name__}"
            )
            return False

        if source is None:
            self._unavailable_count += 1
            self._last_error = ""
            return False
        if source is _MISSING:
            self._last_error = ""
            return False
        if source is _INVALID or measurement_noise is None:
            self._invalid_value_count += 1
            # A stable committed version cannot change in place. Remember an
            # invalid version so the same bad value is not revalidated at 10 Hz;
            # a writer update gets a new even version and is checked normally.
            if version is not None:
                self._last_seen_settings_version = version
            self._last_error = "live smoothing value is invalid or out of range"
            return False

        previous = self._last_applied_measurement_noise
        if (
            previous is not None
            and abs(measurement_noise - previous)
            <= self._policy.change_epsilon
        ):
            if version is not None:
                self._last_seen_settings_version = version
            self._unchanged_count += 1
            self._last_error = ""
            return False

        try:
            self._target.set_measurement_noise(measurement_noise)
        except Exception as error:
            self._apply_error_count += 1
            self._last_error = (
                "live smoothing filter update failed: "
                f"{type(error).__name__}"
            )
            # Do not commit the version. The same settings snapshot can retry at
            # the next bounded poll after a transient target failure.
            return False

        self._last_applied_measurement_noise = measurement_noise
        if version is not None:
            self._last_seen_settings_version = version
        self._applied_count += 1
        self._last_error = ""
        return True

    def close(self) -> bool:
        """Close the owned reader once; optional cleanup failures stay contained."""
        if self._closed:
            return True
        self._closed = True
        close = getattr(self._reader, "close", None)
        if not callable(close):
            return True
        try:
            close()
        except Exception as error:
            self._close_error_count += 1
            self._last_error = (
                "live smoothing settings close failed: "
                f"{type(error).__name__}"
            )
            return False
        return True

    def snapshot(self) -> LiveFilterTuningSnapshot:
        return LiveFilterTuningSnapshot(
            poll_count=self._poll_count,
            skipped_poll_count=self._skipped_poll_count,
            unavailable_count=self._unavailable_count,
            invalid_value_count=self._invalid_value_count,
            unchanged_count=self._unchanged_count,
            applied_count=self._applied_count,
            read_error_count=self._read_error_count,
            apply_error_count=self._apply_error_count,
            clock_error_count=self._clock_error_count,
            clock_reset_count=self._clock_reset_count,
            close_error_count=self._close_error_count,
            last_applied_measurement_noise=(
                self._last_applied_measurement_noise
            ),
            last_poll_s=self._last_poll_s,
            closed=self._closed,
            last_error=self._last_error,
            version_fast_path_count=self._version_fast_path_count,
            unchanged_version_count=self._unchanged_version_count,
            invalid_version_sample_count=(
                self._invalid_version_sample_count
            ),
            last_seen_settings_version=(
                self._last_seen_settings_version
            ),
        )
