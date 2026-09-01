# tracker/shared_settings.py
"""Shared-memory channel for live overlay tuning (v5, 88 bytes).

Layout (88 bytes, little-endian):
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
    uint32  display_backend    (0=desktop, 1=stereo, 2=quilt)
    uint32  depth_mode         (0=quality, 1=balanced, 2=fast, 3=auto)
    uint32  version            (monotonic counter)
    uint32  stereo_layout      (0=full_sbs, 1=half_sbs)
    uint32  eye_order          (0=left_right, 1=right_left)
    uint32  panel_width_px
    uint32  panel_height_px
    float   focus_plane_cm
    uint32  tracking_mode      (0=glassless3d_managed, 1=vendor_managed)
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import math
import struct
import threading

STRUCT_FORMAT = "<fffffIfffffffIII" "IIIIfI"
STRUCT_SIZE = struct.calcsize(STRUCT_FORMAT)  # == 88
VERSION_INDEX = 15
VERSION_OFFSET = struct.calcsize("<fffffIfffffffII")
_VERSION_SIZE = struct.calcsize("<I")
_VERSION_END = VERSION_OFFSET + _VERSION_SIZE
SHM_NAME = "G3D_Settings"
_UINT32_MAX = 0xFFFF_FFFF

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
    display_backend: int = 0
    depth_mode: int = 3
    stereo_layout: int = 0
    eye_order: int = 0
    panel_width_px: int = 0
    panel_height_px: int = 0
    focus_plane_cm: float = 0.0
    tracking_mode: int = 0


def _finite_float(value: object, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(
            f"{field_name} must be a finite float"
        ) from error
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be a finite float")
    return parsed


def _uint32(value: object, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(
            f"{field_name} must be an unsigned 32-bit integer"
        ) from error
    if not 0 <= parsed <= _UINT32_MAX:
        raise ValueError(
            f"{field_name} must be an unsigned 32-bit integer"
        )
    return parsed


def _settings_versions(current_version: int) -> tuple[int, int]:
    """Return the odd writing marker and following even committed version."""
    writing_version = (int(current_version) + 1) | 1
    committed_version = (writing_version + 1) & 0xFFFF_FFFE
    return writing_version & _UINT32_MAX, committed_version


def _pack_settings(s: OverlaySettings, committed_version: int) -> bytes:
    """Validate and pack a complete even-version snapshot before publication."""
    return struct.pack(
        STRUCT_FORMAT,
        _finite_float(s.strength_x, "strength_x"),
        _finite_float(s.strength_y, "strength_y"),
        _finite_float(s.virtual_depth_cm, "virtual_depth_cm"),
        _finite_float(s.screen_w_cm, "screen_w_cm"),
        _finite_float(s.screen_h_cm, "screen_h_cm"),
        _uint32(s.depth_curve, "depth_curve"),
        _finite_float(s.depth_gamma, "depth_gamma"),
        _finite_float(s.focus_radius, "focus_radius"),
        _finite_float(s.head_dist_cm, "head_dist_cm"),
        _finite_float(s.camera_fov_deg, "camera_fov_deg"),
        _finite_float(s.ipd_mm, "ipd_mm"),
        _finite_float(s.smoothing_alpha, "smoothing_alpha"),
        _finite_float(s.deadzone_mm, "deadzone_mm"),
        _uint32(s.display_backend, "display_backend"),
        _uint32(s.depth_mode, "depth_mode"),
        _uint32(committed_version, "version"),
        _uint32(s.stereo_layout, "stereo_layout"),
        _uint32(s.eye_order, "eye_order"),
        _uint32(s.panel_width_px, "panel_width_px"),
        _uint32(s.panel_height_px, "panel_height_px"),
        _finite_float(s.focus_plane_cm, "focus_plane_cm"),
        _uint32(s.tracking_mode, "tracking_mode"),
    )


class SharedSettingsWriter:
    """Creates and owns the G3D_Settings shared memory segment."""

    def __init__(self, name: str = SHM_NAME) -> None:
        self._name = name
        self._handle: int | None = None
        self._view: int | None = None
        self._version: int = 0
        self._write_lock = threading.Lock()

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
        with self._write_lock:
            view = self._view
            if view is None:
                raise RuntimeError("write() called after close()")
            writing_version, committed_version = _settings_versions(
                self._version
            )
            # Build the complete snapshot before marking the mapping odd. A bad
            # UI/config value can therefore raise without making the last valid
            # settings unreadable.
            data = _pack_settings(s, committed_version)
            writing_marker = struct.pack("<I", writing_version)
            committed_marker = data[VERSION_OFFSET:_VERSION_END]

            # The version lives in the middle of the ABI. Copying the complete
            # even-version struct in one memmove could expose that even marker
            # before the trailing stereo/panel/tracking fields were installed.
            # Keep the marker odd while both body slices are copied, then commit
            # the even version in the final aligned four-byte store.
            ctypes.memmove(
                view + VERSION_OFFSET,
                writing_marker,
                _VERSION_SIZE,
            )
            ctypes.memmove(view, data[:VERSION_OFFSET], VERSION_OFFSET)
            ctypes.memmove(
                view + _VERSION_END,
                data[_VERSION_END:],
                STRUCT_SIZE - _VERSION_END,
            )
            ctypes.memmove(
                view + VERSION_OFFSET,
                committed_marker,
                _VERSION_SIZE,
            )
            self._version = committed_version

    def close(self) -> None:
        with self._write_lock:
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
            f = None
            for _ in range(4):
                first = bytes(raw)
                first_version = struct.unpack_from(
                    "<I", first, VERSION_OFFSET
                )[0]
                if first_version & 1:
                    continue
                second = bytes(raw)
                second_version = struct.unpack_from(
                    "<I", second, VERSION_OFFSET
                )[0]
                if (
                    first_version == second_version
                    and not (second_version & 1)
                ):
                    f = struct.unpack(STRUCT_FORMAT, second)
                    break
            if f is None:
                return None
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
            display_backend=f[13],
            depth_mode=f[14],
            stereo_layout=f[16],
            eye_order=f[17],
            panel_width_px=f[18],
            panel_height_px=f[19],
            focus_plane_cm=f[20],
            tracking_mode=f[21],
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
