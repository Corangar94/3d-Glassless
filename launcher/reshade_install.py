"""Copy bundled ReShade assets into a game directory."""
from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Generator


class InstallError(Exception):
    """Raised when an installation step fails."""

    def __init__(self, step: str, reason: str) -> None:
        super().__init__(f"{step}: {reason}")
        self.step = step
        self.reason = reason


def _bundle_dir() -> str:
    """Return the directory containing bundled assets.

    In a PyInstaller one-file build, sys._MEIPASS points to the temp
    extraction dir. In development, fall back to the project root.
    """
    if hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def install_steps(
    game_dir: str, profile_name: str = "wow"
) -> Generator[str, None, None]:
    """Copy bundled assets into game_dir, yielding each step name on success.

    Raises InstallError if any step fails.
    """
    base = _bundle_dir()

    # Step 1: ReShade DLL
    src_dll = os.path.join(base, "ReShade64.dll")
    dst_dll = os.path.join(game_dir, "d3d11.dll")
    try:
        shutil.copy2(src_dll, dst_dll)
    except OSError as e:
        raise InstallError("Copying ReShade", str(e))
    yield "Copying ReShade"

    # Step 2: Shaders
    shader_dir = os.path.join(game_dir, "reshade-shaders", "Shaders")
    try:
        os.makedirs(shader_dir, exist_ok=True)
        for fname in ("Glassless3D.fx", "Glassless3D.fxh"):
            shutil.copy2(
                os.path.join(base, "shaders", fname),
                os.path.join(shader_dir, fname),
            )
    except OSError as e:
        raise InstallError("Copying shaders", str(e))
    yield "Copying shaders"

    # Step 3: ReShade.ini
    try:
        _write_reshade_ini(game_dir, profile_name, base)
    except OSError as e:
        raise InstallError("Writing ReShade.ini", str(e))
    yield "Writing ReShade.ini"

    # Step 4: Addon
    src_addon = os.path.join(base, "Glassless3D.addon")
    dst_addon = os.path.join(game_dir, "Glassless3D.addon")
    try:
        shutil.copy2(src_addon, dst_addon)
    except OSError as e:
        raise InstallError("Installing addon", str(e))
    yield "Installing addon"


def install(game_dir: str, profile_name: str = "wow") -> None:
    """Copy bundled assets into game_dir. Raises InstallError on failure."""
    for _ in install_steps(game_dir, profile_name):
        pass


def _write_reshade_ini(game_dir: str, profile_name: str, base: str) -> None:
    profile_path = os.path.join(base, "profiles", f"{profile_name}.json")
    if not os.path.exists(profile_path):
        profile_path = os.path.join(base, "profiles", "default.json")
    with open(profile_path) as f:
        profile = json.load(f)

    ini_path = os.path.join(game_dir, "ReShade.ini")
    depth_settings: dict = profile.get("reshade", {})
    shader_defaults: dict = profile.get("shader_defaults", {})
    all_keys = set(depth_settings) | set(shader_defaults)

    lines: list[str] = []
    if os.path.exists(ini_path):
        with open(ini_path) as f:
            lines = f.readlines()

    kept = [
        ln for ln in lines
        if not any(ln.startswith(k) for k in all_keys)
        and ln.strip() not in ("[PREPROCESSOR]", "[Glassless3D.fx]")
    ]
    block: list[str] = []
    if depth_settings:
        block += ["[PREPROCESSOR]\n"] + [f"{k}={v}\n" for k, v in depth_settings.items()]
    if shader_defaults:
        block += ["[Glassless3D.fx]\n"] + [f"{k}={v}\n" for k, v in shader_defaults.items()]

    with open(ini_path, "w") as f:
        f.writelines(kept + block)
