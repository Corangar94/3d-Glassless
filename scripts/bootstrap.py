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
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from collections.abc import Callable

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# -- Download helpers ----------------------------------------------------------

def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, dest: str, label: str, *, sha256: str | None = None) -> None:
    if os.path.exists(dest):
        if sha256 and _sha256(dest).lower() != sha256.lower():
            raise RuntimeError(f"SHA-256 mismatch for existing {os.path.relpath(dest, _ROOT)}")
        print(f"  already present: {os.path.relpath(dest, _ROOT)}")
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    print(f"  downloading {label}...", end="", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    if sha256 and _sha256(dest).lower() != sha256.lower():
        os.remove(dest)
        raise RuntimeError(f"SHA-256 mismatch for downloaded {label}")
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

RESHADE_VERSION = "6.7.3"
RESHADE_URL = f"https://reshade.me/downloads/ReShade_Setup_{RESHADE_VERSION}_Addon.exe"
RESHADE_INSTALLER = os.path.join(_ROOT, "vendor", f"_ReShade_Setup_{RESHADE_VERSION}_Addon.exe")
# Published from reshade.me and checked against its documented signing
# certificate thumbprint 589690208A5E52FB96980C4A6698F50ACD47C49F.
RESHADE_INSTALLER_SHA256 = "c78db69bd127e98054bd496fb422655f4a1cc664e28f8d12ce9835b2647bc571"
RESHADE32_SHA256 = "b0a0fa7472d9a153816edcf7606902eb9c8f262e6100fc9973ec495634dca2c2"
RESHADE64_SHA256 = "ec9245d05c11751f2ac0d2256e6921ad8fb36be9172ef6d587856591eb729a25"
RESHADE32_DLL = os.path.join(_ROOT, "ReShade32.dll")
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
    print("\n[experimental] verified ReShade32.dll + ReShade64.dll")
    if (os.path.isfile(RESHADE32_DLL) and os.path.isfile(RESHADE_DLL) and
            _sha256(RESHADE32_DLL) == RESHADE32_SHA256 and
            _sha256(RESHADE_DLL) == RESHADE64_SHA256):
        print("  already present and verified: ReShade32.dll + ReShade64.dll")
        return True

    seven_zip = _find_7zip()
    if not seven_zip:
        print("  FAIL  7-Zip not found.")
        print("  Install 7-Zip from https://7-zip.org then re-run.")
        return False

    try:
        _download(
            RESHADE_URL, RESHADE_INSTALLER, "ReShade installer (~3 MB)",
            sha256=RESHADE_INSTALLER_SHA256,
        )
    except Exception as e:
        print(f"  FAIL  download: {e}")
        return False

    print("  extracting architecture-specific DLLs via 7-Zip...", end="", flush=True)
    result = subprocess.run(
        [seven_zip, "e", RESHADE_INSTALLER, "ReShade32.dll", "ReShade64.dll", f"-o{_ROOT}", "-y"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not os.path.isfile(RESHADE32_DLL) or not os.path.isfile(RESHADE_DLL):
        print(f" FAILED")
        print(result.stderr[-500:])
        return False

    if _sha256(RESHADE32_DLL) != RESHADE32_SHA256 or _sha256(RESHADE_DLL) != RESHADE64_SHA256:
        print(" FAILED: extracted DLL hash mismatch")
        return False
    print(" OK")
    return True


# -- Experimental ReShade SDK headers (for addon build) ----------------------

SDK_ZIP_URL = (
    f"https://github.com/crosire/reshade/archive/refs/tags/v{RESHADE_VERSION}.zip"
)
SDK_ZIP_SHA256 = "0a771b62095d145028944e3944a6dedaa8a58cc9f91aee48b61679910987f9d1"
SDK_HEADER_SHA256 = "dcb22f29ca7d7b2e4a9ea2f19cf98ce2911141659e525d3417f5ffada75899c8"
SDK_ZIP = os.path.join(_ROOT, "vendor", "_reshade_src.zip")
SDK_INCLUDE = os.path.join(_ROOT, "vendor", "reshade", "include")


def step_reshade_sdk() -> bool:
    print("\n[experimental] ReShade SDK headers")
    main_header = os.path.join(SDK_INCLUDE, "reshade.hpp")
    if os.path.isfile(main_header) and _sha256(main_header) == SDK_HEADER_SHA256:
        print("  already present and version-verified: vendor/reshade/include/")
        return True
    try:
        _download(
            SDK_ZIP_URL, SDK_ZIP, f"ReShade {RESHADE_VERSION} source (~2 MB)",
            sha256=SDK_ZIP_SHA256,
        )
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
    if not os.path.isfile(main_header) or _sha256(main_header) != SDK_HEADER_SHA256:
        print(" FAILED: extracted SDK header hash mismatch")
        return False
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

ADDON_OUT = os.path.join(_ROOT, "Glassless3D.addon64")
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
_MINGW32_DIR = os.path.join(_ROOT, "vendor", "_mingw32")
_MINGW32_ZIP = os.path.join(_ROOT, "vendor", "_mingw32.zip")
_MINGW32_URL = (
    "https://downloads.sourceforge.net/project/winlibs-mingw/"
    "14.2.0posix-19.1.1-12.0.0-msvcrt-r2/"
    "winlibs-i686-posix-dwarf-gcc-14.2.0-mingw-w64msvcrt-12.0.0-r2.zip"
)
_MINGW32_SHA256 = "430cb1d3a7e0c45683fc46b16275a92b98ba7f4eec975bb7846e1da83b2aa21e"


def _find_cmake() -> str | None:
    if shutil.which("cmake"):
        return "cmake"
    bundled = os.path.join(_MINGW_DIR, "mingw64", "bin", "cmake.exe")
    if os.path.isfile(bundled):
        return bundled
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
    # Use the pinned portable toolchain before any system g++. Newer
    # MinGW WinRT headers can redefine IReference<boolean>/IReference<BYTE>.
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


def _find_gcc32() -> str | None:
    compiler = os.path.join(_MINGW32_DIR, "mingw32", "bin", "g++.exe")
    if os.path.isfile(compiler):
        return compiler
    try:
        _download(_MINGW32_URL, _MINGW32_ZIP, "32-bit MinGW-w64 (~235 MB)", sha256=_MINGW32_SHA256)
        root = os.path.realpath(_MINGW32_DIR)
        with zipfile.ZipFile(_MINGW32_ZIP) as archive:
            for member in archive.infolist():
                destination = os.path.realpath(os.path.join(root, member.filename))
                if os.path.commonpath((root, destination)) != root:
                    raise RuntimeError(f"unsafe toolchain archive member: {member.filename}")
            archive.extractall(root)
    except Exception as exc:
        print(f"  FAIL  32-bit MinGW setup: {exc}")
        return None
    return compiler if os.path.isfile(compiler) else None


def _build_addon_arch(cmake: str, compiler: str, arch: str) -> bool:
    env = os.environ.copy()
    env["PATH"] = os.path.dirname(compiler) + os.pathsep + env.get("PATH", "")
    build_dir = os.path.join(ADDON_SRC, "build_mingw32" if arch == "x86" else "build_mingw")
    output_name = "Glassless3D.addon32" if arch == "x86" else "Glassless3D.addon64"
    cfg = subprocess.run(
        [
            cmake, "-G", "MinGW Makefiles", ADDON_SRC, "-B", build_dir,
            f"-DRESHADE_INCLUDE={SDK_INCLUDE}", "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_CXX_COMPILER={compiler}",
        ],
        capture_output=True, text=True, env=env,
    )
    if cfg.returncode != 0:
        print(f" FAILED ({arch} configure)\n{cfg.stderr[-800:]}")
        return False
    bld = subprocess.run(
        [cmake, "--build", build_dir, "--config", "Release"],
        capture_output=True, text=True, env=env,
    )
    if bld.returncode != 0:
        print(f" FAILED ({arch} build)\n{bld.stderr[-800:]}")
        return False
    built = os.path.join(build_dir, output_name)
    if not os.path.isfile(built):
        print(f" FAILED: {output_name} not found")
        return False
    shutil.copy2(built, os.path.join(_ROOT, output_name))
    return True


def step_build_addon() -> bool:
    print("\n[experimental] architecture-complete Glassless3D ReShade addons")

    addon32 = os.path.join(_ROOT, "Glassless3D.addon32")
    manifest_path = os.path.join(_ROOT, "reshade-assets.json")
    manifest_current = False
    try:
        with open(manifest_path, encoding="utf-8") as stream:
            manifest_current = json.load(stream).get("reshade_version") == RESHADE_VERSION
    except (OSError, ValueError):
        pass
    if os.path.isfile(ADDON_OUT) and os.path.isfile(addon32) and manifest_current:
        print("  already present: Glassless3D.addon32 + Glassless3D.addon64")
        _write_reshade_asset_manifest()
        return True

    if not os.path.isdir(SDK_INCLUDE) or not os.listdir(SDK_INCLUDE):
        print("  FAIL  ReShade SDK headers missing (step 3 must succeed first).")
        return False

    cmake = _find_cmake()
    if not cmake:
        print("  FAIL  cmake not found.  Run:  pip install cmake")
        return False

    gcc64 = _find_gcc()
    gcc32 = _find_gcc32()
    if not gcc64 or not gcc32:
        return False
    print("  building x64 + x86...", end="", flush=True)
    if not _build_addon_arch(cmake, gcc64, "x64"):
        return False
    if not _build_addon_arch(cmake, gcc32, "x86"):
        return False
    _write_reshade_asset_manifest()
    print(" OK")
    return True


def _write_reshade_asset_manifest() -> None:
    assets: dict[str, dict[str, str]] = {}
    for name, arch in (
        ("ReShade32.dll", "x86"), ("ReShade64.dll", "x64"),
        ("Glassless3D.addon32", "x86"), ("Glassless3D.addon64", "x64"),
    ):
        path = os.path.join(_ROOT, name)
        if os.path.isfile(path):
            assets[name] = {"arch": arch, "sha256": _sha256(path)}
    path = os.path.join(_ROOT, "reshade-assets.json")
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        json.dump({"reshade_version": RESHADE_VERSION, "assets": assets}, stream, indent=2)
        stream.write("\n")


# -- Step 7: Build overlay exe ------------------------------------------------

def _sync_overlay_runtime_files() -> bool:
    pairs = (
        (os.path.join(ORT_DIR, "lib", "onnxruntime.dll"), os.path.join(_ROOT, "onnxruntime.dll")),
        (os.path.join(DML_DIR, "lib", "DirectML.dll"), os.path.join(_ROOT, "DirectML.dll")),
    )
    try:
        for source, destination in pairs:
            if not os.path.isfile(source):
                print(f"  FAIL  missing runtime dependency: {source}")
                return False
            shutil.copy2(source, destination)
    except OSError as exc:
        print(f"  FAIL  could not copy overlay runtime DLLs: {exc}")
        return False
    return all(os.path.isfile(destination) for _source, destination in pairs)


def step_build_overlay() -> bool:
    print("\n[primary] Glassless3DOverlay.exe (D3D11 screen-capture overlay)")

    cmake = _find_cmake()
    if not cmake:
        print("  FAIL  cmake not found.  Run:  pip install cmake")
        return False

    env = os.environ.copy()
    extra: list[str] = []
    use_msvc = False

    # Prefer MinGW-w64 for the native overlay. The current WinRT COM
    # declarations use MinGW's __CRT_UUID_DECL support and are not yet
    # portable to MSVC without a separate compiler-compatibility pass.

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
        print(" FAILED")
        output = (cfg.stdout + "\n" + cfg.stderr).strip()
        print(output[-6000:])
        return False

    print(" building...", end="", flush=True)
    bld = subprocess.run(
        [cmake, "--build", build_dir, "--config", "Release"],
        capture_output=True, text=True, env=env,
    )
    if bld.returncode != 0:
        print(" FAILED")
        output = (bld.stdout + "\n" + bld.stderr).strip()
        print(output[-12000:])
        return False

    built_candidates = (
        os.path.join(build_dir, "Release", "Glassless3DOverlay.exe"),
        os.path.join(build_dir, "Glassless3DOverlay.exe"),
    )
    built = next((path for path in built_candidates if os.path.isfile(path)), None)
    if built is None:
        print(" FAILED: built overlay executable not found")
        return False

    try:
        shutil.copy2(built, OVERLAY_OUT)
    except OSError as exc:
        print(f" FAILED: could not replace {OVERLAY_OUT}: {exc}")
        print("  Stop any running overlay process and re-run bootstrap.")
        return False

    if not _sync_overlay_runtime_files():
        return False

    required = (
        OVERLAY_OUT,
        os.path.join(_ROOT, "onnxruntime.dll"),
        os.path.join(_ROOT, "DirectML.dll"),
    )
    if not all(os.path.isfile(path) for path in required):
        print(" FAILED: overlay runtime layout is incomplete after build")
        return False

    print(f"  {os.path.getsize(OVERLAY_OUT)//1024} KB")
    print("  OK  overlay executable + ONNX Runtime + DirectML")
    return True


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
