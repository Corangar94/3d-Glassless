#!/usr/bin/env python3
"""Hardened entry point for the Glassless3D bootstrap.

The build orchestration remains in ``_bootstrap_core``. This module wraps its
network and archive operations so downloads are atomic/HTTPS-only and ZIP
members cannot escape their intended extraction directory.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable

if __package__:
    from . import _bootstrap_core as _core
else:  # ``python scripts/bootstrap.py``
    import _bootstrap_core as _core  # type: ignore[no-redef]

# Preserve the original public/internal API for callers and existing tests.
for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)


def _download(url: str, dest: str, label: str, *, sha256: str | None = None) -> None:
    """Download an HTTPS asset atomically, optionally verifying SHA-256."""
    if urllib.parse.urlsplit(url).scheme.lower() != "https":
        raise RuntimeError(f"refusing non-HTTPS download for {label}")

    if os.path.exists(dest):
        if sha256 and _core._sha256(dest).lower() != sha256.lower():
            raise RuntimeError(
                f"SHA-256 mismatch for existing {os.path.relpath(dest, _core._ROOT)}"
            )
        print(f"  already present: {os.path.relpath(dest, _core._ROOT)}")
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

        if sha256 and _core._sha256(temp_path).lower() != sha256.lower():
            raise RuntimeError(f"SHA-256 mismatch for downloaded {label}")
        os.replace(temp_path, dest)
        temp_path = None
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.remove(temp_path)
    print(f" {os.path.getsize(dest) // 1024} KB")


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


def step_reshade_sdk() -> bool:
    """Download and safely extract the pinned ReShade SDK headers."""
    print("\n[experimental] ReShade SDK headers")
    main_header = os.path.join(SDK_INCLUDE, "reshade.hpp")
    if os.path.isfile(main_header) and _core._sha256(main_header) == SDK_HEADER_SHA256:
        print("  already present and version-verified: vendor/reshade/include/")
        return True
    try:
        _download(
            SDK_ZIP_URL,
            SDK_ZIP,
            f"ReShade {RESHADE_VERSION} source (~2 MB)",
            sha256=SDK_ZIP_SHA256,
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

    if not os.path.isfile(main_header) or _core._sha256(main_header) != SDK_HEADER_SHA256:
        print(" FAILED: extracted SDK header hash mismatch")
        return False
    print(f" {len(os.listdir(SDK_INCLUDE))} files")
    print("  OK  vendor/reshade/include/")
    return True


# Patch every core call site, including functions defined in that module.
_core._download = _download
_core._extract_from_nupkg = _extract_from_nupkg
_core.step_reshade_sdk = step_reshade_sdk


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
