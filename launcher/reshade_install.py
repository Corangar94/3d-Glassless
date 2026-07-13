"""Install the experimental ReShade backend into a game directory."""
from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Generator

from launcher.game_profiles import Backend, PolicyDecision


_ALLOWED_PROXY_NAMES = frozenset({"dxgi.dll", "d3d11.dll", "d3d9.dll"})


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
    game_dir: str,
    profile_name: str = "wow",
    *,
    policy: PolicyDecision,
    proxy_name: str = "dxgi.dll",
) -> Generator[str, None, None]:
    """Install the experimental backend assets into game_dir.

    Raises InstallError if any step fails.
    """
    if not policy.allows(Backend.RESHADE_ADDON):
        raise InstallError(
            "Policy check",
            "ReShade installation is not permitted: "
            f"{policy.reason or 'offline advanced acknowledgement is required'}",
        )

    normalized_proxy = proxy_name.strip().lower()
    if normalized_proxy not in _ALLOWED_PROXY_NAMES:
        allowed = ", ".join(sorted(_ALLOWED_PROXY_NAMES))
        raise InstallError(
            "Selecting ReShade proxy",
            f"unsupported proxy {proxy_name!r}; expected one of: {allowed}",
        )

    base = _bundle_dir()

    # Step 1: Experimental ReShade DLL
    src_dll = os.path.join(base, "ReShade64.dll")
    # Use dxgi.dll by default for DX11/DX12 games. D3D9 titles such as HL2
    # need d3d9.dll, but keep the choice explicit and policy-gated.
    dst_dll = os.path.join(game_dir, normalized_proxy)
    # Guard: the *standard* ReShade 5.9.2 DLL is 3,977,952 bytes and silently
    # rejects external addons with "limited add-on functionality" in its log.
    # The *addon* build is 4,155,904 bytes. If we somehow end up with the
    # wrong DLL bundled, fail loudly here instead of confusing the user later.
    _ADDON_BUILD_SIZE = 4_155_904
    try:
        actual_size = os.path.getsize(src_dll)
    except OSError as e:
        raise InstallError("Copying ReShade", f"cannot stat {src_dll}: {e}")
    if actual_size != _ADDON_BUILD_SIZE:
        raise InstallError(
            "Copying ReShade",
            f"bundled ReShade64.dll is {actual_size} bytes, expected "
            f"{_ADDON_BUILD_SIZE} (the addon build). Re-run "
            f"scripts/bootstrap.py after deleting ReShade64.dll.",
        )
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

    # Step 3: Experimental ReShade.ini
    try:
        _write_reshade_ini(game_dir, profile_name, base)
    except (OSError, ValueError) as e:
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


def install(
    game_dir: str,
    profile_name: str = "wow",
    *,
    policy: PolicyDecision,
    proxy_name: str = "dxgi.dll",
) -> None:
    """Install the experimental backend into game_dir. Raises InstallError on failure."""
    for _ in install_steps(game_dir, profile_name, policy=policy, proxy_name=proxy_name):
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

    protected_sections = {"[PREPROCESSOR]", "[Glassless3D.fx]", "[RESHADE]"}
    kept = [
        ln for ln in lines
        if ln.split("=", 1)[0].strip() not in all_keys
        and ln.strip() not in protected_sections
        and not ln.strip() in ("EffectSearchPaths", "TextureSearchPaths")
    ]
    # Strip any existing EffectSearchPaths / TextureSearchPaths lines too
    kept = [
        ln for ln in kept
        if not ln.startswith("EffectSearchPaths=")
        and not ln.startswith("TextureSearchPaths=")
        and not ln.startswith("PresetPath=")
    ]
    # Force Home (VK_HOME=36) as the overlay key — the launcher UI brands
    # "press Home" for ReShade. ReShade defaults to F12 (123) which nobody
    # will guess. Rewrite any existing KeyOverlay=... line in place.
    rewrote_keyoverlay = False
    for i, ln in enumerate(kept):
        if ln.startswith("KeyOverlay="):
            kept[i] = "KeyOverlay=36,0,0,0\n"
            rewrote_keyoverlay = True
            break
    if not rewrote_keyoverlay:
        # No existing [INPUT] section — prepend one so ReShade picks it up.
        kept = ["[INPUT]\n", "KeyOverlay=36,0,0,0\n"] + kept

    block: list[str] = [
        "[RESHADE]\n",
        r"PresetPath=.\Glassless3D.ini" + "\n",
        r"EffectSearchPaths=.\reshade-shaders\Shaders" + "\n",
        r"TextureSearchPaths=.\reshade-shaders\Textures" + "\n",
    ]
    if depth_settings:
        block += ["[PREPROCESSOR]\n"] + [f"{k}={v}\n" for k, v in depth_settings.items()]

    with open(ini_path, "w") as f:
        f.writelines(kept + block)

    preset_path = os.path.join(game_dir, "Glassless3D.ini")
    preset: list[str] = [
        "Techniques=Glassless3D\n",
        "TechniqueSorting=Glassless3D\n",
    ]
    if shader_defaults:
        preset += ["[Glassless3D.fx]\n"] + [f"{k}={v}\n" for k, v in shader_defaults.items()]
    with open(preset_path, "w") as f:
        f.writelines(preset)
