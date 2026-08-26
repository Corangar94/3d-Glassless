"""Versioned high-rate head-pose shared memory.

The legacy ``G3D`` block remains unchanged. This companion mapping adds
velocity, orientation, confidence, camera/publish timestamps, and producer
prediction lead so the native overlay can compensate only the remaining
publish-to-render delay without filtering or predicting the same interval twice.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from enum import IntFlag
import math
import struct

from tracker.pose import FilteredPose, monotonic_ms

POSE_V2_NAME = "G3D_PoseV2"
POSE_V2_MAGIC = 0x32443347
POSE_V2_VERSION = 2
POSE_V2_FORMAT = "<IIffffffffffIIII"
POSE_V2_SIZE = struct.calcsize(POSE_V2_FORMAT)
POSE_V2_SEQ_SIZE = 4
_UINT32_MASK = 0xFFFF_FFFF
_MAX_ENCODED_PREDICTION_LEAD_MS = 1000

_PAGE_READWRITE = 0x04
_FILE_MAP_ALL_ACCESS = 0xF001F
_FILE_MAP_READ = 0x0004
_INVALID_HANDLE = ctypes.c_void_p(-1)
_k32 = ctypes.windll.kernel32
_k32.CreateFileMappingW.restype = ctypes.c_void_p
_k32.OpenFileMappingW.restype = ctypes.c_void_p
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [ctypes.c_void_p]


class PoseFlags(IntFlag):
    VALID = 1 << 0
    PREDICTED = 1 << 1
    ORIENTATION_VALID = 1 << 2


@dataclass(frozen=True)
class PosePacketV2:
    x_cm: float
    y_cm: float
    z_cm: float
    vx_cm_s: float
    vy_cm_s: float
    vz_cm_s: float
    yaw_deg: float
    pitch_deg: float
    roll_deg: float
    confidence: float
    capture_timestamp_ms: int
    publish_timestamp_ms: int
    flags: PoseFlags
    prediction_lead_ms: int = 0


def _prediction_lead_ms(pose: FilteredPose, publish_timestamp_ms: int) -> int:
    """Encode only a small forward producer horizon into the old reserved word."""
    target = int(pose.prediction_target_timestamp_ms) & _UINT32_MASK
    if target == 0:
        return 0
    lead = (target - int(publish_timestamp_ms)) & _UINT32_MASK
    if lead > _MAX_ENCODED_PREDICTION_LEAD_MS:
        return 0
    return lead


class PoseStateWriter:
    def __init__(self, name: str = POSE_V2_NAME) -> None:
        self._name = name
        self._handle: int | None = _k32.CreateFileMappingW(
            _INVALID_HANDLE, None, _PAGE_READWRITE, 0, POSE_V2_SIZE, name
        )
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._view: int | None = _k32.MapViewOfFile(
            self._handle, _FILE_MAP_ALL_ACCESS, 0, 0, POSE_V2_SIZE
        )
        if not self._view:
            error = ctypes.get_last_error()
            _k32.CloseHandle(self._handle)
            self._handle = None
            raise ctypes.WinError(error)
        self._seq_handle: int | None = _k32.CreateFileMappingW(
            _INVALID_HANDLE,
            None,
            _PAGE_READWRITE,
            0,
            POSE_V2_SEQ_SIZE,
            f"{name}_Seq",
        )
        self._seq_view: int | None = None
        if self._seq_handle:
            self._seq_view = _k32.MapViewOfFile(
                self._seq_handle,
                _FILE_MAP_ALL_ACCESS,
                0,
                0,
                POSE_V2_SEQ_SIZE,
            )
        if self._seq_view:
            ctypes.c_uint32.from_address(self._seq_view).value = 0
        self.write(
            FilteredPose(
                x_cm=0.0,
                y_cm=0.0,
                z_cm=60.0,
                publish_timestamp_ms=monotonic_ms(),
            ),
            valid=False,
        )

    def write(self, pose: FilteredPose, *, valid: bool = True) -> None:
        if self._view is None:
            raise RuntimeError("write() called after close()")
        values = (
            pose.x_cm,
            pose.y_cm,
            pose.z_cm,
            pose.vx_cm_s,
            pose.vy_cm_s,
            pose.vz_cm_s,
            pose.yaw_deg,
            pose.pitch_deg,
            pose.roll_deg,
            pose.confidence,
        )
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("pose packet contains non-finite values")
        flags = PoseFlags.VALID if valid else PoseFlags(0)
        if pose.predicted:
            flags |= PoseFlags.PREDICTED
        if any(
            abs(value) > 1e-4
            for value in (pose.yaw_deg, pose.pitch_deg, pose.roll_deg)
        ):
            flags |= PoseFlags.ORIENTATION_VALID
        publish_timestamp_ms = int(
            pose.publish_timestamp_ms or monotonic_ms()
        ) & _UINT32_MASK
        prediction_lead_ms = _prediction_lead_ms(pose, publish_timestamp_ms)
        data = struct.pack(
            POSE_V2_FORMAT,
            POSE_V2_MAGIC,
            POSE_V2_VERSION,
            *[float(value) for value in values],
            int(pose.capture_timestamp_ms) & _UINT32_MASK,
            publish_timestamp_ms,
            int(flags),
            prediction_lead_ms,
        )
        sequence = (
            ctypes.c_uint32.from_address(self._seq_view)
            if self._seq_view
            else None
        )
        if sequence is not None:
            sequence.value = (sequence.value + 1) & _UINT32_MASK
        ctypes.memmove(self._view, data, POSE_V2_SIZE)
        if sequence is not None:
            sequence.value = (sequence.value + 1) & _UINT32_MASK

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

    def __enter__(self) -> "PoseStateWriter":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class PoseStateReader:
    def __init__(self, name: str = POSE_V2_NAME) -> None:
        self._name = name
        self._handle: int | None = None
        self._view: int | None = None
        self._seq_handle: int | None = None
        self._seq_view: int | None = None
        self._try_attach()

    def _try_attach(self) -> None:
        if self._view is not None:
            return
        self._handle = _k32.OpenFileMappingW(_FILE_MAP_READ, False, self._name)
        if not self._handle:
            self._handle = None
            return
        self._view = _k32.MapViewOfFile(
            self._handle, _FILE_MAP_READ, 0, 0, POSE_V2_SIZE
        )
        if not self._view:
            _k32.CloseHandle(self._handle)
            self._handle = None
            return
        self._seq_handle = _k32.OpenFileMappingW(
            _FILE_MAP_READ, False, f"{self._name}_Seq"
        )
        if self._seq_handle:
            self._seq_view = _k32.MapViewOfFile(
                self._seq_handle,
                _FILE_MAP_READ,
                0,
                0,
                POSE_V2_SEQ_SIZE,
            )

    def read(self) -> PosePacketV2 | None:
        self._try_attach()
        if self._view is None:
            return None
        raw = (ctypes.c_char * POSE_V2_SIZE).from_address(self._view)
        snapshot: bytes | None = None
        if self._seq_view:
            sequence = ctypes.c_uint32.from_address(self._seq_view)
            for _ in range(8):
                before = sequence.value
                if before & 1:
                    continue
                candidate = bytes(raw)
                after = sequence.value
                if before == after and not (after & 1):
                    snapshot = candidate
                    break
        else:
            for _ in range(4):
                first, second = bytes(raw), bytes(raw)
                if first == second:
                    snapshot = second
                    break
        if snapshot is None:
            return None
        unpacked = struct.unpack(POSE_V2_FORMAT, snapshot)
        if unpacked[0] != POSE_V2_MAGIC or unpacked[1] != POSE_V2_VERSION:
            return None
        values = unpacked[2:12]
        if not all(math.isfinite(float(value)) for value in values):
            return None
        prediction_lead_ms = int(unpacked[15])
        if prediction_lead_ms > _MAX_ENCODED_PREDICTION_LEAD_MS:
            prediction_lead_ms = 0
        return PosePacketV2(
            x_cm=values[0],
            y_cm=values[1],
            z_cm=values[2],
            vx_cm_s=values[3],
            vy_cm_s=values[4],
            vz_cm_s=values[5],
            yaw_deg=values[6],
            pitch_deg=values[7],
            roll_deg=values[8],
            confidence=values[9],
            capture_timestamp_ms=unpacked[12],
            publish_timestamp_ms=unpacked[13],
            flags=PoseFlags(unpacked[14]),
            prediction_lead_ms=prediction_lead_ms,
        )

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

    def __enter__(self) -> "PoseStateReader":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
