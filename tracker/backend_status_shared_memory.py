"""Versioned tracker-backend diagnostics over Windows named shared memory."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import struct

from tracker.pose import elapsed_u32_ms, monotonic_ms, normalize_wire_timestamp


STATUS_MAPPING_NAME = "G3D_TrackerBackendV1"
STATUS_MAGIC = 0x31425447  # little-endian bytes: GTB1
STATUS_VERSION = 1
_FAILURE_BYTES = 192
_NONE_U32 = 0xFFFF_FFFF
_STATUS_FORMAT = f"<13I{_FAILURE_BYTES}s"
STATUS_SIZE = struct.calcsize(_STATUS_FORMAT)
_SEQ_SIZE = 4

_FLAG_CANDIDATE_ACTIVE = 1 << 0
_FLAG_POSE_TRANSITION_ACTIVE = 1 << 1
_FLAG_POSE_TRANSITION_PRESERVES_POSITION = 1 << 2

_CONFIGURED_CODES = {
    "unknown": 0,
    "auto": 1,
    "mediapipe": 2,
    "cv2": 3,
}
_CONFIGURED_NAMES = {value: key for key, value in _CONFIGURED_CODES.items()}
_ACTIVE_CODES = {
    "unknown": 0,
    "mediapipe": 1,
    "cv2": 2,
}
_ACTIVE_NAMES = {value: key for key, value in _ACTIVE_CODES.items()}

_PAGE_READWRITE = 0x04
_FILE_MAP_ALL_ACCESS = 0xF001F
_FILE_MAP_READ = 0x0004
_INVALID_HANDLE = ctypes.c_void_p(-1)


@dataclass(frozen=True)
class TrackerBackendStatus:
    configured_mode: str
    active_backend: str
    failover_count: int = 0
    primary_retry_attempts: int = 0
    retry_in_ms: int | None = None
    candidate_active: bool = False
    candidate_age_ms: int | None = None
    candidate_probe_count: int = 0
    candidate_healthy_callbacks: int = 0
    backend_transition_id: int = 0
    pose_transition_active: bool = False
    pose_transition_preserves_position: bool = False
    last_failure: str = ""
    timestamp_ms: int = 0

    def age_ms(self, now_ms: int | None = None) -> int:
        now = (
            monotonic_ms()
            if now_ms is None
            else normalize_wire_timestamp(now_ms)
        )
        return elapsed_u32_ms(now, self.timestamp_ms)

    def is_fresh(
        self,
        max_age_ms: int = 2_000,
        now_ms: int | None = None,
    ) -> bool:
        return self.age_ms(now_ms) <= max(0, int(max_age_ms))


def _kernel32():
    windll = getattr(ctypes, "windll", None)
    kernel32 = getattr(windll, "kernel32", None)
    if kernel32 is None:
        raise OSError("Windows named shared memory is unavailable")
    kernel32.CreateFileMappingW.restype = ctypes.c_void_p
    kernel32.OpenFileMappingW.restype = ctypes.c_void_p
    kernel32.MapViewOfFile.restype = ctypes.c_void_p
    kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    return kernel32


def _normalized_configured_mode(value: object) -> str:
    text = str(value or "unknown").strip().lower()
    return text if text in _CONFIGURED_CODES else "unknown"


def _normalized_active_backend(value: object) -> str:
    text = str(value or "unknown").strip().lower()
    return text if text in _ACTIVE_CODES else "unknown"


def _u32(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(0xFFFF_FFFE, parsed))


def _optional_u32(value: object) -> int:
    return _NONE_U32 if value is None else _u32(value)


def _decoded_optional_u32(value: int) -> int | None:
    return None if int(value) == _NONE_U32 else int(value)


def _encoded_failure_field(value: object) -> bytes:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    payload = text.encode("utf-8", errors="replace")
    limit = _FAILURE_BYTES - 1
    if len(payload) > limit:
        payload = payload[:limit]
        while payload:
            try:
                payload.decode("utf-8", errors="strict")
                break
            except UnicodeDecodeError as error:
                payload = payload[: error.start]
    return payload + b"\0" * (_FAILURE_BYTES - len(payload))


def encode_tracker_backend_status(status: TrackerBackendStatus) -> bytes:
    flags = 0
    if status.candidate_active:
        flags |= _FLAG_CANDIDATE_ACTIVE
    if status.pose_transition_active:
        flags |= _FLAG_POSE_TRANSITION_ACTIVE
    if status.pose_transition_preserves_position:
        flags |= _FLAG_POSE_TRANSITION_PRESERVES_POSITION
    return struct.pack(
        _STATUS_FORMAT,
        STATUS_MAGIC,
        STATUS_VERSION,
        _CONFIGURED_CODES[_normalized_configured_mode(status.configured_mode)],
        _ACTIVE_CODES[_normalized_active_backend(status.active_backend)],
        flags,
        _u32(status.failover_count),
        _u32(status.primary_retry_attempts),
        _optional_u32(status.retry_in_ms),
        _optional_u32(status.candidate_age_ms),
        _u32(status.candidate_probe_count),
        _u32(status.candidate_healthy_callbacks),
        _u32(status.backend_transition_id),
        normalize_wire_timestamp(status.timestamp_ms),
        _encoded_failure_field(status.last_failure),
    )


def decode_tracker_backend_status(data: bytes) -> TrackerBackendStatus | None:
    if len(data) != STATUS_SIZE:
        return None
    try:
        (
            magic,
            version,
            configured_code,
            active_code,
            flags,
            failover_count,
            retry_attempts,
            retry_in_ms,
            candidate_age_ms,
            candidate_probe_count,
            candidate_healthy_callbacks,
            transition_id,
            timestamp_ms,
            failure_field,
        ) = struct.unpack(_STATUS_FORMAT, data)
    except struct.error:
        return None
    if magic != STATUS_MAGIC or version != STATUS_VERSION:
        return None
    configured_mode = _CONFIGURED_NAMES.get(configured_code)
    active_backend = _ACTIVE_NAMES.get(active_code)
    if configured_mode is None or active_backend is None:
        return None
    failure = failure_field.split(b"\0", 1)[0].decode(
        "utf-8",
        errors="replace",
    )
    return TrackerBackendStatus(
        configured_mode=configured_mode,
        active_backend=active_backend,
        failover_count=int(failover_count),
        primary_retry_attempts=int(retry_attempts),
        retry_in_ms=_decoded_optional_u32(retry_in_ms),
        candidate_active=bool(flags & _FLAG_CANDIDATE_ACTIVE),
        candidate_age_ms=_decoded_optional_u32(candidate_age_ms),
        candidate_probe_count=int(candidate_probe_count),
        candidate_healthy_callbacks=int(candidate_healthy_callbacks),
        backend_transition_id=int(transition_id),
        pose_transition_active=bool(flags & _FLAG_POSE_TRANSITION_ACTIVE),
        pose_transition_preserves_position=bool(
            flags & _FLAG_POSE_TRANSITION_PRESERVES_POSITION
        ),
        last_failure=failure,
        timestamp_ms=int(timestamp_ms),
    )


def infer_configured_tracker_mode(tracker: object) -> str:
    active = getattr(tracker, "active_backend", None)
    snapshot = getattr(tracker, "snapshot", None)
    if isinstance(active, str) and callable(snapshot):
        return "auto"
    module = type(tracker).__module__.lower()
    if module.endswith("face_tracker_cv2"):
        return "cv2"
    if module.endswith("face_tracker"):
        return "mediapipe"
    return "unknown"


def status_from_tracker(
    tracker: object,
    *,
    configured_mode: str | None = None,
    timestamp_ms: int | None = None,
) -> TrackerBackendStatus:
    configured = _normalized_configured_mode(
        configured_mode or infer_configured_tracker_mode(tracker)
    )
    active_value = getattr(tracker, "active_backend", None)
    active = (
        _normalized_active_backend(active_value)
        if isinstance(active_value, str)
        else (
            configured
            if configured in {"mediapipe", "cv2"}
            else "unknown"
        )
    )
    values: dict[str, object] = {}
    snapshot = getattr(tracker, "snapshot", None)
    values_obj: object | None = None
    if callable(snapshot):
        try:
            try:
                values_obj = snapshot(timestamp_ms)
            except TypeError:
                values_obj = snapshot()
        except Exception as error:
            values["last_failure"] = (
                f"backend status snapshot failed: {type(error).__name__}"
            )
        if values_obj is not None:
            for name in (
                "active_backend",
                "failover_count",
                "primary_retry_attempts",
                "retry_in_ms",
                "primary_candidate_active",
                "primary_candidate_age_ms",
                "primary_candidate_probe_count",
                "primary_candidate_healthy_callbacks",
                "backend_transition_id",
                "pose_transition_active",
                "pose_transition_preserves_position",
                "last_failure",
            ):
                values[name] = getattr(values_obj, name, values.get(name))
    snapshot_active = values.get("active_backend")
    if isinstance(snapshot_active, str):
        active = _normalized_active_backend(snapshot_active)
    timestamp = (
        monotonic_ms()
        if timestamp_ms is None
        else normalize_wire_timestamp(timestamp_ms)
    )
    return TrackerBackendStatus(
        configured_mode=configured,
        active_backend=active,
        failover_count=_u32(values.get("failover_count", 0)),
        primary_retry_attempts=_u32(
            values.get("primary_retry_attempts", 0)
        ),
        retry_in_ms=(
            None
            if values.get("retry_in_ms") is None
            else _u32(values.get("retry_in_ms"))
        ),
        candidate_active=bool(
            values.get("primary_candidate_active", False)
        ),
        candidate_age_ms=(
            None
            if values.get("primary_candidate_age_ms") is None
            else _u32(values.get("primary_candidate_age_ms"))
        ),
        candidate_probe_count=_u32(
            values.get("primary_candidate_probe_count", 0)
        ),
        candidate_healthy_callbacks=_u32(
            values.get("primary_candidate_healthy_callbacks", 0)
        ),
        backend_transition_id=_u32(
            values.get("backend_transition_id", 0)
        ),
        pose_transition_active=bool(
            values.get("pose_transition_active", False)
        ),
        pose_transition_preserves_position=bool(
            values.get("pose_transition_preserves_position", False)
        ),
        last_failure=str(values.get("last_failure", "") or ""),
        timestamp_ms=timestamp,
    )


class TrackerBackendStatusWriter:
    def __init__(self, name: str = STATUS_MAPPING_NAME) -> None:
        self._name = name
        self._k32 = _kernel32()
        self._handle: int | None = self._k32.CreateFileMappingW(
            _INVALID_HANDLE,
            None,
            _PAGE_READWRITE,
            0,
            STATUS_SIZE,
            name,
        )
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._view: int | None = self._k32.MapViewOfFile(
            self._handle,
            _FILE_MAP_ALL_ACCESS,
            0,
            0,
            STATUS_SIZE,
        )
        if not self._view:
            error = ctypes.get_last_error()
            self._k32.CloseHandle(self._handle)
            self._handle = None
            raise ctypes.WinError(error)
        self._seq_handle: int | None = self._k32.CreateFileMappingW(
            _INVALID_HANDLE,
            None,
            _PAGE_READWRITE,
            0,
            _SEQ_SIZE,
            f"{name}_Seq",
        )
        self._seq_view: int | None = None
        if self._seq_handle:
            self._seq_view = self._k32.MapViewOfFile(
                self._seq_handle,
                _FILE_MAP_ALL_ACCESS,
                0,
                0,
                _SEQ_SIZE,
            )
        if self._seq_view:
            ctypes.c_uint32.from_address(self._seq_view).value = 0

    def write(self, status: TrackerBackendStatus) -> None:
        if self._view is None:
            raise RuntimeError("write() called after close()")
        payload = encode_tracker_backend_status(status)
        seq_word = (
            ctypes.c_uint32.from_address(self._seq_view)
            if self._seq_view
            else None
        )
        if seq_word is not None:
            seq_word.value = (seq_word.value + 1) & 0xFFFF_FFFF
        ctypes.memmove(self._view, payload, STATUS_SIZE)
        if seq_word is not None:
            seq_word.value = (seq_word.value + 1) & 0xFFFF_FFFF

    def close(self) -> None:
        if self._view is not None:
            self._k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle is not None:
            self._k32.CloseHandle(self._handle)
            self._handle = None
        if self._seq_view is not None:
            self._k32.UnmapViewOfFile(self._seq_view)
            self._seq_view = None
        if self._seq_handle is not None:
            self._k32.CloseHandle(self._seq_handle)
            self._seq_handle = None

    def __enter__(self) -> "TrackerBackendStatusWriter":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class TrackerBackendStatusReader:
    def __init__(self, name: str = STATUS_MAPPING_NAME) -> None:
        self._name = name
        self._k32 = _kernel32()
        self._handle: int | None = None
        self._view: int | None = None
        self._seq_handle: int | None = None
        self._seq_view: int | None = None
        self._try_attach()

    def _try_attach(self) -> None:
        if self._view is not None:
            return
        self._handle = self._k32.OpenFileMappingW(
            _FILE_MAP_READ,
            False,
            self._name,
        )
        if not self._handle:
            self._handle = None
            return
        self._view = self._k32.MapViewOfFile(
            self._handle,
            _FILE_MAP_READ,
            0,
            0,
            STATUS_SIZE,
        )
        if not self._view:
            self._k32.CloseHandle(self._handle)
            self._handle = None
            return
        self._seq_handle = self._k32.OpenFileMappingW(
            _FILE_MAP_READ,
            False,
            f"{self._name}_Seq",
        )
        if self._seq_handle:
            self._seq_view = self._k32.MapViewOfFile(
                self._seq_handle,
                _FILE_MAP_READ,
                0,
                0,
                _SEQ_SIZE,
            )

    def read(self) -> TrackerBackendStatus | None:
        self._try_attach()
        if self._view is None:
            return None
        try:
            raw = (ctypes.c_char * STATUS_SIZE).from_address(self._view)
            payload: bytes | None = None
            if self._seq_view:
                seq_word = ctypes.c_uint32.from_address(self._seq_view)
                for _ in range(8):
                    before = seq_word.value
                    if before & 1:
                        continue
                    candidate = bytes(raw)
                    after = seq_word.value
                    if before == after and not (after & 1):
                        payload = candidate
                        break
            else:
                for _ in range(4):
                    first = bytes(raw)
                    second = bytes(raw)
                    if first == second:
                        payload = second
                        break
            return (
                None
                if payload is None
                else decode_tracker_backend_status(payload)
            )
        except Exception:
            self.close()
            return None

    def close(self) -> None:
        if self._view is not None:
            self._k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle is not None:
            self._k32.CloseHandle(self._handle)
            self._handle = None
        if self._seq_view is not None:
            self._k32.UnmapViewOfFile(self._seq_view)
            self._seq_view = None
        if self._seq_handle is not None:
            self._k32.CloseHandle(self._seq_handle)
            self._seq_handle = None

    def __enter__(self) -> "TrackerBackendStatusReader":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class TrackerBackendStatusPublisher:
    """Best-effort per-frame publisher owned by ``FrameProcessorAdapter``."""

    def __init__(self, tracker: object) -> None:
        self._tracker = tracker
        self._configured_mode = infer_configured_tracker_mode(tracker)
        self._writer: TrackerBackendStatusWriter | None = None
        if self._configured_mode == "unknown":
            return
        try:
            self._writer = TrackerBackendStatusWriter()
        except Exception:
            self._writer = None

    @property
    def available(self) -> bool:
        return self._writer is not None

    def publish(self, timestamp_ms: int) -> None:
        writer = self._writer
        if writer is None:
            return
        try:
            writer.write(
                status_from_tracker(
                    self._tracker,
                    configured_mode=self._configured_mode,
                    timestamp_ms=timestamp_ms,
                )
            )
        except Exception:
            try:
                writer.close()
            except Exception:
                pass
            self._writer = None

    def close(self) -> None:
        writer = self._writer
        self._writer = None
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def make_tracker_backend_status_publisher(
    tracker: object,
) -> TrackerBackendStatusPublisher | None:
    publisher = TrackerBackendStatusPublisher(tracker)
    return publisher if publisher.available else None
