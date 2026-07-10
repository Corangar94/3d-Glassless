#!/usr/bin/env python3
# setup.py
# Installs the experimental ReShade backend by copying Glassless3D.addon
# + shaders into a game directory, and updates ReShade.ini with the correct
# depth buffer settings.
#
# Usage:
#   python setup.py --game wow --profile-config "%APPDATA%\Glassless3D\config.yaml"
#   python setup.py --game-dir "C:\Games\MyGame" --profile default --profile-config config.yaml
#   python setup.py --game wow --profile-config config.yaml --dry-run

import argparse
import json
import os
import shutil
import sys
import winreg
from pathlib import Path
from typing import Sequence

from launcher.game_profile_store import ProfileStoreError, load_profiles
from launcher.game_profiles import Backend, GameProfile, PolicyDecision, evaluate_profile

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.join(BASE_DIR, "profiles")
SHADERS_DIR  = os.path.join(BASE_DIR, "shaders")
ADDON_PATH   = os.path.join(BASE_DIR, "Glassless3D.addon")

RESHADE_URL = "https://reshade.me/downloads/ReShade_Setup_5.9.2.exe"


def load_profile(name: str) -> dict:
    path = os.path.join(PROFILES_DIR, f"{name}.json")
    if not os.path.exists(path):
        sys.exit(f"ERROR: Profile '{name}' not found at {path}")
    with open(path) as f:
        return json.load(f)


def find_game_dir(profile: dict) -> str:
    setup = profile.get("setup", {})

    reg_key = setup.get("registry_key", "")
    reg_val = setup.get("registry_value", "")
    if reg_key and reg_val:
        try:
            root_str, subkey = reg_key.split("\\", 1)
            root = {"HKLM": winreg.HKEY_LOCAL_MACHINE,
                    "HKCU": winreg.HKEY_CURRENT_USER}[root_str]
            with winreg.OpenKey(root, subkey) as key:
                value, _ = winreg.QueryValueEx(key, reg_val)
                if os.path.isdir(value):
                    return value
        except (FileNotFoundError, OSError, KeyError):
            pass

    for path in setup.get("common_paths", []):
        if os.path.isdir(path):
            return path

    sys.exit(
        "ERROR: Could not find game directory automatically.\n"
        "Use --game-dir to specify it manually."
    )


def apply_depth_settings(game_dir: str, profile: dict, dry_run: bool) -> None:
    ini_path = os.path.join(game_dir, "ReShade.ini")
    depth_settings = profile.get("reshade", {})
    shader_defaults = profile.get("shader_defaults", {})

    lines: list[str] = []
    if os.path.exists(ini_path):
        with open(ini_path) as f:
            lines = f.readlines()

    # Strip any existing PREPROCESSOR / Glassless3D.fx keys, then append fresh blocks
    all_keys = set(depth_settings) | set(shader_defaults)
    kept = [l for l in lines
            if not any(l.startswith(k) for k in all_keys)
            and l.strip() not in ("[PREPROCESSOR]", "[Glassless3D.fx]")]
    block: list[str] = []
    if depth_settings:
        block += ["[PREPROCESSOR]\n"] + [f"{k}={v}\n" for k, v in depth_settings.items()]
    if shader_defaults:
        block += ["[Glassless3D.fx]\n"] + [f"{k}={v}\n" for k, v in shader_defaults.items()]

    if dry_run:
        print(f"  [dry-run] Would write to {ini_path}:")
        for line in block:
            print(f"    {line}", end="")
    else:
        with open(ini_path, "w") as f:
            f.writelines(kept + block)
        print(f"  [OK] Updated {ini_path}")


def install(
    game_dir: str,
    profile: dict,
    dry_run: bool,
    *,
    policy: PolicyDecision,
) -> None:
    if not policy.allows(Backend.RESHADE_ADDON):
        sys.exit(
            "ERROR: ReShade installation is not permitted: "
            f"{policy.reason or 'offline advanced acknowledgement is required'}"
        )

    print(f"\nInstalling to: {game_dir}")
    print(f"Profile:       {profile['name']}")
    if dry_run:
        print("(DRY RUN — no files written)\n")

    # Addon
    dst_addon = os.path.join(game_dir, "Glassless3D.addon")
    if dry_run:
        print(f"  [dry-run] Would copy {ADDON_PATH} → {dst_addon}")
    else:
        shutil.copy2(ADDON_PATH, dst_addon)
        print(f"  [OK] Copied addon → {dst_addon}")

    # Shaders
    shader_dst = os.path.join(game_dir, "reshade-shaders", "Shaders")
    if not dry_run:
        os.makedirs(shader_dst, exist_ok=True)
    for fname in ["Glassless3D.fx", "Glassless3D.fxh"]:
        src = os.path.join(SHADERS_DIR, fname)
        dst = os.path.join(shader_dst, fname)
        if dry_run:
            print(f"  [dry-run] Would copy {src} → {dst}")
        else:
            shutil.copy2(src, dst)
            print(f"  [OK] Copied {fname}")

    apply_depth_settings(game_dir, profile, dry_run)

    print("\n── Experimental backend prerequisites ────────────────────────")
    print(f"  1. If you are opting into this backend, install ReShade into {game_dir}:")
    print(f"     Download: {RESHADE_URL}")
    print("     Run installer → select Wow.exe → choose DirectX 11")
    print("  2. Start the tracker:   python tracker/main.py")
    print("     (or use OpenTrack with NeuralNet tracker + FreeTrack output)")
    print("  3. Launch the game.")
    print("  4. Press Home → ReShade overlay → enable 'Glassless3D'.")
    print("  5. Enjoy!\n")


def _normalized_path(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(path))))


def _require_target_matches_active_profile(
    active_profile: GameProfile,
    game_dir: str,
    install_profile: dict,
) -> None:
    executable_path = active_profile.executable_path.strip()
    if not executable_path:
        sys.exit(
            "ERROR: The active game profile requires an executable path before ReShade installation."
        )

    executable = Path(executable_path)
    if not executable.is_absolute():
        sys.exit("ERROR: The active game profile executable path must be absolute.")
    if _normalized_path(executable.parent) != _normalized_path(game_dir):
        sys.exit("ERROR: Target game directory does not match active profile executable.")

    setup = install_profile.get("setup")
    expected_name = setup.get("executable") if isinstance(setup, dict) else None
    if isinstance(expected_name, str) and expected_name:
        if os.path.normcase(executable.name) != os.path.normcase(expected_name):
            sys.exit("ERROR: Target executable does not match selected ReShade install profile.")
    if not executable.is_file():
        sys.exit(f"ERROR: Active profile executable not found: {executable}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Glassless3D experimental ReShade backend installer")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--game",     help="Profile name (wow, default)")
    group.add_argument("--game-dir", help="Path to game directory")
    parser.add_argument("--profile", default="default",
                        help="Profile to use with --game-dir")
    parser.add_argument(
        "--profile-config",
        type=Path,
        required=True,
        help="Glassless3D config.yaml containing the active game profile",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        profiles, active_profile_id = load_profiles(args.profile_config)
    except ProfileStoreError as exc:
        sys.exit(f"ERROR: {exc}")
    if active_profile_id is None:
        sys.exit("ERROR: No active Glassless3D game profile is configured.")
    active_profile = profiles[active_profile_id]
    policy = evaluate_profile(active_profile)
    if not policy.allows(Backend.RESHADE_ADDON):
        sys.exit(f"ERROR: ReShade installation is not permitted: {policy.reason}")

    if args.game:
        install_profile = load_profile(args.game)
        game_dir = find_game_dir(install_profile)
    else:
        install_profile = load_profile(args.profile)
        game_dir = os.path.abspath(args.game_dir)
        if not os.path.isdir(game_dir):
            sys.exit(f"ERROR: Directory not found: {game_dir}")

    _require_target_matches_active_profile(active_profile, game_dir, install_profile)

    if not os.path.exists(ADDON_PATH):
        sys.exit("ERROR: Glassless3D.addon not found.\nBuild it: cd addon && build.bat")

    install(game_dir, install_profile, dry_run=args.dry_run, policy=policy)


if __name__ == "__main__":
    main()
