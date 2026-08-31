"""Runtime MediaPipe-to-OpenCV failover and bounded primary recovery."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from tracker.async_inference_watchdog import AsyncInferenceFailure
from tracker.backend_pose_bridge import (
    BackendPoseContinuityBridge,
    PoseContinuityPolicy,
)
from tracker.backend_transition_state import mark_backend_transition
from tracker.pose import (
    HeadPosition,
    elapsed_u32_ms,
    monotonic_ms,
    normalize_wire_timestamp,
)


TrackerFactory = Callable[[], object]
LogFunction = Callable[[str], None]


@dataclass(frozen=True)
class BackendFailoverPolicy:
    """Bound automatic recovery without allowing backend flapping."""

    retry_primary_after_ms: int = 30_000
    max_primary_retries: int = 1
    shadow_probe_interval_ms: int = 100
    shadow_probe_timeout_ms: int = 5_000
    minimum_healthy_callbacks: int = 3

    def __post_init__(self) -> None:
        if self.retry_primary_after_ms < 0:
            raise ValueError("retry_primary_after_ms cannot be negative")
        if self.max_primary_retries < 0:
            raise ValueError("max_primary_retries cannot be negative")
        if self.shadow_probe_interval_ms < 0:
            raise ValueError("shadow_probe_interval_ms cannot be negative")
        if self.shadow_probe_timeout_ms < 1:
            raise ValueError("shadow_probe_timeout_ms must be positive")
        if self.minimum_healthy_callbacks < 1:
            raise ValueError("minimum_healthy_callbacks must be at least one")


@dataclass(frozen=True)
class BackendFailoverSnapshot:
    active_backend: str
    failover_count: int
    primary_retry_attempts: int
    primary_candidate_active: bool
    last_failure: str
    retry_in_ms: int | None
    backend_transition_id: int = 0
    pose_transition_active: bool = False
    pose_transition_preserves_position: bool = False
    primary_candidate_probe_count: int = 0
    primary_candidate_age_ms: int | None = None
    primary_candidate_healthy_callbacks: int = 0


class AutoFailoverFaceTracker:
    """Prefer MediaPipe, degrade locally, and probe one bounded recovery.

    Only ``AsyncInferenceFailure`` switches an already-running primary backend.
    Other exceptions still surface as implementation defects. The OpenCV
    fallback remains the sole visible pose source until a replacement MediaPipe
    candidate demonstrates multiple healthy callbacks and an actual pose.
    """

    def __init__(
        self,
        *,
        primary_factory: TrackerFactory,
        fallback_factory: TrackerFactory,
        policy: BackendFailoverPolicy = BackendFailoverPolicy(),
        pose_continuity_policy: PoseContinuityPolicy = PoseContinuityPolicy(),
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
        self._primary_candidate_started_ms: int | None = None
        self._primary_candidate_last_probe_ms: int | None = None
        self._primary_candidate_probe_count = 0
        self._primary_failed_at_ms: int | None = None
        self._primary_retry_attempts = 0
        self._failover_count = 0
        self._last_failure = ""
        self._calibration: dict[str, float] = {}
        self._pose_bridge = BackendPoseContinuityBridge(
            pose_continuity_policy
        )
        self._backend_transition_id = 0
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
    def backend_transition_id(self) -> int:
        return self._backend_transition_id

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

    def _bridge_result(
        self,
        result: Any,
        capture_timestamp_ms: int,
    ) -> Any:
        if result is None or isinstance(result, HeadPosition):
            return self._pose_bridge.apply(result, capture_timestamp_ms)
        return result

    def _begin_backend_transition(self, capture_timestamp_ms: int) -> None:
        preserve_position = self._pose_bridge.begin_transition(
            capture_timestamp_ms
        )
        self._backend_transition_id = mark_backend_transition(
            preserve_position=preserve_position
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
        self._clear_primary_candidate_state()
        self._active = fallback
        self._active_backend = "cv2"
        self._primary_failed_at_ms = normalize_wire_timestamp(
            capture_timestamp_ms
        )
        self._failover_count += 1
        self._last_failure = self._error_text(error)
        self._begin_backend_transition(capture_timestamp_ms)
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

    def _clear_primary_candidate_state(self) -> None:
        self._primary_candidate = None
        self._primary_candidate_started_ms = None
        self._primary_candidate_last_probe_ms = None
        self._primary_candidate_probe_count = 0

    def _discard_candidate(
        self,
        error: BaseException,
        capture_timestamp_ms: int,
    ) -> None:
        candidate = self._primary_candidate
        probe_count = self._primary_candidate_probe_count
        self._last_failure = self._error_text(error)
        self._primary_failed_at_ms = normalize_wire_timestamp(
            capture_timestamp_ms
        )
        self._clear_primary_candidate_state()
        self._safe_close(candidate)
        self._log(
            "MediaPipe shadow recovery failed after "
            f"{probe_count} sampled frames; remaining on OpenCV "
            f"({self._last_failure})"
        )

    def _start_candidate(self, capture_timestamp_ms: int) -> None:
        self._primary_retry_attempts += 1
        try:
            candidate = self._new_primary_candidate()
        except Exception as error:
            self._primary_failed_at_ms = normalize_wire_timestamp(
                capture_timestamp_ms
            )
            self._last_failure = self._error_text(error)
            self._log(
                "could not create MediaPipe shadow recovery candidate "
                f"({self._last_failure})"
            )
            return
        timestamp = normalize_wire_timestamp(capture_timestamp_ms)
        self._primary_candidate = candidate
        self._primary_candidate_started_ms = timestamp
        self._primary_candidate_last_probe_ms = None
        self._primary_candidate_probe_count = 0
        cadence = (
            "every fallback frame"
            if not self._candidate_has_async_health(candidate)
            or self._policy.shadow_probe_interval_ms <= 0
            else (
                f"at <= {1000.0 / self._policy.shadow_probe_interval_ms:.1f} Hz"
            )
        )
        self._log(
            "probing MediaPipe recovery in shadow mode "
            f"{cadence}; requires "
            f"{self._policy.minimum_healthy_callbacks} healthy callbacks "
            f"plus a pose within {self._policy.shadow_probe_timeout_ms} ms"
        )

    def _candidate_age_ms(self, capture_timestamp_ms: int) -> int | None:
        if self._primary_candidate_started_ms is None:
            return None
        return elapsed_u32_ms(
            normalize_wire_timestamp(capture_timestamp_ms),
            self._primary_candidate_started_ms,
        )

    @staticmethod
    def _candidate_has_async_health(candidate: object) -> bool:
        if callable(getattr(candidate, "async_health_snapshot", None)):
            return True
        watchdog = getattr(candidate, "_async_watchdog", None)
        return callable(getattr(watchdog, "snapshot", None))

    def _candidate_probe_due(self, capture_timestamp_ms: int) -> bool:
        candidate = self._primary_candidate
        if candidate is None:
            return False
        # Preserve historical every-frame probing for generic third-party/test
        # candidates that do not expose MediaPipe-style health telemetry.
        if (
            not self._candidate_has_async_health(candidate)
            or self._policy.shadow_probe_interval_ms <= 0
        ):
            return True
        if self._primary_candidate_last_probe_ms is None:
            return True
        return (
            elapsed_u32_ms(
                normalize_wire_timestamp(capture_timestamp_ms),
                self._primary_candidate_last_probe_ms,
            )
            >= self._policy.shadow_probe_interval_ms
        )

    @staticmethod
    def _candidate_health_snapshot(candidate: object) -> object | None:
        snapshot = getattr(candidate, "async_health_snapshot", None)
        if callable(snapshot):
            return snapshot()
        watchdog = getattr(candidate, "_async_watchdog", None)
        snapshot = getattr(watchdog, "snapshot", None)
        return snapshot() if callable(snapshot) else None

    def _candidate_ready(self, candidate: object, result: Any) -> bool:
        health = self._candidate_health_snapshot(candidate)
        if health is not None and hasattr(
            health,
            "consecutive_successful_callbacks",
        ):
            return (
                result is not None
                and int(getattr(health, "consecutive_submission_errors", 0)) == 0
                and int(getattr(health, "consecutive_callback_errors", 0)) == 0
                and int(
                    getattr(
                        health,
                        "consecutive_successful_callbacks",
                        0,
                    )
                )
                >= self._policy.minimum_healthy_callbacks
            )
        ready_for_promotion = getattr(candidate, "ready_for_promotion", None)
        if callable(ready_for_promotion):
            return bool(ready_for_promotion())
        return result is not None

    def _promote_primary(
        self,
        result: Any,
        capture_timestamp_ms: int,
    ) -> tuple[bool, Any]:
        candidate = self._primary_candidate
        if candidate is None:
            return False, None
        fallback = self._active
        self._active = candidate
        self._active_backend = "mediapipe"
        self._primary_failed_at_ms = None
        self._clear_primary_candidate_state()
        self._begin_backend_transition(capture_timestamp_ms)
        self._safe_close(fallback)
        self._log("MediaPipe recovery proved healthy; promoted from OpenCV")
        return True, result

    def _probe_primary(
        self,
        frame: object,
        capture_timestamp_ms: int,
    ) -> tuple[bool, Any]:
        timestamp = normalize_wire_timestamp(capture_timestamp_ms)
        if self._primary_candidate is None:
            if not self._retry_due(timestamp):
                return False, None
            self._start_candidate(timestamp)
        candidate = self._primary_candidate
        if candidate is None:
            return False, None

        # Camera-session reset clears the candidate watchdog. Restart the probe
        # deadline on the first frame from the replacement camera as well.
        if self._primary_candidate_started_ms is None:
            self._primary_candidate_started_ms = timestamp
            self._primary_candidate_last_probe_ms = None
            self._primary_candidate_probe_count = 0

        candidate_age = self._candidate_age_ms(timestamp)
        if (
            candidate_age is not None
            and candidate_age >= self._policy.shadow_probe_timeout_ms
        ):
            self._discard_candidate(
                TimeoutError(
                    "MediaPipe shadow recovery timed out without sufficient "
                    "healthy callbacks and a usable pose"
                ),
                timestamp,
            )
            return False, None

        if not self._candidate_probe_due(timestamp):
            return False, None

        self._primary_candidate_last_probe_ms = timestamp
        self._primary_candidate_probe_count += 1
        try:
            result = self._process(candidate, frame, timestamp)
            ready = self._candidate_ready(candidate, result)
        except Exception as error:
            # Shadow recovery must never destabilize a healthy fallback.
            self._discard_candidate(error, timestamp)
            return False, None
        if not ready:
            return False, None
        return self._promote_primary(result, timestamp)

    def process_frame(
        self,
        frame_bgr: object,
        capture_timestamp_ms: int | None = None,
    ) -> Any:
        if self._closed:
            raise RuntimeError("tracker backend controller is closed")
        timestamp = (
            monotonic_ms()
            if capture_timestamp_ms is None
            else normalize_wire_timestamp(capture_timestamp_ms)
        )

        if self._active_backend == "mediapipe":
            try:
                primary_result = self._process(
                    self._active,
                    frame_bgr,
                    timestamp,
                )
                return self._bridge_result(primary_result, timestamp)
            except Exception as error:
                if not isinstance(error, self._primary_failure_types):
                    raise
                self._switch_to_fallback(error, timestamp)
                fallback_result = self._process(
                    self._active,
                    frame_bgr,
                    timestamp,
                )
                return self._bridge_result(fallback_result, timestamp)

        fallback_raw = self._process(self._active, frame_bgr, timestamp)
        fallback_result = self._bridge_result(fallback_raw, timestamp)
        promoted, primary_result = self._probe_primary(frame_bgr, timestamp)
        if promoted and primary_result is not None:
            return self._bridge_result(primary_result, timestamp)
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
        self._pose_bridge.reset()
        if self._primary_candidate is not None:
            self._primary_candidate_started_ms = None
            self._primary_candidate_last_probe_ms = None
            self._primary_candidate_probe_count = 0

    def snapshot(
        self,
        capture_timestamp_ms: int | None = None,
    ) -> BackendFailoverSnapshot:
        retry_in_ms: int | None = None
        candidate_age_ms: int | None = None
        healthy_callbacks = 0
        if (
            self._active_backend == "cv2"
            and self._primary_candidate is None
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
        if self._primary_candidate is not None:
            if capture_timestamp_ms is not None:
                candidate_age_ms = self._candidate_age_ms(
                    capture_timestamp_ms
                )
            try:
                health = self._candidate_health_snapshot(
                    self._primary_candidate
                )
            except Exception:
                health = None
            if health is not None:
                healthy_callbacks = int(
                    getattr(
                        health,
                        "consecutive_successful_callbacks",
                        0,
                    )
                )
        return BackendFailoverSnapshot(
            active_backend=self._active_backend,
            failover_count=self._failover_count,
            primary_retry_attempts=self._primary_retry_attempts,
            primary_candidate_active=self._primary_candidate is not None,
            last_failure=self._last_failure,
            retry_in_ms=retry_in_ms,
            backend_transition_id=self._backend_transition_id,
            pose_transition_active=self._pose_bridge.transition_active,
            pose_transition_preserves_position=(
                self._pose_bridge.transition_preserves_position
            ),
            primary_candidate_probe_count=(
                self._primary_candidate_probe_count
            ),
            primary_candidate_age_ms=candidate_age_ms,
            primary_candidate_healthy_callbacks=healthy_callbacks,
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
        self._clear_primary_candidate_state()
        self._pose_bridge.reset()

    def __enter__(self) -> "AutoFailoverFaceTracker":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
