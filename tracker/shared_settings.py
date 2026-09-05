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

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import math
import struct
import threading
from typing import Iterator

STRUCT_FORMAT = "<fffffIfffffffIII" "IIIIfI"
STRUCT_SIZE = struct.calcsize(STRUCT_FORMAT)  # == 88
SMOOTHING_ALPHA_INDEX = 11
SMOOTHING_ALPHA_OFFSET = struct.calcsize("<fffffIfffff")
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
_WAIT_OBJECT_0 = 0x00000000
_WAIT_ABANDONED = 0x00000080
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF
_WRITE_MUTEX_TIMEOUT_MS = 1000

_k32 = ctypes.windll.kernel32
_k32.CreateFileMappingW.restype = ctypes.c_void_p
_k32.OpenFileMappingW.restype = ctypes.c_void_p
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.CreateMutexW.restype = ctypes.c_void_p
_k32.WaitForSingleObject.restype = ctypes.c_ulong
_k32.ReleaseMutex.restype = ctypes.c_int
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


def _normalized_committed_version(marker: int) -> int:
    """Return an even base after a clean commit or abandoned odd write."""
    value = int(marker) & _UINT32_MAX
    if value & 1:
        return (value + 1) & 0xFFFF_FFFE
    return value


def _mapping_version(view: int) -> int:
    return ctypes.c_uint32.from_address(view + VERSION_OFFSET).value


def _mapping_has_payload(view: int) -> bool:
    raw = (ctypes.c_ubyte * STRUCT_SIZE).from_address(view)
    return any(raw)


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


@contextmanager
def _acquired_write_mutex(
    handle: int,
    name: str,
) -> Iterator[None]:
    result = int(
        _k32.WaitForSingleObject(handle, _WRITE_MUTEX_TIMEOUT_MS)
    )
    if result not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
        if result == _WAIT_TIMEOUT:
            raise TimeoutError(
                f"timed out coordinating shared-settings writer {name!r}"
            )
        if result == _WAIT_FAILED:
            raise ctypes.WinError(ctypes.get_last_error())
        raise OSError(
            f"unexpected shared-settings mutex result 0x{result:08x}"
        )
    try:
        yield
    except BaseException:
        # Preserve the publication/validation exception. Release failure is less
        # actionable and must not replace the original cause.
        _k32.ReleaseMutex(handle)
        raise
    else:
        if not _k32.ReleaseMutex(handle):
            raise ctypes.WinError(ctypes.get_last_error())


class SharedSettingsWriter:
    """Creates or joins the coordinated G3D_Settings writer channel.

    All updated writers serialize publication through a named kernel mutex and
    derive the next version from the mapping itself. A newly attached writer
    therefore preserves an existing committed snapshot instead of resetting it
    to defaults, and multiple writer processes cannot interleave body slices or
    reuse the same even version.
    """

    def __init__(self, name: str = SHM_NAME) -> None:
        self._name = name
        self._handle: int | None = None
        self._view: int | None = None
        self._mutex_handle: int | None = None
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
            error = ctypes.get_last_error()
            _k32.CloseHandle(self._handle)
            self._handle = None
            raise ctypes.WinError(error)
        self._mutex_handle = _k32.CreateMutexW(
            None,
            False,
            f"{name}_WriteMutex",
        )
        if self._mutex_handle is None:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

        try:
            with self._process_write_guard():
                self._initialize_mapping_locked(self._view)
        except BaseException:
            self.close()
            raise

    @contextmanager
    def _process_write_guard(self) -> Iterator[None]:
        handle = getattr(self, "_mutex_handle", None)
        if handle is None:
            # Focused direct tests and historical subclasses that build a bare
            # writer keep the original in-process locking behavior.
            yield
            return
        with _acquired_write_mutex(handle, self._name):
            yield

    def _publish_locked(
        self,
        settings: OverlaySettings,
        base_version: int,
    ) -> None:
        view = self._view
        if view is None:
            raise RuntimeError("write() called after close()")
        writing_version, committed_version = _settings_versions(
            base_version
        )
        # Build the complete snapshot before marking the mapping odd. A bad
        # UI/config value can therefore raise without making the last valid
        # settings unreadable.
        data = _pack_settings(settings, committed_version)
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

    def _initialize_mapping_locked(self, view: int) -> None:
        marker = _mapping_version(view)
        if not (marker & 1) and (marker != 0 or _mapping_has_payload(view)):
            # Existing committed channel, including the legitimate uint32
            # rollover snapshot at version zero. Do not inject defaults between
            # a surviving reader and the caller's first real write.
            self._version = marker
            return

        # A zero-filled new mapping or an abandoned odd transaction has no
        # reliable committed payload. Publish one complete default snapshot so
        # readers never accept zeroed physical settings as a valid state.
        self._publish_locked(
            OverlaySettings(),
            _normalized_committed_version(marker),
        )

    def write(self, settings: OverlaySettings) -> None:
        with self._write_lock:
            view = self._view
            if view is None:
                raise RuntimeError("write() called after close()")
            with self._process_write_guard():
                # Coordinated writers use the mapping as the source of truth.
                # Bare historical/test writers retain their local version.
                if getattr(self, "_mutex_handle", None) is None:
                    base_version = self._version
                else:
                    base_version = _normalized_committed_version(
                        _mapping_version(view)
                    )
                self._publish_locked(settings, base_version)

    def close(self) -> None:
        with self._write_lock:
            if self._view is not None:
                _k32.UnmapViewOfFile(self._view)
                self._view = None
            if self._handle is not None:
                _k32.CloseHandle(self._handle)
                self._handle = None
            if self._mutex_handle is not None:
                _k32.CloseHandle(self._mutex_handle)
                self._mutex_handle = None

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

    def _detach(self) -> None:
        view = self._view
        handle = self._handle
        self._view = None
        self._handle = None
        if view is not None:
            _k32.UnmapViewOfFile(view)
        if handle is not None:
            _k32.CloseHandle(handle)

    def _try_attach(self) -> None:
        if self._view is not None:
            return
        if self._handle is None:
            self._handle = _k32.OpenFileMappingW(
                _FILE_MAP_READ,
                False,
                self._name,
            )
            if self._handle is None:
                return  # writer not running yet
        self._view = _k32.MapViewOfFile(
            self._handle, _FILE_MAP_READ, 0, 0, STRUCT_SIZE,
        )
        if self._view is None:
            # Map failed; close handle so next call retries cleanly.
            _k32.CloseHandle(self._handle)
            self._handle = None

    def read_smoothing_alpha(self) -> tuple[int, float] | None:
        """Read only ``(version, smoothing_alpha)`` under the existing seqlock.

        This projection is for the packaged live-filter poller. It avoids two
        88-byte copies, a complete struct unpack, and ``OverlaySettings``
        construction when only the producer Kalman noise is needed.
        """
        self._try_attach()
        view = self._view
        if view is None:
            return None
        try:
            for _ in range(4):
                first_version = ctypes.c_uint32.from_address(
                    view + VERSION_OFFSET
                ).value
                if first_version & 1:
                    continue
                smoothing_alpha = ctypes.c_float.from_address(
                    view + SMOOTHING_ALPHA_OFFSET
                ).value
                second_version = ctypes.c_uint32.from_address(
                    view + VERSION_OFFSET
                ).value
                if (
                    first_version == second_version
                    and not (second_version & 1)
                ):
                    return second_version, float(smoothing_alpha)
            return None
        except (OSError, ValueError):
            self._detach()
            return None

    def read(self) -> OverlaySettings | None:
        """Return current settings snapshot, or None if writer not running."""
        self._try_attach()
        if self._view is None:
            return None
        try:
            raw = (ctypes.c_char * STRUCT_SIZE).from_address(self._view)
            fields = None
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
                    fields = struct.unpack(STRUCT_FORMAT, second)
                    break
            if fields is None:
                return None
        except (OSError, ValueError):
            self._detach()
            return None
        return OverlaySettings(
            strength_x=fields[0], strength_y=fields[1],
            virtual_depth_cm=fields[2],
            screen_w_cm=fields[3], screen_h_cm=fields[4],
            depth_curve=fields[5],
            depth_gamma=fields[6], focus_radius=fields[7],
            head_dist_cm=fields[8], camera_fov_deg=fields[9],
            ipd_mm=fields[10], smoothing_alpha=fields[11],
            deadzone_mm=fields[12],
            display_backend=fields[13],
            depth_mode=fields[14],
            stereo_layout=fields[16],
            eye_order=fields[17],
            panel_width_px=fields[18],
            panel_height_px=fields[19],
            focus_plane_cm=fields[20],
            tracking_mode=fields[21],
        )

    def close(self) -> None:
        self._detach()

    def __enter__(self) -> "SharedSettingsReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
