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
from pathlib import Path
from typing import Optional

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


class OverlayProcess:
    """Lifecycle wrapper around the overlay subprocess.

    Not thread-safe; call from a single thread (the Qt main thread).
    """

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._exe_path: Optional[Path] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> Path:
        """Launch the overlay. Returns the path of the exe actually spawned.

        Raises OverlayStartError if the binary is missing or launch fails.
        A missing model is only a warning; the overlay has a fallback path
        (1x1 zero depth texture) so it can still run at flat depth.
        """
        if self.is_running():
            assert self._exe_path is not None
            return self._exe_path

        exe = find_overlay_exe()
        if exe is None:
            raise OverlayStartError(
                f"{OVERLAY_EXE_NAME} not found. The desktop overlay is the primary "
                "runtime path. Run `python scripts/bootstrap.py` to build it."
            )

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
            self._proc = subprocess.Popen(
                [str(exe)],
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
        self._exe_path = exe
        return exe

    def stop(self, timeout: float = 3.0) -> None:
        """Terminate the overlay. Safe to call if already stopped."""
        proc = self._proc
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
        self._proc = None

    # ── Status ────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def poll_exit_code(self) -> Optional[int]:
        """Return exit code if the overlay has quit, else None."""
        return None if self._proc is None else self._proc.poll()
