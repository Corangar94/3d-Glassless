"""Runtime MediaPipe-to-OpenCV failover for the automatic tracker mode."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from tracker.async_inference_watchdog import AsyncInferenceFailure
from tracker.pose import elapsed_u32_ms, normalize_wire_timestamp


TrackerFactory = Callable[[], object]
LogFunction = Callable[[str], None]


@dataclass(frozen=True)
class BackendFailoverPolicy:
    """Bound automatic recovery without allowing backend flapping."""

    retry_primary_after_ms: int = 30_000
    max_primary_retries: int = 1

    def __post_init__(self) -> None:
        if self.retry_primary_after_ms < 0:
            raise ValueError("retry_primary_after_ms cannot be negative")
        if self.max_primary_retries < 0:
            raise ValueError("max_primary_retries cannot be negative")


@dataclass(frozen=True)
class BackendFailoverSnapshot:
    active_backend: str
    failover_count: int
    primary_retry_attempts: int
    primary_candidate_active: bool
    last_failure: str
    retry_in_ms: int | None


class AutoFailoverFaceTracker:
    """Prefer MediaPipe, degrade locally, and probe one bounded recovery.

    Only ``AsyncInferenceFailure`` switches an already-running primary backend.
    Other exceptions still surface as implementation defects. A shadow recovery
    candidate is optional work, so any exception from that candidate merely
    abandons the probe while the OpenCV fallback keeps producing poses.
    """

    def __init__(
        self,
        *,
        primary_factory: TrackerFactory,
        fallback_factory: TrackerFactory,
        policy: BackendFailoverPolicy = BackendFailoverPolicy(),
        logger: LogFunction = print,
        primary_failure_types: tuple[type[BaseException], ...] = (
            AsyncInferenceFailure,
        ),
    ) -> None:
        if not primary_failure_types:
            raise ValueError("primary_failure_types cannot be empty")
        self._primary_factory = primary_factory
        self._fallback_factory = fallback_factory
        self._policy = policy
        self._logger = logger
        self._primary_failure_types = primary_failure_types
        self._active: object
        self._active_backend = ""
        self._primary_candidate: object | None = None
        self._primary_failed_at_ms: int | None = None
        self._primary_retry_attempts = 0
        self._failover_count = 0
        self._last_failure = ""
        self._calibration: dict[str, float] = {}
        self._closed = False

        try:
            self._active = self._primary_factory()
        except Exception as error:
            self._record_initial_primary_failure(error)
            self._active = self._new_fallback()
            self._active_backend = "cv2"
            self._log(
                "MediaPipe unavailable at startup; using OpenCV fallback "
                f"({self._last_failure})"
            )
        else:
            self._active_backend = "mediapipe"

    @property
    def active_backend(self) -> str:
        return self._active_backend

    @property
    def policy(self) -> BackendFailoverPolicy:
        return self._policy

    @staticmethod
    def _error_text(error: BaseException) -> str:
        detail = str(error).strip()
        return (
            f"{type(error).__name__}: {detail}"
            if detail
            else type(error).__name__
        )

    def _log(self, message: str) -> None:
        self._logger(f"[G3D] Tracker backend: {message}")

    @staticmethod
    def _process(
        tracker: object,
        frame: object,
        capture_timestamp_ms: int,
    ) -> Any:
        process_frame = getattr(tracker, "process_frame", None)
        if not callable(process_frame):
            raise TypeError("tracker.process_frame must be callable")
        return process_frame(
            frame,
            capture_timestamp_ms=capture_timestamp_ms,
        )

    def _apply_calibration(self, tracker: object) -> None:
        if not self._calibration:
            return
        set_calibration = getattr(tracker, "set_calibration", None)
        if callable(set_calibration):
            set_calibration(**self._calibration)

    def _new_fallback(self) -> object:
        tracker = self._fallback_factory()
        self._apply_calibration(tracker)
        return tracker

    def _new_primary_candidate(self) -> object:
        tracker = self._primary_factory()
        self._apply_calibration(tracker)
        return tracker

    def _safe_close(self, tracker: object | None) -> None:
        if tracker is None:
            return
        close = getattr(tracker, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception as error:
            self._log(
                "contained backend cleanup failure "
                f"({self._error_text(error)})"
            )

    def _record_initial_primary_failure(self, error: BaseException) -> None:
        self._failover_count += 1
        self._last_failure = self._error_text(error)
        # No wire timestamp exists until the first camera frame. The retry delay
        # starts on that first fallback frame rather than from an unrelated clock.
        self._primary_failed_at_ms = None

    def _switch_to_fallback(
        self,
        error: BaseException,
        capture_timestamp_ms: int,
    ) -> None:
        fallback = self._new_fallback()
        failed_primary = self._active
        failed_candidate = self._primary_candidate
        self._primary_candidate = None
        self._active = fallback
        self._active_backend = "cv2"
        self._primary_failed_at_ms = normalize_wire_timestamp(
            capture_timestamp_ms
        )
        self._failover_count += 1
        self._last_failure = self._error_text(error)
        self._safe_close(failed_candidate)
        self._safe_close(failed_primary)
        self._log(
            "degraded from MediaPipe to OpenCV fallback "
            f"({self._last_failure})"
        )

    def _retry_due(self, capture_timestamp_ms: int) -> bool:
        if self._primary_retry_attempts >= self._policy.max_primary_retries:
            return False
        timestamp = normalize_wire_timestamp(capture_timestamp_ms)
        if self._primary_failed_at_ms is None:
            self._primary_failed_at_ms = timestamp
        return (
            elapsed_u32_ms(timestamp, self._primary_failed_at_ms)
            >= self._policy.retry_primary_after_ms
        )

    def _discard_candidate(
        self,
        error: BaseException,
        capture_timestamp_ms: int,
    ) -> None:
        candidate = self._primary_candidate
        self._primary_candidate = None
        self._primary_failed_at_ms = normalize_wire_timestamp(
            capture_timestamp_ms
        )
        self._last_failure = self._error_text(error)
        self._safe_close(candidate)
        self._log(
            "MediaPipe shadow recovery failed; remaining on OpenCV "
            f"({self._last_failure})"
        )

    def _start_candidate(self, capture_timestamp_ms: int) -> None:
        self._primary_retry_attempts += 1
        try:
            self._primary_candidate = self._new_primary_candidate()
        except Exception as error:
            self._primary_candidate = None
            self._primary_failed_at_ms = normalize_wire_timestamp(
                capture_timestamp_ms
            )
            self._last_failure = self._error_text(error)
            self._log(
                "could not create MediaPipe shadow recovery candidate "
                f"({self._last_failure})"
            )
            return
        self._log(
            "probing MediaPipe recovery in shadow mode "
            f"({self._primary_retry_attempts}/"
            f"{self._policy.max_primary_retries})"
        )

    @staticmethod
    def _candidate_ready(candidate: object, result: Any) -> bool:
        ready_for_promotion = getattr(candidate, "ready_for_promotion", None)
        if callable(ready_for_promotion):
            return bool(ready_for_promotion())
        # Synchronous or test backends without an explicit readiness API prove
        # progress by returning a pose.
        return result is not None

    def _probe_primary(
        self,
        frame: object,
        capture_timestamp_ms: int,
    ) -> tuple[bool, Any]:
        if self._primary_candidate is None:
            if not self._retry_due(capture_timestamp_ms):
                return False, None
            self._start_candidate(capture_timestamp_ms)
        candidate = self._primary_candidate
        if candidate is None:
            return False, None

        try:
            result = self._process(
                candidate,
                frame,
                capture_timestamp_ms,
            )
            ready = self._candidate_ready(candidate, result)
        except Exception as error:
            # Shadow recovery must never destabilize a healthy fallback.
            self._discard_candidate(error, capture_timestamp_ms)
            return False, None

        if not ready:
            return False, None

        fallback = self._active
        self._active = candidate
        self._active_backend = "mediapipe"
        self._primary_candidate = None
        self._primary_failed_at_ms = None
        self._safe_close(fallback)
        self._log("MediaPipe recovery proved healthy; promoted from OpenCV")
        return True, result

    def process_frame(
        self,
        frame_bgr: object,
        capture_timestamp_ms: int | None = None,
    ) -> Any:
        if self._closed:
            raise RuntimeError("tracker backend controller is closed")
        timestamp = normalize_wire_timestamp(
            1 if capture_timestamp_ms is None else capture_timestamp_ms
        )

        if self._active_backend == "mediapipe":
            try:
                return self._process(self._active, frame_bgr, timestamp)
            except Exception as error:
                if not isinstance(error, self._primary_failure_types):
                    raise
                self._switch_to_fallback(error, timestamp)
                return self._process(self._active, frame_bgr, timestamp)

        fallback_result = self._process(self._active, frame_bgr, timestamp)
        promoted, primary_result = self._probe_primary(frame_bgr, timestamp)
        if promoted and primary_result is not None:
            return primary_result
        return fallback_result

    def set_calibration(
        self,
        *,
        real_ipd_cm: float | None = None,
        camera_fov_deg: float | None = None,
    ) -> None:
        values: dict[str, float] = {}
        if real_ipd_cm is not None:
            values["real_ipd_cm"] = float(real_ipd_cm)
        if camera_fov_deg is not None:
            values["camera_fov_deg"] = float(camera_fov_deg)
        if not values:
            return
        self._calibration.update(values)

        seen: set[int] = set()
        for tracker in (self._active, self._primary_candidate):
            if tracker is None or id(tracker) in seen:
                continue
            seen.add(id(tracker))
            self._apply_calibration(tracker)

    def reset_session(self) -> None:
        seen: set[int] = set()
        for tracker in (self._active, self._primary_candidate):
            if tracker is None or id(tracker) in seen:
                continue
            seen.add(id(tracker))
            reset_session = getattr(tracker, "reset_session", None)
            if callable(reset_session):
                reset_session()

    def snapshot(
        self,
        capture_timestamp_ms: int | None = None,
    ) -> BackendFailoverSnapshot:
        retry_in_ms: int | None = None
        if (
            self._active_backend == "cv2"
            and self._primary_retry_attempts < self._policy.max_primary_retries
        ):
            if self._primary_failed_at_ms is None or capture_timestamp_ms is None:
                retry_in_ms = self._policy.retry_primary_after_ms
            else:
                elapsed = elapsed_u32_ms(
                    normalize_wire_timestamp(capture_timestamp_ms),
                    self._primary_failed_at_ms,
                )
                retry_in_ms = max(
                    0,
                    self._policy.retry_primary_after_ms - elapsed,
                )
        return BackendFailoverSnapshot(
            active_backend=self._active_backend,
            failover_count=self._failover_count,
            primary_retry_attempts=self._primary_retry_attempts,
            primary_candidate_active=self._primary_candidate is not None,
            last_failure=self._last_failure,
            retry_in_ms=retry_in_ms,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        seen: set[int] = set()
        for tracker in (self._active, self._primary_candidate):
            if tracker is None or id(tracker) in seen:
                continue
            seen.add(id(tracker))
            self._safe_close(tracker)
        self._primary_candidate = None

    def __enter__(self) -> "AutoFailoverFaceTracker":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
