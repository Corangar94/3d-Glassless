#!/usr/bin/env python3
"""Hardened entry point for the Glassless3D bootstrap.

The build orchestration remains in ``_bootstrap_core``. This module wraps its
network, extraction, and toolchain discovery paths so every downloaded runtime
input is immutable, hash-pinned, and revalidated before use.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

if __package__:
    from . import _bootstrap_core as _core
else:  # ``python scripts/bootstrap.py``
    import _bootstrap_core as _core  # type: ignore[no-redef]

# Preserve the original public/internal API for callers and existing tests.
for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)


# Primary runtime assets. URLs identify immutable versions and every byte stream
# is pinned by a repository-maintained SHA-256.
FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_landmarker/face_landmarker/float16/1/face_landmarker.task"
)
FACE_MODEL_SHA256 = (
    "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
)
FACE_MODEL_DEST = os.path.join(_core._ROOT, "models", "face_landmarker.task")

ORT_NUPKG_URL = (
    "https://api.nuget.org/v3-flatcontainer/"
    "microsoft.ml.onnxruntime.directml/1.20.1/"
    "microsoft.ml.onnxruntime.directml.1.20.1.nupkg"
)
ORT_NUPKG_SHA256 = (
    "6763468507b7cfc777b1334b3e174c11a540ddacb7bd4354bc2e0ec89e56eec2"
)
DML_NUPKG_URL = (
    "https://api.nuget.org/v3-flatcontainer/"
    "microsoft.ai.directml/1.15.2/"
    "microsoft.ai.directml.1.15.2.nupkg"
)
DML_NUPKG_SHA256 = (
    "9f07482559087088a4dba4ae76eeeee1fad3f7077a92ccfbdb439c6bc2964c09"
)
ORT_TREE_SHA256 = (
    "d2283477f4fd1ab9a26f3ba9aa930c36aa4b69e13b1b5a25f8fd86a136fbadeb"
)
DML_TREE_SHA256 = (
    "3530d61ca4c87e15ac71c3e141dd819de7b2c5bdbf6d8e74f6797099858b95a0"
)

DEPTH_MODEL_REVISION = "c70d1ddbcd93c9bda8098268cc3554adf5e8dd4f"
DEPTH_MODEL_URL = (
    "https://huggingface.co/onnx-community/depth-anything-v2-small/"
    f"resolve/{DEPTH_MODEL_REVISION}/onnx/model_fp16.onnx"
)
DEPTH_MODEL_SHA256 = (
    "2df6223f206b5164e21f664ace61dabeb9bb6a49b8b5a3e00510b4807d0f5b04"
)

MINGW64_ARCHIVE_SHA256 = (
    "daf82b7bb6cb2d6b4cb5e630d3b4d37e75a9de31589aac8ff91f9228e2f97376"
)
MINGW64_TREE_SHA256 = (
    "2f0095b04a9211616da721948621157af7b7ebde6160d91114b65d9ae967e66a"
)

_verified_mingw_paths: tuple[str, str] | None = None


def _sha256(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(path: str | os.PathLike[str]) -> str:
    """Format a path safely even when it is on another Windows drive."""
    try:
        return os.path.relpath(path, _core._ROOT)
    except ValueError:
        return os.path.abspath(path)


def _download(url: str, dest: str, label: str, *, sha256: str | None = None) -> None:
    """Download an HTTPS asset atomically, optionally verifying SHA-256."""
    if urllib.parse.urlsplit(url).scheme.lower() != "https":
        raise RuntimeError(f"refusing non-HTTPS download for {label}")

    if os.path.exists(dest):
        if sha256 and _sha256(dest).lower() != sha256.lower():
            raise RuntimeError(
                f"SHA-256 mismatch for existing {_display_path(dest)}"
            )
        print(f"  already present: {_display_path(dest)}")
        return

    destination_dir = os.path.dirname(dest) or "."
    os.makedirs(destination_dir, exist_ok=True)
    print(f"  downloading {label}...", end="", flush=True)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=destination_dir,
            prefix=f".{os.path.basename(dest)}.",
            suffix=".part",
        ) as temp_file:
            temp_path = temp_file.name
            with urllib.request.urlopen(request, timeout=60) as response:
                final_url = response.geturl()
                if urllib.parse.urlsplit(final_url).scheme.lower() != "https":
                    raise RuntimeError(
                        f"refusing HTTPS downgrade while downloading {label}"
                    )
                shutil.copyfileobj(response, temp_file)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        if sha256 and _sha256(temp_path).lower() != sha256.lower():
            raise RuntimeError(f"SHA-256 mismatch for downloaded {label}")
        os.replace(temp_path, dest)
        temp_path = None
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.remove(temp_path)
    print(f" {os.path.getsize(dest) // 1024} KB")


def _ensure_verified_asset(
    url: str,
    destination: str,
    label: str,
    sha256: str,
) -> None:
    """Reuse an exact cached asset or replace it from its pinned HTTPS URL."""
    if os.path.exists(destination) and _sha256(destination).lower() != sha256.lower():
        print(
            "  cached asset failed SHA-256 verification; replacing: "
            f"{_display_path(destination)}"
        )
        os.remove(destination)
    _download(url, destination, label, sha256=sha256)


def _safe_archive_destination(root: str, relative: str) -> str:
    """Return a contained extraction path, rejecting traversal and NTFS ADS."""
    normalized = relative.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if (
        not parts
        or normalized.startswith("/")
        or any(part == ".." or ":" in part for part in parts)
    ):
        raise RuntimeError(f"unsafe archive member: {relative!r}")

    root_real = os.path.realpath(root)
    destination = os.path.realpath(os.path.join(root_real, *parts))
    try:
        contained = os.path.commonpath((root_real, destination)) == root_real
    except ValueError:
        contained = False
    if not contained:
        raise RuntimeError(f"unsafe archive member: {relative!r}")
    return destination


def _extract_from_nupkg(nupkg_path: str, prefix: str, dest_dir: str) -> int:
    """Safely extract matching NuGet package members into ``dest_dir``."""
    count = 0
    with zipfile.ZipFile(nupkg_path) as archive:
        for name in archive.namelist():
            if not name.startswith(prefix) or name.endswith("/"):
                continue
            relative = name[len(prefix):]
            if not relative:
                continue
            destination = _safe_archive_destination(dest_dir, relative)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with archive.open(name) as source, open(destination, "wb") as target:
                shutil.copyfileobj(source, target)
            count += 1
    return count


def _tree_digest(root: str | os.PathLike[str]) -> str | None:
    """Hash a directory's relative paths and every file's SHA-256."""
    root_path = Path(root)
    if not root_path.is_dir():
        return None
    files = sorted(
        (path for path in root_path.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root_path).as_posix(),
    )
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root_path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _tree_matches(root: str, expected_sha256: str) -> bool:
    actual = _tree_digest(root)
    return actual is not None and actual.lower() == expected_sha256.lower()


def _replace_directories(replacements: list[tuple[str, str]]) -> None:
    """Replace same-volume directories and roll back the set on failure."""
    backups: list[tuple[str, str | None]] = []
    installed: list[str] = []
    try:
        for source, destination in replacements:
            backup: str | None = None
            if os.path.exists(destination):
                backup = f"{destination}.backup-{os.getpid()}"
                if os.path.exists(backup):
                    shutil.rmtree(backup)
                os.replace(destination, backup)
            backups.append((destination, backup))
            os.replace(source, destination)
            installed.append(destination)
    except Exception:
        for destination in reversed(installed):
            if os.path.isdir(destination):
                shutil.rmtree(destination)
        for destination, backup in reversed(backups):
            if backup is not None and os.path.exists(backup):
                os.replace(backup, destination)
        raise
    else:
        for _destination, backup in backups:
            if backup is not None and os.path.exists(backup):
                shutil.rmtree(backup)


def step_face_model() -> bool:
    print("\n[primary] Face landmarker model")
    try:
        _ensure_verified_asset(
            FACE_MODEL_URL,
            FACE_MODEL_DEST,
            "face_landmarker.task (~4 MB)",
            FACE_MODEL_SHA256,
        )
    except Exception as error:
        print(f"  FAIL  {error}")
        return False
    print("  OK  models/face_landmarker.task (SHA-256 verified)")
    return True


def step_onnxruntime() -> bool:
    """Install exact, tree-verified ONNX Runtime and DirectML layouts."""
    print("\n[primary] ONNX Runtime + DirectML (for depth inference)")
    if _tree_matches(ORT_DIR, ORT_TREE_SHA256) and _tree_matches(
        DML_DIR, DML_TREE_SHA256
    ):
        print("  already present and tree-verified: vendor/onnxruntime + vendor/directml")
        return True

    for path, name in ((ORT_DIR, "ONNX Runtime"), (DML_DIR, "DirectML")):
        if os.path.exists(path):
            print(f"  cached {name} tree failed verification; rebuilding")

    try:
        _ensure_verified_asset(
            ORT_NUPKG_URL,
            ORT_NUPKG,
            f"ONNX Runtime DirectML {ORT_VERSION} (~16 MB)",
            ORT_NUPKG_SHA256,
        )
        _ensure_verified_asset(
            DML_NUPKG_URL,
            DML_NUPKG,
            f"DirectML {DML_VERSION} (~193 MB)",
            DML_NUPKG_SHA256,
        )
    except Exception as error:
        print(f"  FAIL  download: {error}")
        return False

    vendor_dir = os.path.dirname(ORT_DIR)
    temp_root = tempfile.mkdtemp(prefix=".g3d-dependencies-", dir=vendor_dir)
    temp_ort = os.path.join(temp_root, "onnxruntime")
    temp_dml = os.path.join(temp_root, "directml")
    try:
        counts = (
            _extract_from_nupkg(
                ORT_NUPKG,
                "runtimes/win-x64/native/",
                os.path.join(temp_ort, "lib"),
            ),
            _extract_from_nupkg(
                ORT_NUPKG,
                "build/native/include/",
                os.path.join(temp_ort, "include"),
            ),
            _extract_from_nupkg(
                DML_NUPKG,
                "bin/x64-win/",
                os.path.join(temp_dml, "lib"),
            ),
            _extract_from_nupkg(
                DML_NUPKG,
                "lib/x64-win/",
                os.path.join(temp_dml, "lib"),
            ),
            _extract_from_nupkg(
                DML_NUPKG,
                "include/",
                os.path.join(temp_dml, "include"),
            ),
        )
        if not all((counts[0], counts[1], counts[2], counts[4])):
            raise RuntimeError(f"required package members missing: counts={counts}")
        if not _tree_matches(temp_ort, ORT_TREE_SHA256):
            raise RuntimeError("extracted ONNX Runtime tree digest mismatch")
        if not _tree_matches(temp_dml, DML_TREE_SHA256):
            raise RuntimeError("extracted DirectML tree digest mismatch")

        _replace_directories([(temp_ort, ORT_DIR), (temp_dml, DML_DIR)])
        if not _tree_matches(ORT_DIR, ORT_TREE_SHA256):
            raise RuntimeError("installed ONNX Runtime tree failed verification")
        if not _tree_matches(DML_DIR, DML_TREE_SHA256):
            raise RuntimeError("installed DirectML tree failed verification")
    except Exception as error:
        print(f"  FAIL  install: {error}")
        return False
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
        for archive in (ORT_NUPKG, DML_NUPKG):
            if os.path.isfile(archive):
                os.remove(archive)

    ort_dll = os.path.join(ORT_DIR, "lib", "onnxruntime.dll")
    dml_dll = os.path.join(DML_DIR, "lib", "DirectML.dll")
    if not (os.path.isfile(ort_dll) and os.path.isfile(dml_dll)):
        print("  FAIL  expected runtime DLLs missing after verified extraction")
        return False
    print(
        f"  OK  onnxruntime.dll ({os.path.getsize(ort_dll) // 1024} KB) "
        f"+ DirectML.dll ({os.path.getsize(dml_dll) // 1024} KB), tree-verified"
    )
    return True


def step_depth_model() -> bool:
    print("\n[primary] Depth Anything V2 Small (monocular depth model)")
    try:
        _ensure_verified_asset(
            DEPTH_MODEL_URL,
            DEPTH_MODEL_DEST,
            "depth_anything_v2_small_fp16.onnx (~50 MB)",
            DEPTH_MODEL_SHA256,
        )
    except Exception as error:
        print(f"  FAIL  {error}")
        return False
    print("  OK  models/depth_anything_v2_small_fp16.onnx (immutable + verified)")
    return True


def step_reshade_sdk() -> bool:
    """Download and safely extract the pinned ReShade SDK headers."""
    print("\n[experimental] ReShade SDK headers")
    main_header = os.path.join(SDK_INCLUDE, "reshade.hpp")
    if os.path.isfile(main_header) and _sha256(main_header) == SDK_HEADER_SHA256:
        print("  already present and version-verified: vendor/reshade/include/")
        return True
    try:
        _ensure_verified_asset(
            SDK_ZIP_URL,
            SDK_ZIP,
            f"ReShade {RESHADE_VERSION} source (~2 MB)",
            SDK_ZIP_SHA256,
        )
    except Exception as error:
        print(f"  FAIL  download: {error}")
        return False

    print("  extracting include/ headers...", end="", flush=True)
    prefix = f"reshade-{RESHADE_VERSION}/include/"
    try:
        with zipfile.ZipFile(SDK_ZIP) as archive:
            members = [name for name in archive.namelist() if name.startswith(prefix)]
            if not members:
                print(" FAILED: include/ not found in archive")
                return False
            for member in members:
                relative = member[len(prefix):]
                if not relative or member.endswith("/"):
                    continue
                destination = _safe_archive_destination(SDK_INCLUDE, relative)
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                with archive.open(member) as source, open(destination, "wb") as target:
                    shutil.copyfileobj(source, target)
    except Exception as error:
        print(f" FAILED: {error}")
        return False
    finally:
        if os.path.isfile(SDK_ZIP):
            os.remove(SDK_ZIP)

    if not os.path.isfile(main_header) or _sha256(main_header) != SDK_HEADER_SHA256:
        print(" FAILED: extracted SDK header hash mismatch")
        return False
    print(f" {len(os.listdir(SDK_INCLUDE))} files")
    print("  OK  vendor/reshade/include/")
    return True


def _locate_mingw_binary(name: str) -> str | None:
    preferred = os.path.join(_MINGW_DIR, "mingw64", "bin", name)
    if os.path.isfile(preferred):
        return preferred
    for root, _dirs, files in os.walk(_MINGW_DIR):
        if name in files:
            return os.path.join(root, name)
    return None


def _ensure_mingw_toolchain() -> tuple[str, str] | None:
    global _verified_mingw_paths
    if _verified_mingw_paths is not None:
        gpp, cmake = _verified_mingw_paths
        if os.path.isfile(gpp) and os.path.isfile(cmake):
            return _verified_mingw_paths
        _verified_mingw_paths = None

    if _tree_matches(_MINGW_DIR, MINGW64_TREE_SHA256):
        gpp = _locate_mingw_binary("g++.exe")
        cmake = _locate_mingw_binary("cmake.exe")
        if gpp and cmake:
            _verified_mingw_paths = (gpp, cmake)
            return _verified_mingw_paths

    if os.path.isdir(_MINGW_DIR):
        print("  cached MinGW tree failed verification; rebuilding")

    seven_zip = _find_7zip()
    if not seven_zip:
        print("  FAIL  7-Zip not found; cannot install pinned MinGW.")
        return None

    try:
        _ensure_verified_asset(
            _MINGW_URL,
            _MINGW_7Z,
            "MinGW-w64 GCC 14.2.0 (~100 MB, one-time)",
            MINGW64_ARCHIVE_SHA256,
        )
    except Exception as error:
        print(f"  FAIL  MinGW download: {error}")
        return None

    parent = os.path.dirname(_MINGW_DIR)
    temp_root = tempfile.mkdtemp(prefix=".g3d-mingw64-", dir=parent)
    try:
        print("  extracting and verifying MinGW-w64...", end="", flush=True)
        result = subprocess.run(
            [seven_zip, "x", _MINGW_7Z, f"-o{temp_root}", "-y"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-500:] or "7-Zip extraction failed")
        if not _tree_matches(temp_root, MINGW64_TREE_SHA256):
            raise RuntimeError("extracted MinGW tree digest mismatch")
        _replace_directories([(temp_root, _MINGW_DIR)])
        if not _tree_matches(_MINGW_DIR, MINGW64_TREE_SHA256):
            raise RuntimeError("installed MinGW tree failed verification")
    except Exception as error:
        print(f" FAILED: {error}")
        return None
    finally:
        if os.path.isdir(temp_root):
            shutil.rmtree(temp_root, ignore_errors=True)
        if os.path.isfile(_MINGW_7Z):
            os.remove(_MINGW_7Z)

    gpp = _locate_mingw_binary("g++.exe")
    cmake = _locate_mingw_binary("cmake.exe")
    if not gpp or not cmake:
        print(" FAILED: verified MinGW tree lacks g++.exe or cmake.exe")
        return None
    _verified_mingw_paths = (gpp, cmake)
    print(" OK")
    return _verified_mingw_paths


def _find_gcc() -> str | None:
    paths = _ensure_mingw_toolchain()
    return paths[0] if paths else None


def _find_cmake() -> str | None:
    """Return CMake from the same tree-verified toolchain as the compiler."""
    paths = _ensure_mingw_toolchain()
    return paths[1] if paths else None


# Patch every core call site, including functions defined in that module.
_core._sha256 = _sha256
_core._download = _download
_core._extract_from_nupkg = _extract_from_nupkg
_core.step_face_model = step_face_model
_core.step_onnxruntime = step_onnxruntime
_core.step_depth_model = step_depth_model
_core.step_reshade_sdk = step_reshade_sdk
_core._find_gcc = _find_gcc
_core._find_cmake = _find_cmake
_core.ORT_NUPKG_URL = ORT_NUPKG_URL
_core.DML_NUPKG_URL = DML_NUPKG_URL
_core.DEPTH_MODEL_URL = DEPTH_MODEL_URL


def _build_steps(with_reshade: bool) -> list[tuple[str, Callable[[], bool]]]:
    """Build the step list from this module so monkeypatching stays effective."""
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
    print(f"Project root: {_core._ROOT}")
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
