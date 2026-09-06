"""Install the reviewed Windows dependency closure without resolving new versions."""
from __future__ import annotations
import json
import os
import platform
import re
import venv
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def validate_locked_versions(lock: Path, installed: dict[str, str]) -> None:
    normalize = lambda name: re.sub(r"[-_.]+", "-", name).lower()
    expected = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s]+)", line)
        if match:
            expected[normalize(match[1])] = match[2]
    actual = {normalize(k): v for k, v in installed.items() if normalize(k) != "glassless3d"}
    if not expected or actual != expected:
        extra = sorted(actual.keys() - expected.keys())
        changed = sorted(name for name in expected if actual.get(name) != expected[name])
        raise RuntimeError(f"Environment differs from release lock; use a clean venv. Extra: {extra}; mismatched/missing: {changed}")


def main() -> int:
    # pip inspect can include non-ASCII package metadata on Windows runners.
    # Force UTF-8 for subprocess output even when the caller uses a legacy code page.
    os.environ["PYTHONUTF8"] = "1"
    if sys.platform != "win32" or sys.version_info[:2] not in ((3, 11), (3, 12)) or sys.maxsize <= 2**32:
        raise SystemExit("Release locks support Windows x64 Python 3.11/3.12 only")
    if sys.prefix == sys.base_prefix:
        # Never mix the release closure with preinstalled GitHub runner tools.
        directory = ROOT / ".venv"
        python = directory / "Scripts" / "python.exe"
        if not python.exists():
            venv.EnvBuilder(with_pip=True).create(directory)
        subprocess.run([str(python), "-m", "scripts.locked_environment"], cwd=ROOT, check=True)
        github_path = os.environ.get("GITHUB_PATH")
        if github_path:
            with open(github_path, "a", encoding="utf-8") as stream:
                stream.write(str(python.parent) + "\n")
        else:
            print(f"Activate {directory / 'Scripts' / 'Activate.ps1'} before running project commands")
        return 0
    lock = ROOT / "requirements" / f"windows-py{sys.version_info.major}{sys.version_info.minor}.lock"
    subprocess.run([sys.executable, "-m", "pip", "install", "--require-hashes", "--only-binary=:all:", "-r", str(lock)], check=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-build-isolation", "-e", str(ROOT)], check=True)
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    output = ROOT / "release"
    output.mkdir(exist_ok=True)
    result = subprocess.run([sys.executable, "-m", "pip", "inspect"], check=True, capture_output=True, text=True, encoding="utf-8")
    (output / "resolved-environment.json").write_text(result.stdout, encoding="utf-8")
    inspected = json.loads(result.stdout)
    validate_locked_versions(lock, {item["metadata"]["name"]: item["metadata"]["version"] for item in inspected["installed"]})
    print(f"Verified {lock.name} on {platform.python_version()}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
