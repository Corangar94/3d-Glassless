"""Discover user-facing top-level Windows game candidates.

The launcher uses this as an explicit live-window picker.  HWND values are
never persisted: they are transient, while the executable path remains the
durable profile identity and PID narrows only the current launch.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunningGameWindow:
    title: str
    executable_path: str
    pid: int
    hwnd: int
    window_class: str = ""

    @property
    def label(self) -> str:
        return f"{self.title} — {Path(self.executable_path).name} (PID {self.pid})"


_EXCLUDED_EXECUTABLES = {
    "applicationframehost.exe",
    "dwm.exe",
    "explorer.exe",
    "glassless3doverlay.exe",
    "lockapp.exe",
    "searchhost.exe",
    "shellexperiencehost.exe",
    "startmenuexperiencehost.exe",
    "textinputhost.exe",
}


def discover_running_game_windows() -> list[RunningGameWindow]:
    """Return viable visible top-level windows, best candidate first per exe."""

    if sys.platform != "win32":
        return []

    windll = getattr(ctypes, "WinDLL")
    user32: Any = windll("user32", use_last_error=True)
    kernel32: Any = windll("kernel32", use_last_error=True)
    try:
        dwmapi: Any | None = windll("dwmapi", use_last_error=True)
    except OSError:
        dwmapi = None

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    ]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    GWL_STYLE = -16
    GWL_EXSTYLE = -20
    WS_CHILD = 0x40000000
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    DWMWA_CLOAKED = 14
    GA_ROOTOWNER = 3
    current_pid = os.getpid()
    foreground = int(user32.GetForegroundWindow() or 0)
    ranked: dict[str, tuple[int, RunningGameWindow]] = {}

    callback_type = getattr(ctypes, "WINFUNCTYPE")(
        ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p
    )
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    if dwmapi is not None:
        dwmapi.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

    @callback_type
    def visit(hwnd_raw: int, _lparam: int) -> bool:
        hwnd = int(hwnd_raw or 0)
        if not hwnd or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        style = int(user32.GetWindowLongPtrW(hwnd, GWL_STYLE))
        ex_style = int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
        if style & WS_CHILD or ex_style & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE):
            return True
        if dwmapi is not None:
            cloaked = ctypes.c_uint(0)
            if dwmapi.DwmGetWindowAttribute(
                hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
            ) == 0 and cloaked.value:
                return True

        rect = RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return True
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 320 or height < 240:
            return True

        title_length = int(user32.GetWindowTextLengthW(hwnd))
        if title_length <= 0:
            return True
        title_buffer = ctypes.create_unicode_buffer(title_length + 1)
        user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
        title = title_buffer.value.strip()
        if not title:
            return True

        pid_value = ctypes.c_uint(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_value))
        pid = int(pid_value.value)
        if not pid or pid == current_pid:
            return True

        process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not process:
            return True
        try:
            path_buffer = ctypes.create_unicode_buffer(32768)
            path_length = ctypes.c_uint(len(path_buffer))
            if not kernel32.QueryFullProcessImageNameW(
                process, 0, path_buffer, ctypes.byref(path_length)
            ):
                return True
            executable_path = path_buffer.value
        finally:
            kernel32.CloseHandle(process)

        if Path(executable_path).name.casefold() in _EXCLUDED_EXECUTABLES:
            return True
        class_buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
        candidate = RunningGameWindow(
            title=title,
            executable_path=executable_path,
            pid=pid,
            hwnd=hwnd,
            window_class=class_buffer.value,
        )
        score = width * height
        if int(user32.GetAncestor(hwnd, GA_ROOTOWNER) or 0) == hwnd:
            score += 1 << 61
        if foreground == hwnd:
            score += 1 << 62
        key = os.path.normcase(os.path.abspath(executable_path))
        previous = ranked.get(key)
        if previous is None or score > previous[0]:
            ranked[key] = (score, candidate)
        return True

    user32.EnumWindows(visit, 0)
    return [
        candidate
        for _score, candidate in sorted(
            ranked.values(), key=lambda entry: (-entry[0], entry[1].title.casefold())
        )
    ]
