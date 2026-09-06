"""Install the reviewed Windows dependency closure without resolving new versions."""
from __future__ import annotations
import json
import platform
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def main() -> int:
    if sys.platform != "win32" or sys.version_info[:2] not in ((3, 11), (3, 12)) or sys.maxsize <= 2**32:
        raise SystemExit("Release locks support Windows x64 Python 3.11/3.12 only")
    lock = ROOT / "requirements" / f"windows-py{sys.version_info.major}{sys.version_info.minor}.lock"
    subprocess.run([sys.executable, "-m", "pip", "install", "--require-hashes", "--only-binary=:all:", "-r", str(lock)], check=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-build-isolation", "-e", str(ROOT)], check=True)
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    output = ROOT / "release"
    output.mkdir(exist_ok=True)
    result = subprocess.run([sys.executable, "-m", "pip", "inspect"], check=True, capture_output=True, text=True, encoding="utf-8")
    (output / "resolved-environment.json").write_text(result.stdout, encoding="utf-8")
    print(f"Verified {lock.name} on {platform.python_version()}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
