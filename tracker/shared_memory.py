# tracker/shared_memory.py
import ctypes
import struct
import time

STRUCT_FORMAT = "<fffI"   # little-endian: float x, float y, float z, uint32 timestamp
STRUCT_SIZE = struct.calcsize(STRUCT_FORMAT)  # == 16

_PAGE_READWRITE = 0x04
_FILE_MAP_ALL_ACCESS = 0xF001F
_INVALID_HANDLE = ctypes.c_void_p(-1)  # INVALID_HANDLE_VALUE (-1 as a void pointer)

_k32 = ctypes.windll.kernel32

# NOTE: These assignments mutate the process-global windll.kernel32 object.
# All callers in this process will inherit these restype/argtypes settings.
_k32.CreateFileMappingW.restype = ctypes.c_void_p
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [ctypes.c_void_p]


class SharedMemoryWriter:
    """Writes head pose {x, y, z, timestamp} to a Windows Named Shared Memory segment."""

    def __init__(self, name: str = "G3D") -> None:
        self._name = name
        self._handle: int | None = None
        self._view: int | None = None
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
        self.write(x=0.0, y=0.0, z=60.0)

    def write(self, x: float, y: float, z: float) -> None:
        """Write head position to shared memory with a millisecond timestamp."""
        view = self._view
        if view is None:
            raise RuntimeError("write() called after close()")
        ts = int(time.monotonic_ns() // 1_000_000) & 0xFFFF_FFFF
        data = struct.pack(STRUCT_FORMAT, x, y, z, ts)
        ctypes.memmove(view, data, STRUCT_SIZE)

    def close(self) -> None:
        """Release the shared memory mapping and handle."""
        if self._view is not None:
            _k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle is not None:
            _k32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "SharedMemoryWriter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
