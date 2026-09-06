"""Per-launcher-run internal transport identity; legacy names are opt-in fallback.

A random namespace prevents another ordinary tracker process from satisfying an
owned child's liveness checks. It is isolation, not authentication against an
attacker with the same Windows account and process-inspection privileges.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import os
import re

SESSION_ENV = "G3D_TRACKING_SESSION"
_CHANNELS = frozenset({"G3D", "G3D_State", "G3D_PoseV2", "G3D_TrackerBackendV1"})


def validate_session(value: str | None) -> str | None:
    if value is not None and (not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None):
        raise ValueError("Tracking session must be a 32-character lowercase hex nonce")
    return value


def tracking_session() -> str | None:
    return validate_session(os.environ.get(SESSION_ENV))


def channel_name(name: str, session: str | None = None) -> str:
    nonce = validate_session(session) if session is not None else tracking_session()
    if nonce is None or name not in _CHANNELS:
        return name
    return "Local\\Glassless3D_" + nonce + "_" + name


def child_environment(session: str | None) -> dict[str, str]:
    env = os.environ.copy()
    validated = validate_session(session)
    if validated is None:
        env.pop(SESSION_ENV, None)
    else:
        env[SESSION_ENV] = validated
    return env


class ProducerLease:
    """One production writer process per namespace; handles close on crashes.

    This is an existence lease, not a thread-owned mutex: cleanup can run on a
    different thread. Never open a second writer and reset another's sequence.
    """
    def __init__(self) -> None:
        self._handle = None
        if os.name != "nt":
            raise OSError("Production tracker ownership requires Windows")
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self._kernel.CreateMutexW.restype = wintypes.HANDLE
        self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel.CloseHandle.restype = wintypes.BOOL
        name = "Local\\Glassless3D_Producer_" + (tracking_session() or "legacy")
        ctypes.set_last_error(0)
        handle = self._kernel.CreateMutexW(None, False, name)
        error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(error)
        if error == 183:
            self._kernel.CloseHandle(handle)
            raise RuntimeError("Another tracker already owns this producer namespace")
        self._handle = handle

    def close(self) -> None:
        if self._handle is not None:
            self._kernel.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
