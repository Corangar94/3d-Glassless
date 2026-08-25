"""Spawn and supervise the Glassless3D overlay executable.

The overlay is a standalone D3D11 process that:
  * captures the desktop (DXGI Output Duplication),
  * runs Depth Anything V2 inference on each frame,
  * reads head position from the `G3D` shared-memory segment (written by the
    tracker thread) and live tuning from the `G3D_Settings` shared-memory
    segment (written by the settings GUI),
  * composites a parallax-warped layer on top of the desktop.

The launcher owns its lifetime: start it when tracking starts, terminate it
when tracking stops or the launcher window closes.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Any, Optional

OVERLAY_EXE_NAME = "Glassless3DOverlay.exe"
DEPTH_MODEL_RELS = (
    os.path.join("models", "video_depth_anything_vits_518.onnx"),
    os.path.join("models", "depth_anything_v2_small_fp16.onnx"),
)
DEPTH_MODEL_REL = DEPTH_MODEL_RELS[-1]  # backward-compatible display path
RUNTIME_DLL_NAMES = ("onnxruntime.dll", "DirectML.dll")


def _project_root() -> Path:
    """Return the source root or PyInstaller runtime-content directory."""
    if getattr(sys, "frozen", False):
        extraction_root = getattr(sys, "_MEIPASS", None)
        if extraction_root:
            return Path(extraction_root).resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def find_overlay_exe() -> Optional[Path]:
    """Locate the overlay executable, or return None if not found.

    Search order:
      1. `<project_root>/Glassless3DOverlay.exe` (normal/frozen runtime layout)
      2. `<project_root>/overlay/build_mingw/Glassless3DOverlay.exe` (dev)
      3. `<project_root>/overlay/build/Release/Glassless3DOverlay.exe` (MSVC dev)
    """
    root = _project_root()
    candidates = [
        root / OVERLAY_EXE_NAME,
        root / "overlay" / "build_mingw" / OVERLAY_EXE_NAME,
        root / "overlay" / "build" / "Release" / OVERLAY_EXE_NAME,
        root / "overlay" / "build" / OVERLAY_EXE_NAME,
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def find_depth_model() -> Optional[Path]:
    """Locate the preferred supported depth model, or return None."""
    root = _project_root()
    for relative in DEPTH_MODEL_RELS:
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def missing_overlay_runtime_assets(exe: Path) -> list[str]:
    """Return human-readable assets required before the overlay may start."""
    root = _project_root()
    missing: list[str] = []
    if find_depth_model() is None:
        missing.append("models/<Video Depth Anything or Depth Anything V2 ONNX model>")
    for name in RUNTIME_DLL_NAMES:
        candidates = (exe.parent / name, root / name)
        if not any(candidate.is_file() for candidate in candidates):
            missing.append(name)
    return missing


class OverlayStartError(RuntimeError):
    """Raised when we can't even attempt to launch the overlay."""


def _normalized_windows_path(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _retire_stale_overlay_instances(exe: Path, timeout_ms: int = 1500) -> int:
    """Close orphaned copies of this exact overlay binary before spawning.

    A previous launcher crash can leave the detached native child alive. Its
    global mutex then makes the correctly targeted replacement exit while the
    old desktop-only overlay remains visible. Match the full image path, ask its
    top-level window to close, then force only that exact binary after a bounded
    timeout.
    """

    if sys.platform != "win32":
        return 0
    windll = getattr(ctypes, "WinDLL")
    kernel32: Any = windll("kernel32", use_last_error=True)
    user32: Any = windll("user32", use_last_error=True)

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESSENTRY32W),
    ]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESSENTRY32W),
    ]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL

    TH32CS_SNAPPROCESS = 0x00000002
    PROCESS_TERMINATE = 0x0001
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SYNCHRONIZE = 0x00100000
    WAIT_TIMEOUT = 0x00000102
    WM_CLOSE = 0x0010
    target_path = _normalized_windows_path(exe)
    target_name = exe.name.casefold()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if not snapshot or int(snapshot) == invalid_handle:
        return 0

    matching: list[tuple[int, wintypes.HANDLE]] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        has_entry = bool(kernel32.Process32FirstW(snapshot, ctypes.byref(entry)))
        while has_entry:
            pid = int(entry.th32ProcessID)
            if pid != os.getpid() and entry.szExeFile.casefold() == target_name:
                handle = kernel32.OpenProcess(
                    PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE,
                    False,
                    pid,
                )
                if handle:
                    path_buffer = ctypes.create_unicode_buffer(32768)
                    path_length = wintypes.DWORD(len(path_buffer))
                    if kernel32.QueryFullProcessImageNameW(
                        handle, 0, path_buffer, ctypes.byref(path_length)
                    ) and _normalized_windows_path(path_buffer.value) == target_path:
                        matching.append((pid, handle))
                    else:
                        kernel32.CloseHandle(handle)
            has_entry = bool(kernel32.Process32NextW(snapshot, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(snapshot)

    if not matching:
        return 0
    pids = {pid for pid, _handle in matching}
    callback_type = getattr(ctypes, "WINFUNCTYPE")(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL

    @callback_type
    def close_owned_window(hwnd: int, _lparam: int) -> bool:
        window_pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if int(window_pid.value) in pids:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        return True

    user32.EnumWindows(close_owned_window, 0)
    retired = 0
    for _pid, handle in matching:
        try:
            if kernel32.WaitForSingleObject(handle, timeout_ms) == WAIT_TIMEOUT:
                kernel32.TerminateProcess(handle, 1)
                kernel32.WaitForSingleObject(handle, 1000)
            retired += 1
        finally:
            kernel32.CloseHandle(handle)
    return retired


class OverlayProcess:
    """Lifecycle wrapper around the overlay subprocess.

    Not thread-safe; call from a single thread (the Qt main thread).
    """

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._exe_path: Optional[Path] = None
        self._target_executable: Optional[str] = None
        self._target_pid: Optional[int] = None
        self._lock = threading.RLock()
        self._request_generation = 0
        self._desired_running = False
        self._worker_running = False
        self._restart_requested = False

    def start(
        self,
        target_executable: Optional[str] = None,
        *,
        target_pid: Optional[int] = None,
    ) -> Path:
        """Launch the overlay. Returns the path of the executable actually spawned."""
        normalized_target = (
            target_executable.strip() or None
            if target_executable is not None
            else self._target_executable
        )
        normalized_pid = target_pid if target_pid is not None and target_pid > 0 else None
        with self._lock:
            self._desired_running = True
            self._target_executable = normalized_target
            self._target_pid = normalized_pid
            if self._worker_running:
                exe = find_overlay_exe()
                if exe is None:
                    raise OverlayStartError(f"{OVERLAY_EXE_NAME} not found")
                return exe
            if self._proc is not None and self._proc.poll() is None:
                assert self._exe_path is not None
                return self._exe_path

        exe, proc = self._spawn(normalized_target, normalized_pid)
        with self._lock:
            self._proc = proc
            self._exe_path = exe
        return exe

    def _spawn(
        self, target_executable: Optional[str], target_pid: Optional[int] = None
    ) -> tuple[Path, subprocess.Popen[bytes]]:
        """Create one process; caller serializes it against retirement."""
        exe = find_overlay_exe()
        if exe is None:
            raise OverlayStartError(
                f"{OVERLAY_EXE_NAME} not found. The desktop overlay is the primary "
                "runtime path. Run `python scripts/bootstrap.py` to build it."
            )
        missing = missing_overlay_runtime_assets(exe)
        if missing:
            raise OverlayStartError(
                "The overlay runtime is incomplete. Missing: " + ", ".join(missing)
            )
        _retire_stale_overlay_instances(exe)
        command = [str(exe)]
        if target_pid is not None:
            command.extend(["--target-pid", str(target_pid)])
        elif target_executable:
            command.extend(["--target-exe", target_executable])
        try:
            proc = subprocess.Popen(command, cwd=str(exe.parent))
        except OSError as error:
            raise OverlayStartError(f"Could not launch {exe}: {error}") from error
        return exe, proc

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def is_transitioning(self) -> bool:
        with self._lock:
            return self._worker_running or self._restart_requested

    def stop(self) -> None:
        with self._lock:
            self._desired_running = False
            self._restart_requested = False
            proc = self._proc
            self._proc = None
            self._exe_path = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)

    def stop_async(self) -> None:
        self._request_transition(restart=False)

    def restart_async(
        self,
        target_executable: Optional[str] = None,
        *,
        target_pid: Optional[int] = None,
    ) -> None:
        with self._lock:
            if target_executable is not None:
                self._target_executable = target_executable.strip() or None
            if target_pid is not None:
                self._target_pid = target_pid if target_pid > 0 else None
            self._desired_running = True
            self._restart_requested = True
        self._request_transition(restart=True)

    def _request_transition(self, *, restart: bool) -> None:
        with self._lock:
            self._request_generation += 1
            generation = self._request_generation
            if not restart:
                self._desired_running = False
                self._restart_requested = False
            if self._worker_running:
                return
            self._worker_running = True
        threading.Thread(
            target=self._transition_worker,
            args=(generation,),
            name="g3d-overlay-lifecycle",
            daemon=True,
        ).start()

    def _transition_worker(self, generation: int) -> None:
        try:
            while True:
                with self._lock:
                    proc = self._proc
                    self._proc = None
                    self._exe_path = None
                    desired_running = self._desired_running
                    restart_requested = self._restart_requested
                    target_executable = self._target_executable
                    target_pid = self._target_pid
                    current_generation = self._request_generation
                    self._restart_requested = False
                if proc is not None and proc.poll() is None:
                    try:
                        proc.terminate()
                        proc.wait(timeout=3.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=2.0)
                    except OSError:
                        pass
                if desired_running and (restart_requested or proc is not None):
                    try:
                        exe, replacement = self._spawn(target_executable, target_pid)
                    except OverlayStartError:
                        replacement = None
                        exe = None
                    with self._lock:
                        if replacement is not None and self._desired_running:
                            self._proc = replacement
                            self._exe_path = exe
                with self._lock:
                    if current_generation == self._request_generation:
                        break
                    generation = self._request_generation
        finally:
            with self._lock:
                self._worker_running = False
                rerun = generation != self._request_generation
            if rerun:
                self._request_transition(restart=self._desired_running)
