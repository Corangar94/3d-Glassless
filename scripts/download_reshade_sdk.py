#!/usr/bin/env python3
# scripts/download_reshade_sdk.py
# Downloads the ReShade SDK headers required to compile the addon.
# Run once before building the addon.

import os
import sys
import urllib.request
import zipfile
import shutil

RESHADE_VERSION = "5.9.2"
ZIP_URL = (
    f"https://github.com/crosire/reshade/archive/refs/tags/v{RESHADE_VERSION}.zip"
)
DEST_DIR = os.path.join(os.path.dirname(__file__), "..", "vendor", "reshade")
TMP_ZIP = os.path.join(os.path.dirname(__file__), "..", "vendor", "_reshade_src.zip")


def main() -> None:
    os.makedirs(os.path.dirname(TMP_ZIP), exist_ok=True)

    print(f"Downloading ReShade {RESHADE_VERSION} source...")
    urllib.request.urlretrieve(ZIP_URL, TMP_ZIP, _progress)
    print()

    print("Extracting include headers...")
    with zipfile.ZipFile(TMP_ZIP) as zf:
        prefix = f"reshade-{RESHADE_VERSION}/include/"
        members = [m for m in zf.namelist() if m.startswith(prefix)]
        if not members:
            sys.exit("ERROR: Could not find include/ in archive.")
        for member in members:
            relative = member[len(prefix):]
            if not relative:
                continue
            dest = os.path.join(DEST_DIR, "include", relative)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)

    os.remove(TMP_ZIP)
    print(f"ReShade SDK headers saved to vendor/reshade/include/")


def _progress(block_num: int, block_size: int, total_size: int) -> None:
    downloaded = block_num * block_size
    pct = min(downloaded / total_size * 100, 100) if total_size > 0 else 0
    print(f"\r  {pct:.0f}% ({downloaded // 1024} KB)", end="", flush=True)


if __name__ == "__main__":
    main()
