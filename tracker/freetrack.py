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
from __future__ import annotations

import ctypes
import math
import struct
import threading

# <Iii  = uint32 DataID, int32 CamW, int32 CamH
# 6f    = Yaw Pitch Roll X Y Z
# 6f    = RawYaw RawPitch RawRoll RawX RawY RawZ
# 8f    = 8 tracking point floats (X1,Y1 ... X4,Y4)
FREETRACK_FORMAT = "<Iii6f6f8f"
FREETRACK_SIZE = struct.calcsize(FREETRACK_FORMAT)  # 92 bytes
_DATA_ID_SIZE = struct.calcsize("<I")
_UINT32_MAX = 0xFFFF_FFFF

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


def _finite_float(value: object, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{field_name} must be a finite float") from error
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite float")
    return parsed


def _pack_freetrack_packet(
    seq: int,
    x: float,
    y: float,
    z: float,
) -> bytes:
    sequence = int(seq)
    if not 0 <= sequence <= _UINT32_MAX:
        raise ValueError("FreeTrack DataID must be an unsigned 32-bit integer")
    return struct.pack(
        FREETRACK_FORMAT,
        sequence,
        0, 0,  # CamWidth, CamHeight
        0.0, 0.0, 0.0,  # Yaw, Pitch, Roll
        _finite_float(x, "x"),
        _finite_float(y, "y"),
        _finite_float(z, "z"),
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0,  # Raw pose (unused)
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,  # Tracking pts (unused)
    )


class FreetracWriter:
    """Writes head pose to named shared memory in FreeTrack format.

    ``DataID`` is the FreeTrack packet publication marker. The pose body is
    installed first and the new ID is written last, so readers cannot observe a
    new ID paired with an incomplete new body.
    """

    def __init__(self, name: str = _SHM_NAME) -> None:
        self._name = name
        self._seq: int = 0
        self._handle: int | None = None
        self._view: int | None = None
        self._write_lock = threading.RLock()

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

        # Initialise: head centred, 60 cm away, DataID = 0.
        self._write_raw(seq=0, x=0.0, y=0.0, z=60.0)

    def write(self, x: float, y: float, z: float) -> None:
        """Install one complete pose and then publish its incremented DataID."""
        with self._write_lock:
            next_seq = (self._seq + 1) & _UINT32_MAX
            self._write_raw(seq=next_seq, x=x, y=y, z=z)
            # Failed validation/packing/body/ID writes leave the logical writer
            # sequence unchanged, so the same DataID can be retried safely.
            self._seq = next_seq

    def _write_raw(self, seq: int, x: float, y: float, z: float) -> None:
        with self._write_lock:
            view = self._view
            if view is None:
                raise RuntimeError("write() called after close()")
            data = _pack_freetrack_packet(seq, x, y, z)
            # FreeTrack readers use DataID as the new-packet signal. Publish the
            # body first, then make the ID visible in the final aligned 4-byte
            # copy. Sequential ctypes calls preserve this store order on the
            # supported Windows x64 runtime.
            ctypes.memmove(
                view + _DATA_ID_SIZE,
                data[_DATA_ID_SIZE:],
                FREETRACK_SIZE - _DATA_ID_SIZE,
            )
            ctypes.memmove(view, data[:_DATA_ID_SIZE], _DATA_ID_SIZE)

    def close(self) -> None:
        """Release the shared memory mapping and handle."""
        with self._write_lock:
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
