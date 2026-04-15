# tracker/shared_settings.py
"""Shared-memory channel for live overlay tuning (v2, 56 bytes).

Layout (56 bytes, little-endian):
    float   strength_x
    float   strength_y
    float   virtual_depth_cm
    float   screen_w_cm        (0 = overlay autodetect)
    float   screen_h_cm        (0 = overlay autodetect)
    uint32  depth_curve        (0=linear, 1=sqrt, 2=gamma)
    float   depth_gamma
    float   focus_radius       (UV radius for focus ring)
    float   head_dist_cm
    float   camera_fov_deg
    float   ipd_mm
    float   smoothing_alpha    (Kalman measurement noise r)
    float   deadzone_mm
    uint32  version            (monotonic counter)
"""
from __future__ import annotations

import ctypes
import struct
from dataclasses import dataclass

STRUCT_FORMAT = "<fffffIfffffffI"
STRUCT_SIZE = struct.calcsize(STRUCT_FORMAT)  # == 56
SHM_NAME = "G3D_Settings"

_PAGE_READWRITE   = 0x04
_FILE_MAP_ALL_ACCESS = 0xF001F
_FILE_MAP_READ    = 0x0004
_INVALID_HANDLE   = ctypes.c_void_p(-1)

_k32 = ctypes.windll.kernel32
_k32.CreateFileMappingW.restype = ctypes.c_void_p
_k32.OpenFileMappingW.restype   = ctypes.c_void_p
_k32.MapViewOfFile.restype      = ctypes.c_void_p
_k32.UnmapViewOfFile.argtypes   = [ctypes.c_void_p]
_k32.CloseHandle.argtypes       = [ctypes.c_void_p]


@dataclass(frozen=True)
class OverlaySettings:
    strength_x: float = 1.0
    strength_y: float = 1.0
    virtual_depth_cm: float = 30.0
    screen_w_cm: float = 0.0
    screen_h_cm: float = 0.0
    depth_curve: int = 1          # 0=linear, 1=sqrt, 2=gamma
    depth_gamma: float = 1.0
    focus_radius: float = 0.1
    head_dist_cm: float = 60.0
    camera_fov_deg: float = 90.0
    ipd_mm: float = 64.0
    smoothing_alpha: float = 0.1
    deadzone_mm: float = 5.0


class SharedSettingsWriter:
    """Creates and owns the G3D_Settings shared memory segment."""

    def __init__(self, name: str = SHM_NAME) -> None:
        self._name = name
        self._handle: int | None = None
        self._view: int | None = None
        self._version: int = 0

        self._handle = _k32.CreateFileMappingW(
            _INVALID_HANDLE, None, _PAGE_READWRITE, 0, STRUCT_SIZE, name,
        )
        if self._handle is None:
            raise ctypes.WinError(ctypes.get_last_error())
        self._view = _k32.MapViewOfFile(
            self._handle, _FILE_MAP_ALL_ACCESS, 0, 0, STRUCT_SIZE,
        )
        if self._view is None:
            err = ctypes.get_last_error()
            _k32.CloseHandle(self._handle)
            self._handle = None
            raise ctypes.WinError(err)

        self.write(OverlaySettings())

    def write(self, s: OverlaySettings) -> None:
        view = self._view
        if view is None:
            raise RuntimeError("write() called after close()")
        self._version = (self._version + 1) & 0xFFFF_FFFF
        data = struct.pack(
            STRUCT_FORMAT,
            float(s.strength_x), float(s.strength_y),
            float(s.virtual_depth_cm),
            float(s.screen_w_cm), float(s.screen_h_cm),
            int(s.depth_curve),
            float(s.depth_gamma), float(s.focus_radius),
            float(s.head_dist_cm), float(s.camera_fov_deg),
            float(s.ipd_mm), float(s.smoothing_alpha),
            float(s.deadzone_mm),
            self._version,
        )
        ctypes.memmove(view, data, STRUCT_SIZE)

    def close(self) -> None:
        if self._view is not None:
            _k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle is not None:
            _k32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "SharedSettingsWriter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class SharedSettingsReader:
    """Opens G3D_Settings read-only. Safe to call even if writer not running."""

    def __init__(self, name: str = SHM_NAME) -> None:
        self._name = name
        self._handle: int | None = None
        self._view: int | None = None
        self._try_attach()

    def _try_attach(self) -> None:
        if self._view is not None:
            return
        if self._handle is None:
            self._handle = _k32.OpenFileMappingW(_FILE_MAP_READ, False, self._name)
            if self._handle is None:
                return  # writer not running yet
        self._view = _k32.MapViewOfFile(
            self._handle, _FILE_MAP_READ, 0, 0, STRUCT_SIZE,
        )
        if self._view is None:
            # Map failed; close handle so next call retries cleanly
            _k32.CloseHandle(self._handle)
            self._handle = None

    def read(self) -> OverlaySettings | None:
        """Return current settings snapshot, or None if writer not running."""
        self._try_attach()
        if self._view is None:
            return None
        try:
            raw = (ctypes.c_char * STRUCT_SIZE).from_address(self._view)
            f = struct.unpack(STRUCT_FORMAT, bytes(raw))
        except OSError:
            self._view = None  # stale view; force re-attach next call
            return None
        return OverlaySettings(
            strength_x=f[0], strength_y=f[1],
            virtual_depth_cm=f[2],
            screen_w_cm=f[3], screen_h_cm=f[4],
            depth_curve=f[5],
            depth_gamma=f[6], focus_radius=f[7],
            head_dist_cm=f[8], camera_fov_deg=f[9],
            ipd_mm=f[10], smoothing_alpha=f[11],
            deadzone_mm=f[12],
        )

    def close(self) -> None:
        if self._view is not None:
            _k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle is not None:
            _k32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "SharedSettingsReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
