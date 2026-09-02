# tracker/shared_memory.py
import ctypes
from enum import IntEnum
import struct
import threading

from tracker.pose import monotonic_ms
from tracker.sequence_mapping import (
    DEFAULT_SEQUENCE_ATTACH_ATTEMPTS,
    next_sequence_write_markers,
    try_attach_sequence_mapping,
)

STRUCT_FORMAT = "<fffI"   # little-endian: float x, float y, float z, uint32 timestamp
STRUCT_SIZE = struct.calcsize(STRUCT_FORMAT)  # == 16
STATE_STRUCT_FORMAT = "<II"  # state code, shared uptime timestamp_ms
STATE_STRUCT_SIZE = struct.calcsize(STATE_STRUCT_FORMAT)
SEQ_STRUCT_SIZE = 4


class TrackingState(IntEnum):
    """Face-presence state published separately from the legacy pose block."""

    PAUSED = 0
    TRACKING = 1
    HOLD = 2


_STATE_NAMES = {
    TrackingState.PAUSED: "paused",
    TrackingState.TRACKING: "tracking",
    TrackingState.HOLD: "hold",
}

_PAGE_READWRITE = 0x04
_FILE_MAP_ALL_ACCESS = 0xF001F
_INVALID_HANDLE = ctypes.c_void_p(-1)  # INVALID_HANDLE_VALUE (-1 as a void pointer)
_FILE_MAP_READ = 0x0004

_k32 = ctypes.windll.kernel32

# NOTE: These assignments mutate the process-global windll.kernel32 object.
# All callers in this process will inherit these restype/argtypes settings.
_k32.CreateFileMappingW.restype = ctypes.c_void_p
_k32.OpenFileMappingW.restype = ctypes.c_void_p
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [ctypes.c_void_p]


class SharedMemoryWriter:
    """Writes head pose {x, y, z, timestamp} to a Windows Named Shared Memory segment."""

    def __init__(self, name: str = "G3D") -> None:
        self._name = name
        self._handle: int | None = None
        self._view: int | None = None
        self._seq_handle: int | None = None
        self._seq_view: int | None = None
        self._committed_sequence = 0
        self._write_lock = threading.RLock()
        self._handle = _k32.CreateFileMappingW(
            _INVALID_HANDLE, None, _PAGE_READWRITE, 0, STRUCT_SIZE, name,
        )
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._view = _k32.MapViewOfFile(
            self._handle, _FILE_MAP_ALL_ACCESS, 0, 0, STRUCT_SIZE,
        )
        if not self._view:
            err = ctypes.get_last_error()
            _k32.CloseHandle(self._handle)
            self._handle = None
            raise ctypes.WinError(err)
        self._seq_handle = _k32.CreateFileMappingW(
            _INVALID_HANDLE, None, _PAGE_READWRITE, 0, SEQ_STRUCT_SIZE,
            f"{name}_Seq",
        )
        if self._seq_handle:
            self._seq_view = _k32.MapViewOfFile(
                self._seq_handle, _FILE_MAP_ALL_ACCESS, 0, 0, SEQ_STRUCT_SIZE,
            )
        if self._seq_view:
            ctypes.c_uint32.from_address(self._seq_view).value = 0
        self.write(x=0.0, y=0.0, z=60.0)

    def write(self, x: float, y: float, z: float) -> None:
        """Write head position using the native overlay's shared uptime clock."""
        with self._write_lock:
            view = self._view
            if view is None:
                raise RuntimeError("write() called after close()")
            ts = monotonic_ms()
            data = struct.pack(STRUCT_FORMAT, x, y, z, ts)
            seq_word = (
                ctypes.c_uint32.from_address(self._seq_view)
                if self._seq_view
                else None
            )
            if seq_word is None:
                ctypes.memmove(view, data, STRUCT_SIZE)
                return

            markers = next_sequence_write_markers(
                self._committed_sequence
            )
            seq_word.value = markers.writing
            ctypes.memmove(view, data, STRUCT_SIZE)
            seq_word.value = markers.committed
            self._committed_sequence = markers.committed

    def close(self) -> None:
        """Release the shared memory mapping and handle."""
        with self._write_lock:
            if self._view is not None:
                _k32.UnmapViewOfFile(self._view)
                self._view = None
            if self._handle is not None:
                _k32.CloseHandle(self._handle)
                self._handle = None
            if self._seq_view is not None:
                _k32.UnmapViewOfFile(self._seq_view)
                self._seq_view = None
            if self._seq_handle is not None:
                _k32.CloseHandle(self._seq_handle)
                self._seq_handle = None

    def __enter__(self) -> "SharedMemoryWriter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class SharedMemoryReader:
    """Read-only view of a Windows Named Shared Memory segment.

    Returns None from read() if the segment does not exist yet.
    Retries attachment automatically on each read() call.
    """

    def __init__(self, name: str = "G3D") -> None:
        self._name = name
        self._handle: int | None = None
        self._view: int | None = None
        self._seq_handle: int | None = None
        self._seq_view: int | None = None
        self._seq_attach_attempts_remaining = (
            DEFAULT_SEQUENCE_ATTACH_ATTEMPTS
        )
        self._try_attach()

    def _try_attach(self) -> None:
        if self._view is None:
            if self._handle is None:
                self._handle = _k32.OpenFileMappingW(
                    _FILE_MAP_READ,
                    False,
                    self._name,
                )
                if not self._handle:
                    self._handle = None
                    return
            self._view = _k32.MapViewOfFile(
                self._handle,
                _FILE_MAP_READ,
                0,
                0,
                STRUCT_SIZE,
            )
            if not self._view:
                _k32.CloseHandle(self._handle)
                self._handle = None
                self._view = None
                return

        attachment = try_attach_sequence_mapping(
            _k32,
            f"{self._name}_Seq",
            handle=self._seq_handle,
            view=self._seq_view,
            attempts_remaining=self._seq_attach_attempts_remaining,
            file_map_read=_FILE_MAP_READ,
            size=SEQ_STRUCT_SIZE,
        )
        self._seq_handle = attachment.handle
        self._seq_view = attachment.view
        self._seq_attach_attempts_remaining = attachment.attempts_remaining

    def read(self) -> tuple[float, float, float, int] | None:
        """Return (x_cm, y_cm, z_cm, timestamp_ms) or None if segment absent."""
        self._try_attach()
        if self._view is None:
            return None
        try:
            raw = (ctypes.c_char * STRUCT_SIZE).from_address(self._view)
            snapshot = None
            if self._seq_view:
                seq_word = ctypes.c_uint32.from_address(self._seq_view)
                for _ in range(8):
                    seq1 = seq_word.value
                    if seq1 & 1:
                        continue
                    candidate = bytes(raw)
                    seq2 = seq_word.value
                    if seq1 == seq2 and not (seq2 & 1):
                        snapshot = candidate
                        break
            else:
                # Backward-compatible best effort for legacy writers.
                for _ in range(4):
                    first = bytes(raw)
                    second = bytes(raw)
                    if first == second:
                        snapshot = second
                        break
            if snapshot is None:
                return None
            x, y, z, ts = struct.unpack(STRUCT_FORMAT, snapshot)
        except OSError:
            _k32.UnmapViewOfFile(self._view)
            self._view = None
            _k32.CloseHandle(self._handle)
            self._handle = None
            return None
        return x, y, z, ts

    def close(self) -> None:
        if self._view is not None:
            _k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle is not None:
            _k32.CloseHandle(self._handle)
            self._handle = None
        if self._seq_view is not None:
            _k32.UnmapViewOfFile(self._seq_view)
            self._seq_view = None
        if self._seq_handle is not None:
            _k32.CloseHandle(self._seq_handle)
            self._seq_handle = None

    def __enter__(self) -> "SharedMemoryReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class TrackingStateWriter:
    """Publish face validity without changing the legacy ``G3D`` ABI."""

    def __init__(self, name: str = "G3D_State") -> None:
        self._handle: int | None = _k32.CreateFileMappingW(
            _INVALID_HANDLE, None, _PAGE_READWRITE, 0, STATE_STRUCT_SIZE, name,
        )
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._view: int | None = _k32.MapViewOfFile(
            self._handle, _FILE_MAP_ALL_ACCESS, 0, 0, STATE_STRUCT_SIZE,
        )
        if not self._view:
            err = ctypes.get_last_error()
            _k32.CloseHandle(self._handle)
            self._handle = None
            raise ctypes.WinError(err)
        self.write("paused")

    def write(self, state: str | TrackingState) -> None:
        if self._view is None:
            raise RuntimeError("write() called after close()")
        if isinstance(state, str):
            try:
                code = TrackingState[state.strip().upper()]
            except KeyError as exc:
                raise ValueError(f"unknown tracking state: {state!r}") from exc
        else:
            code = TrackingState(state)
        ts = monotonic_ms()
        ctypes.memmove(
            self._view,
            struct.pack(STATE_STRUCT_FORMAT, int(code), ts),
            STATE_STRUCT_SIZE,
        )

    def close(self) -> None:
        if self._view is not None:
            _k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle is not None:
            _k32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "TrackingStateWriter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class TrackingStateReader:
    """Read face validity from ``G3D_State``; attach lazily."""

    def __init__(self, name: str = "G3D_State") -> None:
        self._name = name
        self._handle: int | None = None
        self._view: int | None = None
        self._try_attach()

    def _try_attach(self) -> None:
        if self._view is not None:
            return
        self._handle = _k32.OpenFileMappingW(_FILE_MAP_READ, False, self._name)
        if not self._handle:
            self._handle = None
            return
        self._view = _k32.MapViewOfFile(
            self._handle, _FILE_MAP_READ, 0, 0, STATE_STRUCT_SIZE,
        )
        if not self._view:
            _k32.CloseHandle(self._handle)
            self._handle = None

    def read(self) -> tuple[str, int] | None:
        self._try_attach()
        if self._view is None:
            return None
        try:
            raw = (ctypes.c_char * STATE_STRUCT_SIZE).from_address(self._view)
            snapshot = None
            for _ in range(4):
                first = bytes(raw)
                second = bytes(raw)
                if first == second:
                    snapshot = second
                    break
            if snapshot is None:
                return None
            code, ts = struct.unpack(STATE_STRUCT_FORMAT, snapshot)
            state = _STATE_NAMES[TrackingState(code)]
        except (OSError, ValueError, KeyError):
            return None
        return state, ts

    def close(self) -> None:
        if self._view is not None:
            _k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle is not None:
            _k32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "TrackingStateReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
