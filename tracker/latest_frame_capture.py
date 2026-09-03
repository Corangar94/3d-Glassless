"""Latest-only camera acquisition for low-latency tracking.

OpenCV's ``VideoCapture.read()`` grabs, decodes, and returns the next frame. A
backend may ignore the requested one-frame buffer, so performing that call only
from the processing loop can let queued camera frames accumulate whenever face
tracking is temporarily slower than the device cadence.

``LatestFrameCapture`` gives one worker thread exclusive ownership of frame
reads and retains only the newest completed result. The processing thread waits
for a new generation and receives the acquisition timestamp atomically with the
frame, while superseded, stale, or persistently frozen frames are discarded
before tracking work begins.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import numbers
import threading
import time
from typing import Callable, Protocol

from tracker.frame_freeze_detector import (
    FrameFreezeDetector,
    FrameFreezeObservation,
)
from tracker.pose import monotonic_ms, normalize_wire_timestamp


Clock = Callable[[], int]
SteadyClock = Callable[[], float]
LogFunction = Callable[[str], None]
ThreadFactory = Callable[..., threading.Thread]
_MAX_WAIT_TIMEOUT_MS = 60_000
_MAX_FRAME_AGE_MS = 60_000
_MAX_FREEZE_CHECK_INTERVAL_MS = 60_000
_MAX_FREEZE_TIMEOUT_MS = 60_000
_MAX_FAILURE_BACKOFF_MS = 10_000
_MAX_SHUTDOWN_TIMEOUT_MS = 60_000
_MAX_RELEASE_LOCK_WAIT_S = 0.250


class CaptureLike(Protocol):
    def read(self) -> tuple[bool, object | None]:
        ...

    def release(self) -> object:
        ...


@dataclass(frozen=True)
class LatestFrameCapturePolicy:
    """Bounded worker, freshness, freeze, consumer, and shutdown timing."""

    enabled: bool = True
    wait_timeout_ms: int = 1_000
    failure_backoff_ms: int = 20
    shutdown_timeout_ms: int = 1_000
    # Appended fields preserve the historical positional arguments above.
    max_frame_age_ms: int = 250
    freeze_check_interval_ms: int = 250
    freeze_timeout_ms: int = 3_000

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")
        for name, value, minimum, maximum in (
            ("wait_timeout_ms", self.wait_timeout_ms, 1, _MAX_WAIT_TIMEOUT_MS),
            (
                "max_frame_age_ms",
                self.max_frame_age_ms,
                0,
                _MAX_FRAME_AGE_MS,
            ),
            (
                "freeze_check_interval_ms",
                self.freeze_check_interval_ms,
                0,
                _MAX_FREEZE_CHECK_INTERVAL_MS,
            ),
            (
                "freeze_timeout_ms",
                self.freeze_timeout_ms,
                0,
                _MAX_FREEZE_TIMEOUT_MS,
            ),
            (
                "failure_backoff_ms",
                self.failure_backoff_ms,
                0,
                _MAX_FAILURE_BACKOFF_MS,
            ),
            (
                "shutdown_timeout_ms",
                self.shutdown_timeout_ms,
                0,
                _MAX_SHUTDOWN_TIMEOUT_MS,
            ),
        ):
            if isinstance(value, bool) or not isinstance(
                value,
                numbers.Integral,
            ):
                raise ValueError(f"{name} must be an integer")
            if not minimum <= int(value) <= maximum:
                raise ValueError(
                    f"{name} must be between {minimum} and {maximum}"
                )

    def config_values(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "wait_timeout_ms": int(self.wait_timeout_ms),
            "max_frame_age_ms": int(self.max_frame_age_ms),
            "freeze_check_interval_ms": int(
                self.freeze_check_interval_ms
            ),
            "freeze_timeout_ms": int(self.freeze_timeout_ms),
            "failure_backoff_ms": int(self.failure_backoff_ms),
            "shutdown_timeout_ms": int(self.shutdown_timeout_ms),
        }


@dataclass(frozen=True)
class LatestFrameCaptureSnapshot:
    captured_frame_count: int
    delivered_frame_count: int
    superseded_frame_count: int
    capture_failure_count: int
    read_timeout_count: int
    latest_generation: int
    delivered_generation: int
    last_capture_timestamp_ms: int | None
    last_delivered_capture_timestamp_ms: int | None
    worker_alive: bool
    released: bool
    last_error: str
    stale_frame_drop_count: int = 0
    last_stale_frame_age_ms: int | None = None
    worker_failure_count: int = 0
    worker_failed: bool = False
    frozen_frame_failure_count: int = 0
    freeze_episode_count: int = 0
    last_frozen_frame_age_ms: int | None = None


@dataclass(frozen=True)
class _CaptureEvent:
    generation: int
    ok: bool
    frame: object | None
    capture_timestamp_ms: int
    completed_at_s: float


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0"}:
            return False
    raise ValueError("enabled must be a boolean")


def _parse_integer(value: object, field_name: str) -> int:
    """Parse an explicit base-10 integer without truncation or bool coercion."""
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{field_name} must be an integer")
        try:
            return int(text, 10)
        except ValueError as error:
            raise ValueError(
                f"{field_name} must be an integer"
            ) from error
    raise ValueError(f"{field_name} must be an integer")


def parse_latest_frame_capture_policy(
    camera_config: object,
    *,
    logger: LogFunction = print,
) -> LatestFrameCapturePolicy:
    """Parse ``camera.latest_frame`` atomically or use safe defaults."""
    camera = camera_config if isinstance(camera_config, dict) else {}
    raw = camera.get("latest_frame", {})
    values = raw if isinstance(raw, dict) else None
    try:
        if values is None:
            raise ValueError("camera.latest_frame must be a mapping")
        return LatestFrameCapturePolicy(
            enabled=_parse_bool(values.get("enabled", True)),
            wait_timeout_ms=_parse_integer(
                values.get("wait_timeout_ms", 1_000),
                "wait_timeout_ms",
            ),
            failure_backoff_ms=_parse_integer(
                values.get("failure_backoff_ms", 20),
                "failure_backoff_ms",
            ),
            shutdown_timeout_ms=_parse_integer(
                values.get("shutdown_timeout_ms", 1_000),
                "shutdown_timeout_ms",
            ),
            max_frame_age_ms=_parse_integer(
                values.get("max_frame_age_ms", 250),
                "max_frame_age_ms",
            ),
            freeze_check_interval_ms=_parse_integer(
                values.get("freeze_check_interval_ms", 250),
                "freeze_check_interval_ms",
            ),
            freeze_timeout_ms=_parse_integer(
                values.get("freeze_timeout_ms", 3_000),
                "freeze_timeout_ms",
            ),
        )
    except (TypeError, ValueError, OverflowError):
        logger(
            "[G3D] Invalid latest-frame camera settings; "
            "using safe defaults"
        )
        return LatestFrameCapturePolicy()


def _normalized_read_result(
    result: object,
) -> tuple[bool, object | None]:
    if not isinstance(result, tuple) or len(result) != 2:
        raise TypeError("camera read returned a malformed result")
    ok = bool(result[0])
    frame = result[1]
    if not ok:
        return False, None
    if frame is None:
        raise ValueError("camera read reported success with no frame")
    return True, frame


class LatestFrameCapture:
    """Single-consumer latest-frame proxy around an opened capture object.

    The worker is the only caller of the underlying ``read`` method. Control
    property access is serialized with reads and receives priority between
    frames. Release first asks the worker to stop, then releases the device to
    unblock a backend read that did not return during the configured grace
    interval.

    Capture ownership transfers only after the worker starts successfully. A
    half-constructed wrapper therefore cannot release a raw capture that the
    runtime has retained for synchronous fallback.
    """

    __g3d_latest_frame_capture__ = True

    def __init__(
        self,
        capture: CaptureLike,
        policy: LatestFrameCapturePolicy = LatestFrameCapturePolicy(),
        *,
        clock: Clock = monotonic_ms,
        steady_clock: SteadyClock = time.monotonic,
        thread_name: str = "G3D-LatestFrameCapture",
        thread_factory: ThreadFactory = threading.Thread,
    ) -> None:
        self._capture = capture
        self._policy = policy
        self._clock = clock
        self._steady_clock = steady_clock
        self._freeze_detector = FrameFreezeDetector(
            check_interval_ms=policy.freeze_check_interval_ms,
            freeze_timeout_ms=policy.freeze_timeout_ms,
        )
        self._condition = threading.Condition()
        self._io_lock = threading.Lock()
        self._release_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._released = False
        self._underlying_released = False
        self._owns_capture = False
        self._worker: threading.Thread | None = None
        self._worker_finished = False
        self._worker_failed = False
        self._worker_failure_count = 0
        self._control_waiters = 0
        self._generation = 0
        self._delivered_generation = 0
        self._event: _CaptureEvent | None = None
        self._captured_frame_count = 0
        self._delivered_frame_count = 0
        self._superseded_frame_count = 0
        self._capture_failure_count = 0
        self._read_timeout_count = 0
        self._stale_frame_drop_count = 0
        self._frozen_frame_failure_count = 0
        self._freeze_episode_count = 0
        self._last_capture_timestamp_ms: int | None = None
        self._last_delivered_capture_timestamp_ms: int | None = None
        self._last_stale_frame_age_ms: int | None = None
        self._last_frozen_frame_age_ms: int | None = None
        self._last_error = ""
        worker = thread_factory(
            target=self._capture_loop,
            name=thread_name,
            daemon=True,
        )
        self._worker = worker
        worker.start()
        # ``start`` is the ownership commit point. Before it returns, the raw
        # capture still belongs to the caller and must remain available if
        # construction raises and the runtime falls back to synchronous reads.
        self._owns_capture = True

    @property
    def native_capture(self) -> object:
        return self._capture

    @property
    def policy(self) -> LatestFrameCapturePolicy:
        return self._policy

    @property
    def owns_capture(self) -> bool:
        return self._owns_capture

    @property
    def worker_failed(self) -> bool:
        with self._condition:
            return self._worker_failed

    @property
    def last_delivered_capture_timestamp_ms(self) -> int | None:
        with self._condition:
            return self._last_delivered_capture_timestamp_ms

    @property
    def failure_summary(self) -> str:
        parts: list[str] = []
        try:
            native = getattr(self._capture, "failure_summary", "")
        except Exception:
            native = ""
        if native:
            parts.append(str(native))
        with self._condition:
            if self._last_error:
                parts.append(self._last_error)
        return ", ".join(parts)

    @staticmethod
    def _error_text(stage: str, error: BaseException) -> str:
        return f"{stage}:{type(error).__name__}"

    def _safe_timestamp(self) -> int:
        try:
            return normalize_wire_timestamp(self._clock())
        except Exception:
            return normalize_wire_timestamp(
                int(time.monotonic() * 1000.0)
            )

    def _safe_steady_time(self) -> float:
        try:
            value = float(self._steady_clock())
        except Exception:
            value = float(time.monotonic())
        if not math.isfinite(value):
            value = float(time.monotonic())
        return value

    def _event_age_ms(self, event: _CaptureEvent) -> float | None:
        try:
            now_s = float(self._steady_clock())
        except Exception:
            return None
        if not math.isfinite(now_s) or not math.isfinite(event.completed_at_s):
            return None
        age_ms = (now_s - event.completed_at_s) * 1000.0
        return None if age_ms < 0.0 else age_ms

    def _discard_stale_event_locked(self, event: _CaptureEvent) -> bool:
        maximum_age_ms = self._policy.max_frame_age_ms
        if not event.ok or maximum_age_ms <= 0:
            return False
        age_ms = self._event_age_ms(event)
        if age_ms is None or age_ms <= maximum_age_ms:
            return False
        displayed_age_ms = max(1, int(math.ceil(age_ms)))
        self._stale_frame_drop_count += 1
        self._last_stale_frame_age_ms = displayed_age_ms
        self._last_error = f"read:StaleFrame({displayed_age_ms}ms)"
        return True

    def _observe_freeze(
        self,
        ok: bool,
        frame: object | None,
        completed_at_s: float,
    ) -> FrameFreezeObservation:
        if not ok or frame is None:
            self._freeze_detector.reset()
            return FrameFreezeObservation()
        try:
            return self._freeze_detector.observe(frame, completed_at_s)
        except Exception:
            # Freeze detection is an optional safety boundary. A hashing or
            # third-party buffer failure must never turn a valid frame into a
            # camera failure.
            self._freeze_detector.reset()
            return FrameFreezeObservation(supported=False)

    def _wait_for_control_calls(self) -> bool:
        with self._condition:
            while self._control_waiters > 0 and not self._stop_event.is_set():
                self._condition.wait()
            return not self._stop_event.is_set()

    def _publish_event(
        self,
        ok: bool,
        frame: object | None,
        timestamp_ms: int,
        completed_at_s: float,
        *,
        error_text: str = "",
        freeze_observation: FrameFreezeObservation | None = None,
    ) -> None:
        with self._condition:
            previous = self._event
            if (
                previous is not None
                and previous.ok
                and previous.generation > self._delivered_generation
            ):
                self._superseded_frame_count += 1
            self._generation += 1
            self._event = _CaptureEvent(
                generation=self._generation,
                ok=bool(ok),
                frame=frame if ok else None,
                capture_timestamp_ms=timestamp_ms,
                completed_at_s=completed_at_s,
            )
            self._last_capture_timestamp_ms = timestamp_ms
            if ok:
                self._captured_frame_count += 1
                self._last_error = ""
            else:
                self._capture_failure_count += 1
                self._last_error = error_text or "read:CaptureFailure"
            if freeze_observation is not None and freeze_observation.frozen:
                self._frozen_frame_failure_count += 1
                if freeze_observation.episode_started:
                    self._freeze_episode_count += 1
                self._last_frozen_frame_age_ms = (
                    freeze_observation.frozen_age_ms
                )
            self._condition.notify_all()

    def _capture_loop(self) -> None:
        worker_error: BaseException | None = None
        try:
            while not self._stop_event.is_set():
                if not self._wait_for_control_calls():
                    break
                error_text = ""
                try:
                    with self._io_lock:
                        if self._stop_event.is_set():
                            break
                        result = self._capture.read()
                    ok, frame = _normalized_read_result(result)
                except Exception as error:
                    ok, frame = False, None
                    error_text = self._error_text("read", error)

                completed_at_s = self._safe_steady_time()
                freeze_observation = self._observe_freeze(
                    ok,
                    frame,
                    completed_at_s,
                )
                if freeze_observation.frozen:
                    frozen_age_ms = (
                        freeze_observation.frozen_age_ms
                        if freeze_observation.frozen_age_ms is not None
                        else self._policy.freeze_timeout_ms
                    )
                    ok, frame = False, None
                    error_text = f"read:FrozenFrame({frozen_age_ms}ms)"
                timestamp_ms = self._safe_timestamp()
                if self._stop_event.is_set():
                    break
                self._publish_event(
                    ok,
                    frame,
                    timestamp_ms,
                    completed_at_s,
                    error_text=error_text,
                    freeze_observation=freeze_observation,
                )
                if not ok and self._policy.failure_backoff_ms > 0:
                    self._stop_event.wait(
                        self._policy.failure_backoff_ms / 1000.0
                    )
        except BaseException as error:
            # Ordinary driver failures are converted to failure events by the
            # inner Exception boundary. This outer boundary is only for a fatal
            # worker-level termination that would otherwise disappear silently.
            worker_error = error
        finally:
            with self._condition:
                self._worker_finished = True
                if not self._stop_event.is_set():
                    self._worker_failed = True
                    self._worker_failure_count += 1
                    self._last_error = (
                        self._error_text("worker", worker_error)
                        if worker_error is not None
                        else "worker:UnexpectedExit"
                    )
                self._condition.notify_all()

    def read_with_timestamp(
        self,
    ) -> tuple[bool, object | None, int]:
        """Return the newest fresh event newer than the previous consumer read."""
        timeout_s = self._policy.wait_timeout_ms / 1000.0
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while True:
                if self._released:
                    return False, None, self._safe_timestamp()
                event = self._event
                if (
                    event is not None
                    and event.generation > self._delivered_generation
                ):
                    # Retire the generation before freshness evaluation so an
                    # old successful frame cannot be reconsidered on every read.
                    self._delivered_generation = event.generation
                    if self._discard_stale_event_locked(event):
                        continue
                    self._last_delivered_capture_timestamp_ms = (
                        event.capture_timestamp_ms
                    )
                    if event.ok:
                        self._delivered_frame_count += 1
                    return (
                        event.ok,
                        event.frame,
                        event.capture_timestamp_ms,
                    )
                if self._worker_failed:
                    # Return an immediate failed read on every call. The camera
                    # loop's existing three-failure policy can then reopen the
                    # device without paying this wrapper's full timeout each time.
                    return False, None, self._safe_timestamp()
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    self._read_timeout_count += 1
                    self._last_error = "read:TimeoutError"
                    return False, None, self._safe_timestamp()
                self._condition.wait(remaining)

    def read(self) -> tuple[bool, object | None]:
        ok, frame, _timestamp_ms = self.read_with_timestamp()
        return ok, frame

    def _proxy_call(
        self,
        name: str,
        default: object,
        *args: object,
    ) -> object:
        acquired = False
        with self._condition:
            if self._released:
                return default
            self._control_waiters += 1
            self._condition.notify_all()
        try:
            method = getattr(self._capture, name)
            acquired = self._io_lock.acquire(
                timeout=self._policy.wait_timeout_ms / 1000.0
            )
            if not acquired:
                with self._condition:
                    self._last_error = f"{name}:TimeoutError"
                return default
            if self._released:
                return default
            return method(*args)
        except Exception as error:
            with self._condition:
                self._last_error = self._error_text(name, error)
            return default
        finally:
            if acquired:
                self._io_lock.release()
            with self._condition:
                self._control_waiters = max(0, self._control_waiters - 1)
                self._condition.notify_all()

    def isOpened(self) -> bool:  # OpenCV spelling is part of the API
        return bool(self._proxy_call("isOpened", False))

    def get(self, property_id: int) -> float:
        result = self._proxy_call("get", 0.0, property_id)
        try:
            return float(result)
        except (TypeError, ValueError, OverflowError):
            return 0.0

    def set(self, property_id: int, value: float) -> bool:
        return bool(self._proxy_call("set", False, property_id, value))

    def getBackendName(self) -> str:
        return str(self._proxy_call("getBackendName", ""))

    def _worker_is_alive(self) -> bool:
        worker = self._worker
        if worker is None:
            return False
        try:
            return bool(worker.is_alive())
        except Exception:
            return False

    def _join_worker(self, timeout_s: float) -> None:
        worker = self._worker
        if worker is None or worker is threading.current_thread():
            return
        try:
            # Joining a thread whose ``start`` raised is itself an error.
            if worker.ident is not None:
                worker.join(max(0.0, timeout_s))
        except (AttributeError, RuntimeError):
            return

    def snapshot(self) -> LatestFrameCaptureSnapshot:
        with self._condition:
            return LatestFrameCaptureSnapshot(
                captured_frame_count=self._captured_frame_count,
                delivered_frame_count=self._delivered_frame_count,
                superseded_frame_count=self._superseded_frame_count,
                capture_failure_count=self._capture_failure_count,
                read_timeout_count=self._read_timeout_count,
                latest_generation=self._generation,
                delivered_generation=self._delivered_generation,
                last_capture_timestamp_ms=self._last_capture_timestamp_ms,
                last_delivered_capture_timestamp_ms=(
                    self._last_delivered_capture_timestamp_ms
                ),
                worker_alive=self._worker_is_alive(),
                released=self._released,
                last_error=self._last_error,
                stale_frame_drop_count=self._stale_frame_drop_count,
                last_stale_frame_age_ms=self._last_stale_frame_age_ms,
                worker_failure_count=self._worker_failure_count,
                worker_failed=self._worker_failed,
                frozen_frame_failure_count=(
                    self._frozen_frame_failure_count
                ),
                freeze_episode_count=self._freeze_episode_count,
                last_frozen_frame_age_ms=(
                    self._last_frozen_frame_age_ms
                ),
            )

    def _release_underlying(self) -> None:
        if not self._owns_capture or self._underlying_released:
            return
        self._underlying_released = True
        self._owns_capture = False
        try:
            self._capture.release()
        except Exception as error:
            with self._condition:
                self._last_error = self._error_text("release", error)

    def _release_underlying_serialized(self, timeout_s: float) -> bool:
        """Release under the normal I/O lock, returning False on timeout."""
        if not self._owns_capture or self._underlying_released:
            return True
        acquired = False
        try:
            acquired = self._io_lock.acquire(timeout=max(0.0, timeout_s))
            if not acquired:
                return False
            self._release_underlying()
            return True
        finally:
            if acquired:
                self._io_lock.release()

    def release(self) -> None:
        with self._release_lock:
            if self._released:
                return
            self._released = True
            self._stop_event.set()
            with self._condition:
                self._condition.notify_all()

            timeout_s = self._policy.shutdown_timeout_ms / 1000.0
            deadline = time.monotonic() + timeout_s
            grace_s = min(0.100, timeout_s * 0.5)
            if grace_s > 0.0:
                self._join_worker(grace_s)

            # Normally serialize driver teardown with read/get/set/isOpened.
            # Reserve at least half of the remaining budget for the post-release
            # worker join. If a native read owns the lock beyond this short wait,
            # fall back to the intentional cross-thread release escape hatch.
            available_s = max(0.0, deadline - time.monotonic())
            release_lock_wait_s = min(
                _MAX_RELEASE_LOCK_WAIT_S,
                available_s * 0.5,
            )
            if not self._release_underlying_serialized(
                release_lock_wait_s
            ):
                self._release_underlying()

            remaining_s = max(0.0, deadline - time.monotonic())
            if self._worker_is_alive() and remaining_s > 0.0:
                self._join_worker(remaining_s)
            with self._condition:
                self._condition.notify_all()

    def __enter__(self) -> "LatestFrameCapture":
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass


def wrap_latest_frame_capture(
    capture: CaptureLike,
    policy: LatestFrameCapturePolicy,
    *,
    clock: Clock = monotonic_ms,
) -> CaptureLike:
    if not policy.enabled:
        return capture
    if getattr(capture, "__g3d_latest_frame_capture__", False):
        return capture
    return LatestFrameCapture(capture, policy, clock=clock)
