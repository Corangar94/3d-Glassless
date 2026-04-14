# tracker/freetrack.py
"""
Writes head pose to the FreeTrack shared memory segment (FT_SharedMem).

Layout matches opentrack fttypes.h FTData struct:
  uint32 DataID | int32 CamW | int32 CamH |
  float Yaw Pitch Roll X Y Z |
  float RawYaw RawPitch RawRoll RawX RawY RawZ |
  float X1 Y1 X2 Y2 X3 Y3 X4 Y4
Total: 92 bytes

Both this module and OpenTrack write to "FT_SharedMem".
The ReShade addon reads DataID + X/Y/Z from offsets 0/24/28/32.
"""
import ctypes
import struct

# <Iii  = uint32 DataID, int32 CamW, int32 CamH
# 6f    = Yaw Pitch Roll X Y Z
# 6f    = RawYaw RawPitch RawRoll RawX RawY RawZ
# 8f    = 8 tracking point floats (X1,Y1 ... X4,Y4)
FREETRACK_FORMAT = "<Iii6f6f8f"
FREETRACK_SIZE = struct.calcsize(FREETRACK_FORMAT)  # 92 bytes

_SHM_NAME = "FT_SharedMem"

_PAGE_READWRITE = 0x04
_FILE_MAP_ALL_ACCESS = 0xF001F
_INVALID_HANDLE = ctypes.c_void_p(-1)

_k32 = ctypes.windll.kernel32

# NOTE: These mutate the process-global windll.kernel32 object.
# All callers in this process inherit these restype/argtypes settings.
_k32.CreateFileMappingW.restype = ctypes.c_void_p
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [ctypes.c_void_p]


class FreetracWriter:
    """
    Writes head pose to Windows Named Shared Memory in FreeTrack format.

    Default name is "FT_SharedMem" (standard FreeTrack/OpenTrack protocol).
    Pass a different name only for testing.
    """

    def __init__(self, name: str = _SHM_NAME) -> None:
        self._name = name
        self._seq: int = 0
        self._handle: int | None = None
        self._view: int | None = None

        self._handle = _k32.CreateFileMappingW(
            _INVALID_HANDLE, None, _PAGE_READWRITE, 0, FREETRACK_SIZE, name,
        )
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())

        self._view = _k32.MapViewOfFile(
            self._handle, _FILE_MAP_ALL_ACCESS, 0, 0, FREETRACK_SIZE,
        )
        if not self._view:
            err = ctypes.get_last_error()
            _k32.CloseHandle(self._handle)
            self._handle = None
            raise ctypes.WinError(err)

        # Initialise: head centred, 60 cm away, DataID = 0
        self._write_raw(seq=0, x=0.0, y=0.0, z=60.0)

    def write(self, x: float, y: float, z: float) -> None:
        """Write head position. Increments DataID so readers detect new data."""
        self._seq = (self._seq + 1) & 0xFFFF_FFFF
        self._write_raw(seq=self._seq, x=x, y=y, z=z)

    def _write_raw(self, seq: int, x: float, y: float, z: float) -> None:
        view = self._view
        if view is None:
            raise RuntimeError("write() called after close()")
        data = struct.pack(
            FREETRACK_FORMAT,
            seq,   # DataID
            0, 0,  # CamWidth, CamHeight
            0.0, 0.0, 0.0,  # Yaw, Pitch, Roll
            x, y, z,        # X, Y, Z (cm)
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0,  # Raw pose (unused)
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,  # Tracking pts (unused)
        )
        ctypes.memmove(view, data, FREETRACK_SIZE)

    def close(self) -> None:
        """Release shared memory mapping and handle."""
        if self._view is not None:
            _k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle is not None:
            _k32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "FreetracWriter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
