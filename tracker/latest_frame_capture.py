"""Latest-only camera acquisition for low-latency tracking.

OpenCV's ``VideoCapture.read()`` grabs, decodes, and returns the next frame. A
backend may ignore the requested one-frame buffer, so performing that call only
from the processing loop can let queued camera frames accumulate whenever face
tracking is temporarily slower than the device cadence.

``LatestFrameCapture`` gives one worker thread exclusive ownership of frame
reads and retains only the newest completed result. The processing thread waits
for a new generation and receives the acquisition timestamp atomically with the
frame, while superseded frames are discarded before tracking work begins.
"""
from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable, Protocol

from tracker.pose import monotonic_ms, normalize_wire_timestamp


Clock = Callable[[], int]
LogFunction = Callable[[str], None]


class CaptureLike(Protocol):
    def read(self) -> tuple[bool, object | None]:
        ...

    def release(self) -> object:
        ...


@dataclass(frozen=True)
class LatestFrameCapturePolicy:
    """Bounded worker, consumer, and shutdown timing."""

    enabled: bool = True
    wait_timeout_ms: int = 1_000
    failure_backoff_ms: int = 20
    shutdown_timeout_ms: int = 1_000

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")
        if self.wait_timeout_ms < 1:
            raise ValueError("wait_timeout_ms must be at least one")
        if self.failure_backoff_ms < 0:
            raise ValueError("failure_backoff_ms cannot be negative")
        if self.shutdown_timeout_ms < 0:
            raise ValueError("shutdown_timeout_ms cannot be negative")

    def config_values(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "wait_timeout_ms": self.wait_timeout_ms,
            "failure_backoff_ms": self.failure_backoff_ms,
            "shutdown_timeout_ms": self.shutdown_timeout_ms,
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
    worker_alive: bool
    released: bool
    last_error: str


@dataclass(frozen=True)
class _CaptureEvent:
    generation: int
    ok: bool
    frame: object | None
    capture_timestamp_ms: int


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
            wait_timeout_ms=int(values.get("wait_timeout_ms", 1_000)),
            failure_backoff_ms=int(
                values.get("failure_backoff_ms", 20)
            ),
            shutdown_timeout_ms=int(
                values.get("shutdown_timeout_ms", 1_000)
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
    property access is serialized with reads when possible. Release first asks
    the worker to stop, then releases the device to unblock a backend read that
    did not return during the configured grace interval.
    """

    __g3d_latest_frame_capture__ = True

    def __init__(
        self,
        capture: CaptureLike,
        policy: LatestFrameCapturePolicy = LatestFrameCapturePolicy(),
        *,
        clock: Clock = monotonic_ms,
        thread_name: str = "G3D-LatestFrameCapture",
    ) -> None:
        self._capture = capture
        self._policy = policy
        self._clock = clock
        self._condition = threading.Condition()
        self._io_lock = threading.Lock()
        self._release_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._released = False
        self._underlying_released = False
        self._generation = 0
        self._delivered_generation = 0
        self._event: _CaptureEvent | None = None
        self._captured_frame_count = 0
        self._delivered_frame_count = 0
        self._superseded_frame_count = 0
        self._capture_failure_count = 0
        self._read_timeout_count = 0
        self._last_capture_timestamp_ms: int | None = None
        self._last_error = ""
        self._worker = threading.Thread(
            target=self._capture_loop,
            name=thread_name,
            daemon=True,
        )
        self._worker.start()

    @property
    def native_capture(self) -> object:
        return self._capture

    @property
    def policy(self) -> LatestFrameCapturePolicy:
        return self._policy

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

    def _publish_event(
        self,
        ok: bool,
        frame: object | None,
        timestamp_ms: int,
        *,
        error_text: str = "",
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
            )
            self._last_capture_timestamp_ms = timestamp_ms
            if ok:
                self._captured_frame_count += 1
                self._last_error = ""
            else:
                self._capture_failure_count += 1
                if error_text:
                    self._last_error = error_text
            self._condition.notify_all()

    def _capture_loop(self) -> None:
        while not self._stop_event.is_set():
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

            timestamp_ms = normalize_wire_timestamp(self._clock())
            if self._stop_event.is_set():
                break
            self._publish_event(
                ok,
                frame,
                timestamp_ms,
                error_text=error_text,
            )
            if not ok and self._policy.failure_backoff_ms > 0:
                self._stop_event.wait(
                    self._policy.failure_backoff_ms / 1000.0
                )

        with self._condition:
            self._condition.notify_all()

    def read_with_timestamp(
        self,
    ) -> tuple[bool, object | None, int]:
        """Return the newest event newer than the previous consumer read."""
        timeout_s = self._policy.wait_timeout_ms / 1000.0
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while True:
                if self._released:
                    return False, None, normalize_wire_timestamp(self._clock())
                event = self._event
                if (
                    event is not None
                    and event.generation > self._delivered_generation
                ):
                    self._delivered_generation = event.generation
                    if event.ok:
                        self._delivered_frame_count += 1
                    return (
                        event.ok,
                        event.frame,
                        event.capture_timestamp_ms,
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    self._read_timeout_count += 1
                    self._last_error = "read:TimeoutError"
                    return (
                        False,
                        None,
                        normalize_wire_timestamp(self._clock()),
                    )
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
        if self._released:
            return default
        try:
            method = getattr(self._capture, name)
            with self._io_lock:
                if self._released:
                    return default
                return method(*args)
        except Exception as error:
            with self._condition:
                self._last_error = self._error_text(name, error)
            return default

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
                worker_alive=self._worker.is_alive(),
                released=self._released,
                last_error=self._last_error,
            )

    def _release_underlying(self) -> None:
        if self._underlying_released:
            return
        self._underlying_released = True
        try:
            self._capture.release()
        except Exception as error:
            with self._condition:
                self._last_error = self._error_text("release", error)

    def release(self) -> None:
        with self._release_lock:
            if self._released:
                return
            self._released = True
            self._stop_event.set()
            with self._condition:
                self._condition.notify_all()

            timeout_s = self._policy.shutdown_timeout_ms / 1000.0
            grace_s = min(0.100, timeout_s * 0.5)
            if grace_s > 0.0:
                self._worker.join(grace_s)

            # Release is also the escape hatch for a backend read blocked inside
            # native code. It is intentionally outside ``_io_lock`` in that case.
            self._release_underlying()
            remaining_s = max(0.0, timeout_s - grace_s)
            if self._worker.is_alive() and remaining_s > 0.0:
                self._worker.join(remaining_s)
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
