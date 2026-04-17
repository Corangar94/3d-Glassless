#!/usr/bin/env python3
"""
scripts/bootstrap.py - one-command dev setup for Glassless3D.

Downloads required assets and (if build tools are available) compiles the
standalone desktop overlay so the launcher can run the primary workflow.
The ReShade addon is experimental and only prepared when requested.

Usage:
    python scripts/bootstrap.py
    python scripts/bootstrap.py --with-reshade
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from collections.abc import Callable

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
    print("\n[primary] Face landmarker model")
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


# -- Experimental ReShade DLL -------------------------------------------------

RESHADE_URL = "https://reshade.me/downloads/ReShade_Setup_5.9.2_Addon.exe"
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
    print("\n[experimental] ReShade64.dll")
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


# -- Experimental ReShade SDK headers (for addon build) ----------------------

RESHADE_VERSION = "5.9.2"
SDK_ZIP_URL = (
    f"https://github.com/crosire/reshade/archive/refs/tags/v{RESHADE_VERSION}.zip"
)
SDK_ZIP = os.path.join(_ROOT, "vendor", "_reshade_src.zip")
SDK_INCLUDE = os.path.join(_ROOT, "vendor", "reshade", "include")


def step_reshade_sdk() -> bool:
    print("\n[experimental] ReShade SDK headers")
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


# -- Step 4: ONNX Runtime + DirectML (for depth inference in overlay) ---------
#
# We need two NuGet packages (both shipped as .nupkg zip files):
#   Microsoft.ML.OnnxRuntime.DirectML -> onnxruntime.dll + headers + .lib
#   Microsoft.AI.DirectML             -> DirectML.dll + header + .lib
# The overlay links against these for real-time monocular depth inference.
# Runtime DLLs are copied next to Glassless3DOverlay.exe by its post-build step.

ORT_VERSION = "1.20.1"
ORT_NUPKG_URL = (
    f"https://www.nuget.org/api/v2/package/Microsoft.ML.OnnxRuntime.DirectML/{ORT_VERSION}"
)
ORT_NUPKG = os.path.join(_ROOT, "vendor", f"_ort_directml_{ORT_VERSION}.nupkg")
ORT_DIR = os.path.join(_ROOT, "vendor", "onnxruntime")

DML_VERSION = "1.15.2"
DML_NUPKG_URL = (
    f"https://www.nuget.org/api/v2/package/Microsoft.AI.DirectML/{DML_VERSION}"
)
DML_NUPKG = os.path.join(_ROOT, "vendor", f"_directml_{DML_VERSION}.nupkg")
DML_DIR = os.path.join(_ROOT, "vendor", "directml")


def _extract_from_nupkg(nupkg_path: str, prefix: str, dest_dir: str) -> int:
    """Extract all entries starting with `prefix` from nupkg (zip) into dest_dir.

    Strips the prefix so files land cleanly. Returns count extracted.
    """
    count = 0
    with zipfile.ZipFile(nupkg_path) as zf:
        for name in zf.namelist():
            if not name.startswith(prefix) or name.endswith("/"):
                continue
            rel = name[len(prefix):]
            if not rel:
                continue
            out = os.path.join(dest_dir, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with zf.open(name) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    return count


def step_onnxruntime() -> bool:
    print("\n[primary] ONNX Runtime + DirectML (for depth inference)")
    ort_dll = os.path.join(ORT_DIR, "lib", "onnxruntime.dll")
    dml_dll = os.path.join(DML_DIR, "lib", "DirectML.dll")
    if os.path.isfile(ort_dll) and os.path.isfile(dml_dll):
        print(f"  already present: vendor/onnxruntime + vendor/directml")
        return True

    try:
        _download(ORT_NUPKG_URL, ORT_NUPKG, f"ONNX Runtime DirectML {ORT_VERSION} (~25 MB)")
        _download(DML_NUPKG_URL, DML_NUPKG, f"DirectML {DML_VERSION} (~10 MB)")
    except Exception as e:
        print(f"  FAIL  download: {e}")
        return False

    try:
        # ORT: DLL + .lib in runtimes/win-x64/native/, headers in build/native/include/
        n1 = _extract_from_nupkg(ORT_NUPKG, "runtimes/win-x64/native/",
                                 os.path.join(ORT_DIR, "lib"))
        n2 = _extract_from_nupkg(ORT_NUPKG, "build/native/include/",
                                 os.path.join(ORT_DIR, "include"))
        # DirectML: bin/x64-win/DirectML.dll, lib/x64-win/DirectML.lib, include/DirectML.h
        n3 = _extract_from_nupkg(DML_NUPKG, "bin/x64-win/",
                                 os.path.join(DML_DIR, "lib"))
        n4 = _extract_from_nupkg(DML_NUPKG, "lib/x64-win/",
                                 os.path.join(DML_DIR, "lib"))
        n5 = _extract_from_nupkg(DML_NUPKG, "include/",
                                 os.path.join(DML_DIR, "include"))
    except Exception as e:
        print(f"  FAIL  extract: {e}")
        return False
    finally:
        for p in (ORT_NUPKG, DML_NUPKG):
            if os.path.isfile(p):
                os.remove(p)

    if not (os.path.isfile(ort_dll) and os.path.isfile(dml_dll)):
        print(f"  FAIL  expected DLLs not found after extraction")
        print(f"    ORT entries: {n1} lib + {n2} headers")
        print(f"    DML entries: {n3} bin + {n4} lib + {n5} headers")
        return False

    print(f"  OK  onnxruntime.dll ({os.path.getsize(ort_dll)//1024} KB) "
          f"+ DirectML.dll ({os.path.getsize(dml_dll)//1024} KB)")
    return True


# -- Step 5: Depth Anything V2 Small ONNX model -------------------------------

DEPTH_MODEL_URL = (
    "https://huggingface.co/onnx-community/depth-anything-v2-small/"
    "resolve/main/onnx/model_fp16.onnx"
)
DEPTH_MODEL_DEST = os.path.join(_ROOT, "models", "depth_anything_v2_small_fp16.onnx")


def step_depth_model() -> bool:
    print("\n[primary] Depth Anything V2 Small (monocular depth model)")
    try:
        _download(DEPTH_MODEL_URL, DEPTH_MODEL_DEST,
                  "depth_anything_v2_small_fp16.onnx (~50 MB)")
        print(f"  OK  models/depth_anything_v2_small_fp16.onnx")
        return True
    except Exception as e:
        print(f"  FAIL  {e}")
        return False


# -- Experimental ReShade addon ----------------------------------------------

ADDON_OUT = os.path.join(_ROOT, "Glassless3D.addon")
ADDON_SRC = os.path.join(_ROOT, "addon")
OVERLAY_OUT = os.path.join(_ROOT, "Glassless3DOverlay.exe")
OVERLAY_SRC = os.path.join(_ROOT, "overlay")

# Portable MinGW-w64 GCC 14.2.0 (downloaded only if no system compiler found)
_MINGW_DIR = os.path.join(_ROOT, "vendor", "_mingw64")
_MINGW_7Z  = os.path.join(_ROOT, "vendor", "_mingw64.7z")
_MINGW_URL = (
    "https://github.com/brechtsanders/winlibs_mingw/releases/download/"
    "14.2.0posix-18.1.8-12.0.0-msvcrt-r1/"
    "winlibs-x86_64-posix-seh-gcc-14.2.0-mingw-w64msvcrt-12.0.0-r1.7z"
)


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


def _find_gcc() -> str | None:
    """Return a path to g++.exe, downloading portable MinGW if needed."""
    # Check PATH first
    if shutil.which("g++"):
        return shutil.which("g++")

    # Check our portable MinGW
    if os.path.isdir(_MINGW_DIR):
        for root, _, files in os.walk(_MINGW_DIR):
            if "g++.exe" in files:
                return os.path.join(root, "g++.exe")

    # Download portable MinGW (~100 MB, one-time)
    seven_zip = _find_7zip()
    if not seven_zip:
        print("  FAIL  7-Zip not found; cannot download MinGW.")
        return None

    try:
        _download(_MINGW_URL, _MINGW_7Z, "MinGW-w64 GCC 14.2.0 (~100 MB, one-time)")
    except Exception as e:
        print(f"  FAIL  MinGW download: {e}")
        return None

    print("  extracting MinGW-w64...", end="", flush=True)
    os.makedirs(_MINGW_DIR, exist_ok=True)
    r = subprocess.run(
        [seven_zip, "x", _MINGW_7Z, f"-o{_MINGW_DIR}", "-y"],
        capture_output=True, text=True,
    )
    if os.path.isfile(_MINGW_7Z):
        os.remove(_MINGW_7Z)
    if r.returncode != 0:
        print(" FAILED")
        print(r.stderr[-500:])
        return None

    for root, _, files in os.walk(_MINGW_DIR):
        if "g++.exe" in files:
            print(f" OK")
            return os.path.join(root, "g++.exe")

    print(" FAILED: g++.exe not found after extraction")
    return None


def step_build_addon() -> bool:
    print("\n[experimental] Glassless3D.addon (C++ ReShade addon)")

    if os.path.isfile(ADDON_OUT):
        print(f"  already present: Glassless3D.addon  ({os.path.getsize(ADDON_OUT)//1024} KB)")
        return True

    if not os.path.isdir(SDK_INCLUDE) or not os.listdir(SDK_INCLUDE):
        print("  FAIL  ReShade SDK headers missing (step 3 must succeed first).")
        return False

    cmake = _find_cmake()
    if not cmake:
        print("  FAIL  cmake not found.  Run:  pip install cmake")
        return False

    # Detect compiler: prefer MSVC, fall back to portable MinGW
    env = os.environ.copy()
    extra: list[str] = []
    use_msvc = False

    vswhere = _find_vswhere()
    if vswhere:
        r = subprocess.run(
            [vswhere, "-latest", "-property", "installationPath"],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            extra = ["-G", "Visual Studio 17 2022", "-A", "x64"]
            use_msvc = True
            print(f"  Using MSVC: {r.stdout.strip()}")

    if not use_msvc:
        gpp = _find_gcc()
        if not gpp:
            return False
        mingw_bin = os.path.dirname(gpp)
        env["PATH"] = mingw_bin + os.pathsep + env.get("PATH", "")
        extra = ["-G", "MinGW Makefiles"]
        print(f"  Using MinGW GCC: {gpp}")

    build_dir = os.path.join(ADDON_SRC, "build_mingw" if not use_msvc else "build")
    os.makedirs(build_dir, exist_ok=True)

    print("  configuring...", end="", flush=True)
    cfg = subprocess.run(
        [cmake, ADDON_SRC, "-B", build_dir,
         f"-DRESHADE_INCLUDE={SDK_INCLUDE}",
         "-DCMAKE_BUILD_TYPE=Release"] + extra,
        capture_output=True, text=True, env=env,
    )
    if cfg.returncode != 0:
        print(f" FAILED")
        print(cfg.stderr[-800:])
        return False

    print(" building...", end="", flush=True)
    bld = subprocess.run(
        [cmake, "--build", build_dir, "--config", "Release"],
        capture_output=True, text=True, env=env,
    )
    if bld.returncode != 0:
        print(f" FAILED")
        print(bld.stderr[-800:])
        return False

    for root, _, files in os.walk(build_dir):
        for fname in files:
            if fname == "Glassless3D.addon":
                shutil.copy2(os.path.join(root, fname), ADDON_OUT)
                print(f"  {os.path.getsize(ADDON_OUT)//1024} KB")
                print("  OK  Glassless3D.addon")
                return True

    print(" FAILED: built but Glassless3D.addon not found in build dir")
    return False


# -- Step 7: Build overlay exe ------------------------------------------------

def step_build_overlay() -> bool:
    print("\n[primary] Glassless3DOverlay.exe (D3D11 screen-capture overlay)")

    if os.path.isfile(OVERLAY_OUT):
        print(f"  already present: Glassless3DOverlay.exe  ({os.path.getsize(OVERLAY_OUT)//1024} KB)")
        return True

    cmake = _find_cmake()
    if not cmake:
        print("  FAIL  cmake not found.  Run:  pip install cmake")
        return False

    env = os.environ.copy()
    extra: list[str] = []
    use_msvc = False

    vswhere = _find_vswhere()
    if vswhere:
        r = subprocess.run(
            [vswhere, "-latest", "-property", "installationPath"],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            extra = ["-G", "Visual Studio 17 2022", "-A", "x64"]
            use_msvc = True

    if not use_msvc:
        gpp = _find_gcc()
        if not gpp:
            return False
        mingw_bin = os.path.dirname(gpp)
        env["PATH"] = mingw_bin + os.pathsep + env.get("PATH", "")
        extra = ["-G", "MinGW Makefiles"]

    build_dir = os.path.join(OVERLAY_SRC, "build_mingw" if not use_msvc else "build")
    os.makedirs(build_dir, exist_ok=True)

    print("  configuring...", end="", flush=True)
    cfg = subprocess.run(
        [cmake, OVERLAY_SRC, "-B", build_dir,
         "-DCMAKE_BUILD_TYPE=Release"] + extra,
        capture_output=True, text=True, env=env,
    )
    if cfg.returncode != 0:
        print(f" FAILED")
        print(cfg.stderr[-800:])
        return False

    print(" building...", end="", flush=True)
    bld = subprocess.run(
        [cmake, "--build", build_dir, "--config", "Release"],
        capture_output=True, text=True, env=env,
    )
    if bld.returncode != 0:
        print(f" FAILED")
        print(bld.stderr[-800:])
        return False

    if os.path.isfile(OVERLAY_OUT):
        print(f"  {os.path.getsize(OVERLAY_OUT)//1024} KB")
        print("  OK  Glassless3DOverlay.exe")
        return True

    print(" FAILED: exe not found after build")
    return False


# -- Main ----------------------------------------------------------------------

def _build_steps(with_reshade: bool) -> list[tuple[str, Callable[[], bool]]]:
    steps: list[tuple[str, Callable[[], bool]]] = [
        ("Face model", step_face_model),
        ("ONNX Runtime", step_onnxruntime),
        ("Depth model", step_depth_model),
        ("Overlay build", step_build_overlay),
    ]
    if with_reshade:
        steps.extend(
            [
                ("Experimental ReShade DLL", step_reshade_dll),
                ("Experimental ReShade SDK", step_reshade_sdk),
                ("Experimental addon build", step_build_addon),
            ]
        )
    return steps


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Prepare the primary Glassless3D desktop overlay runtime."
    )
    parser.add_argument(
        "--with-reshade",
        action="store_true",
        help="Also prepare the experimental ReShade/game-injected backend.",
    )
    args = parser.parse_args(argv)

    print("Glassless3D bootstrap - setting up overlay-first dev environment")
    print(f"Project root: {_ROOT}")
    if not args.with_reshade:
        print("Experimental ReShade backend skipped. Use --with-reshade to prepare it.")

    results = {name: step() for name, step in _build_steps(args.with_reshade)}

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
