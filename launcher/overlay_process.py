"""Spawn and supervise the Glassless3D overlay executable.

The overlay is a standalone D3D11 process that:
  * captures the desktop (DXGI Output Duplication),
  * runs Depth Anything V2 inference on each frame,
  * reads head position from the `G3D` shared-memory segment (written by the
    tracker thread) and live tuning from `G3D_Settings` (written by the
    settings GUI),
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
DEPTH_MODEL_REL = os.path.join("models", "depth_anything_v2_small_fp16.onnx")


def _project_root() -> Path:
    """Return the project root (parent of the `launcher` package)."""
    return Path(__file__).resolve().parent.parent


def find_overlay_exe() -> Optional[Path]:
    """Locate the overlay executable, or return None if not found.

    Search order:
      1. `<project_root>/Glassless3DOverlay.exe` (normal install layout)
      2. `<project_root>/overlay/build_mingw/Glassless3DOverlay.exe` (dev)
      3. `<project_root>/overlay/build/Glassless3DOverlay.exe` (MSVC dev)
    """
    root = _project_root()
    candidates = [
        root / OVERLAY_EXE_NAME,
        root / "overlay" / "build_mingw" / OVERLAY_EXE_NAME,
        root / "overlay" / "build" / "Release" / OVERLAY_EXE_NAME,
        root / "overlay" / "build" / OVERLAY_EXE_NAME,
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def find_depth_model() -> Optional[Path]:
    """Locate the Depth Anything V2 ONNX model, or return None."""
    root = _project_root()
    p = root / DEPTH_MODEL_REL
    return p if p.is_file() else None


class OverlayStartError(RuntimeError):
    """Raised when we can't even attempt to launch the overlay."""


def _normalized_windows_path(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _retire_stale_overlay_instances(exe: Path, timeout_ms: int = 1500) -> int:
    """Close orphaned copies of this exact overlay binary before spawning.

    A previous launcher crash can leave the detached native child alive.  Its
    global mutex then makes the correctly targeted replacement exit while the
    old desktop-only overlay remains visible.  Match the full image path, ask
    its top-level window to close, then force only that exact binary after a
    bounded timeout.
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

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(
        self,
        target_executable: Optional[str] = None,
        *,
        target_pid: Optional[int] = None,
    ) -> Path:
        """Launch the overlay. Returns the path of the exe actually spawned.

        Raises OverlayStartError if the binary is missing or launch fails.
        A missing model is only a warning; the overlay has a fallback path
        (1x1 zero depth texture) so it can still run at flat depth.
        """
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
                # A previous process is still being reaped.  The worker will
                # reconcile this newest desired target before it exits.
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

        _retire_stale_overlay_instances(exe)

        # Model absence is non-fatal — note it but continue.
        model = find_depth_model()
        if model is None:
            print(
                f"[overlay] WARNING: depth model not found at "
                f"{DEPTH_MODEL_REL}. The overlay will still start, but only with "
                "flat fallback depth until the model is restored.",
                file=sys.stderr,
            )

        # CWD = project root so the overlay's <exe>/models/ search hits.
        cwd = str(_project_root())
        creationflags = 0
        if sys.platform == "win32":
            # Detach from console and make the process killable via terminate().
            creationflags = (
                subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            )

        try:
            args = [str(exe)]
            if target_executable:
                args.extend(["--target-exe", target_executable])
            if target_pid is not None:
                args.extend(["--target-pid", str(target_pid)])
            proc = subprocess.Popen(
                args,
                cwd=cwd,
                creationflags=creationflags,
                # Inherit stdout/stderr so the overlay's diagnostic prints show up
                # in the launcher console when run from a terminal.
            )
        except OSError as e:
            raise OverlayStartError(
                "Failed to start the desktop overlay runtime. Run "
                "`python scripts/bootstrap.py` to rebuild it, then try again. "
                f"Original error: {e}"
            ) from e
        return exe, proc

    def stop(self, timeout: float = 3.0) -> None:
        """Terminate the overlay. Safe to call if already stopped."""
        with self._lock:
            self._request_generation += 1
            self._desired_running = False
            self._restart_requested = False
            proc = self._proc
            self._proc = None
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
            except OSError:
                # Process is already gone.
                pass

    def stop_async(self, timeout: float = 3.0) -> None:
        """Coalesce to stopped and reap outside the Qt GUI thread."""
        self._request_transition(False, None, None, timeout, restart=False)

    def restart_async(
        self,
        target_executable: Optional[str] = None,
        timeout: float = 3.0,
        *,
        target_pid: Optional[int] = None,
    ) -> None:
        """Restart once, after the prior process has fully released capture."""
        target = (
            target_executable.strip() or None
            if target_executable is not None
            else self._target_executable
        )
        pid = target_pid if target_pid is not None and target_pid > 0 else None
        self._request_transition(True, target, pid, timeout, restart=True)

    def _request_transition(
        self,
        desired_running: bool,
        target: Optional[str],
        target_pid: Optional[int],
        timeout: float,
        *,
        restart: bool,
    ) -> None:
        with self._lock:
            self._request_generation += 1
            self._desired_running = desired_running
            if desired_running:
                self._target_executable = target
                self._target_pid = target_pid
            self._restart_requested = self._restart_requested or restart
            if self._worker_running:
                return
            self._worker_running = True
        threading.Thread(
            target=self._reconcile_lifecycle,
            args=(timeout,),
            name="g3d-overlay-lifecycle",
            # A detached native overlay must never outlive the launcher just
            # because Python began interpreter shutdown while cleanup was in
            # progress. Non-daemon lifecycle workers finish their bounded reap.
            daemon=False,
        ).start()

    @staticmethod
    def _reap(proc: subprocess.Popen[bytes], timeout: float) -> None:
        try:
            proc.terminate()
        except OSError:
            return
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        except OSError:
            pass

    def _reconcile_lifecycle(self, timeout: float) -> None:
        """Single serialized worker implementing latest-request-wins semantics."""
        while True:
            with self._lock:
                generation = self._request_generation
                desired = self._desired_running
                target = self._target_executable
                target_pid = self._target_pid
                proc = self._proc
                must_restart = self._restart_requested
                if proc is not None and proc.poll() is not None:
                    self._proc = None
                    proc = None
                if proc is not None and (not desired or must_restart):
                    self._proc = None
                    self._restart_requested = False
                else:
                    proc = None

            if proc is not None:
                self._reap(proc, timeout)
                continue

            with self._lock:
                if generation != self._request_generation:
                    continue
                desired = self._desired_running
                target = self._target_executable
                target_pid = self._target_pid
                existing = self._proc
                if existing is not None and existing.poll() is None:
                    self._worker_running = False
                    return
                if not desired:
                    self._worker_running = False
                    return

            try:
                exe, spawned = self._spawn(target, target_pid)
            except OverlayStartError:
                with self._lock:
                    if generation == self._request_generation:
                        self._desired_running = False
                        self._worker_running = False
                return

            with self._lock:
                self._proc = spawned
                self._exe_path = exe
                if generation == self._request_generation and self._desired_running:
                    self._restart_requested = False
                    self._worker_running = False
                    return
                # Desired state changed during spawn.  Keep the process visible
                # to the next loop so it is retired before any replacement.

    # ── Status ────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def is_transitioning(self) -> bool:
        with self._lock:
            return self._worker_running

    def poll_exit_code(self) -> Optional[int]:
        """Return exit code if the overlay has quit, else None."""
        return None if self._proc is None else self._proc.poll()
