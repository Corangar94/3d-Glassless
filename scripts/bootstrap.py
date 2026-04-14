#!/usr/bin/env python3
"""
scripts/bootstrap.py - one-command dev setup for Glassless3D.

Downloads required assets and (if build tools are available) compiles the
ReShade addon so the wizard can run without any extra manual steps.

Usage:
    python scripts/bootstrap.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# -- Download helpers ----------------------------------------------------------

def _download(url: str, dest: str, label: str) -> None:
    if os.path.exists(dest):
        print(f"  already present: {os.path.relpath(dest, _ROOT)}")
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print(f"  downloading {label}...", end="", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    print(f" {os.path.getsize(dest) // 1024} KB")


# -- Step 1: Face landmarker model ---------------------------------------------

def step_face_model() -> bool:
    print("\n[1/4] Face landmarker model")
    dest = os.path.join(_ROOT, "models", "face_landmarker.task")
    url = (
        "https://storage.googleapis.com/mediapipe-models/"
        "face_landmarker/face_landmarker/float16/1/face_landmarker.task"
    )
    try:
        _download(url, dest, "face_landmarker.task (~4 MB)")
        print("  OK  models/face_landmarker.task")
        return True
    except Exception as e:
        print(f"  FAIL  {e}")
        return False


# -- Step 2: ReShade DLL -------------------------------------------------------

RESHADE_URL = "https://reshade.me/downloads/ReShade_Setup_5.9.2.exe"
RESHADE_INSTALLER = os.path.join(_ROOT, "vendor", "_ReShade_Setup_5.9.2.exe")
RESHADE_DLL = os.path.join(_ROOT, "ReShade64.dll")

_7ZIP_CANDIDATES = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
]


def _find_7zip() -> str | None:
    for path in _7ZIP_CANDIDATES:
        if os.path.isfile(path):
            return path
    if shutil.which("7z"):
        return "7z"
    return None


def step_reshade_dll() -> bool:
    print("\n[2/4] ReShade64.dll")
    if os.path.isfile(RESHADE_DLL):
        print(f"  already present: ReShade64.dll  ({os.path.getsize(RESHADE_DLL)//1024} KB)")
        return True

    seven_zip = _find_7zip()
    if not seven_zip:
        print("  FAIL  7-Zip not found.")
        print("  Install 7-Zip from https://7-zip.org then re-run.")
        return False

    try:
        _download(RESHADE_URL, RESHADE_INSTALLER, "ReShade installer (~3 MB)")
    except Exception as e:
        print(f"  FAIL  download: {e}")
        return False

    print("  extracting ReShade64.dll via 7-Zip...", end="", flush=True)
    result = subprocess.run(
        [seven_zip, "e", RESHADE_INSTALLER, "ReShade64.dll", f"-o{_ROOT}", "-y"],
        capture_output=True, text=True,
    )
    # Clean up the installer regardless of outcome
    if os.path.isfile(RESHADE_INSTALLER):
        os.remove(RESHADE_INSTALLER)

    if result.returncode != 0 or not os.path.isfile(RESHADE_DLL):
        print(f" FAILED")
        print(result.stderr[-500:])
        return False

    print(f"  {os.path.getsize(RESHADE_DLL)//1024} KB")
    print("  OK  ReShade64.dll")
    return True


# -- Step 3: ReShade SDK headers (for addon build) ----------------------------

RESHADE_VERSION = "5.9.2"
SDK_ZIP_URL = (
    f"https://github.com/crosire/reshade/archive/refs/tags/v{RESHADE_VERSION}.zip"
)
SDK_ZIP = os.path.join(_ROOT, "vendor", "_reshade_src.zip")
SDK_INCLUDE = os.path.join(_ROOT, "vendor", "reshade", "include")


def step_reshade_sdk() -> bool:
    print("\n[3/4] ReShade SDK headers")
    if os.path.isdir(SDK_INCLUDE) and os.listdir(SDK_INCLUDE):
        print(f"  already present: vendor/reshade/include/")
        return True
    try:
        _download(SDK_ZIP_URL, SDK_ZIP, f"ReShade {RESHADE_VERSION} source (~5 MB)")
    except Exception as e:
        print(f"  FAIL  download: {e}")
        return False

    print("  extracting include/ headers...", end="", flush=True)
    prefix = f"reshade-{RESHADE_VERSION}/include/"
    with zipfile.ZipFile(SDK_ZIP) as zf:
        members = [m for m in zf.namelist() if m.startswith(prefix)]
        if not members:
            print(" FAILED: include/ not found in archive")
            return False
        for member in members:
            relative = member[len(prefix):]
            if not relative:
                continue
            dest = os.path.join(SDK_INCLUDE, relative)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
    os.remove(SDK_ZIP)
    print(f" {len(os.listdir(SDK_INCLUDE))} files")
    print("  OK  vendor/reshade/include/")
    return True


# -- Step 4: Build C++ addon --------------------------------------------------

ADDON_OUT = os.path.join(_ROOT, "Glassless3D.addon")
ADDON_SRC = os.path.join(_ROOT, "addon")


def _find_cmake() -> str | None:
    if shutil.which("cmake"):
        return "cmake"
    try:
        import cmake as _cmake_pkg  # type: ignore[import-untyped]
        pkg_file = _cmake_pkg.__file__ or ""
        return os.path.join(os.path.dirname(pkg_file), "data", "bin", "cmake.exe")
    except ImportError:
        pass
    return None


def _find_vswhere() -> str | None:
    vswhere = os.path.join(
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        "Microsoft Visual Studio", "Installer", "vswhere.exe",
    )
    return vswhere if os.path.isfile(vswhere) else None


def step_build_addon() -> bool:
    print("\n[4/4] Glassless3D.addon (C++ ReShade addon)")

    if os.path.isfile(ADDON_OUT):
        print(f"  already present: Glassless3D.addon  ({os.path.getsize(ADDON_OUT)//1024} KB)")
        return True

    if not os.path.isdir(SDK_INCLUDE) or not os.listdir(SDK_INCLUDE):
        print("  FAIL  ReShade SDK headers missing (step 3 must succeed first).")
        return False

    cmake = _find_cmake()
    if not cmake:
        _print_build_instructions()
        return False

    build_dir = os.path.join(ADDON_SRC, "build")
    os.makedirs(build_dir, exist_ok=True)

    extra: list[str] = []
    vswhere = _find_vswhere()
    if vswhere:
        r = subprocess.run(
            [vswhere, "-latest", "-property", "installationPath"],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            extra = ["-DCMAKE_GENERATOR=Visual Studio 17 2022", "-A", "x64"]
            print(f"  Found VS: {r.stdout.strip()}")

    print("  configuring...", end="", flush=True)
    cfg = subprocess.run(
        [cmake, ADDON_SRC, "-B", build_dir,
         f"-DRESHADE_INCLUDE={SDK_INCLUDE}"] + extra,
        capture_output=True, text=True,
    )
    if cfg.returncode != 0:
        print(f" FAILED")
        print(cfg.stderr[-800:])
        _print_build_instructions()
        return False

    print(" building...", end="", flush=True)
    bld = subprocess.run(
        [cmake, "--build", build_dir, "--config", "Release"],
        capture_output=True, text=True,
    )
    if bld.returncode != 0:
        print(f" FAILED")
        print(bld.stderr[-800:])
        _print_build_instructions()
        return False

    for root, _, files in os.walk(build_dir):
        for fname in files:
            if fname == "Glassless3D.addon":
                shutil.copy2(os.path.join(root, fname), ADDON_OUT)
                print(f"  {os.path.getsize(ADDON_OUT)//1024} KB")
                print("  OK  Glassless3D.addon")
                return True

    print(" FAILED: built but Glassless3D.addon not found in build dir")
    _print_build_instructions()
    return False


def _print_build_instructions() -> None:
    print()
    print("  To build the addon manually:")
    print("  1. Install MSVC Build Tools:")
    print("     https://aka.ms/vs/17/release/vs_buildtools.exe")
    print("     (select 'C++ build tools' workload, ensure x64 is checked)")
    print("  2. Install CMake:  pip install cmake")
    print("     OR download from https://cmake.org/download/")
    print("  3. From a VS Developer Command Prompt:")
    print("       cmake addon/ -B addon/build -A x64")
    print("       cmake --build addon/build --config Release")
    print("       copy addon\\build\\Release\\Glassless3D.addon .")
    print("  4. Re-run bootstrap.py - it will skip already-done steps.")


# -- Main ----------------------------------------------------------------------

def main() -> None:
    print("Glassless3D bootstrap - setting up dev environment")
    print(f"Project root: {_ROOT}")

    results = {
        "Face model":    step_face_model(),
        "ReShade DLL":   step_reshade_dll(),
        "ReShade SDK":   step_reshade_sdk(),
        "Addon build":   step_build_addon(),
    }

    print("\n--- Summary -------------------------------------------")
    all_ok = True
    for name, ok in results.items():
        status = "OK  " if ok else "FAIL"
        print(f"  {status}  {name}")
        if not ok:
            all_ok = False

    if all_ok:
        print("\nAll done! Run:  python -m launcher")
    else:
        print("\nSome steps need attention (see above). Re-run after fixing.")
        sys.exit(1)


if __name__ == "__main__":
    main()
