"""Run compiled policies, production HLSL, and DirectML integration; retain evidence."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
from launcher.native_provenance import verify_native_build

ROOT = Path(__file__).resolve().parents[1]

def main() -> int:
    cmake = next((ROOT / "vendor" / "_mingw64").rglob("ctest.exe"), None)
    if cmake is None:
        raise SystemExit("Run the verified bootstrap before native tests")
    env = os.environ.copy()
    env["PATH"] = str(cmake.parent) + os.pathsep + env.get("PATH", "")
    out = ROOT / "release"
    out.mkdir(exist_ok=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    verify_native_build(ROOT, sha)
    run = subprocess.run([str(cmake), "--test-dir", str(ROOT / "overlay" / "build_mingw"), "--output-on-failure", "--no-tests=error", "--output-junit", str(out / "native-tests.xml"), "-C", "Release"], env=env, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=180)
    (out / "native-tests.log").write_text(run.stdout + run.stderr, encoding="utf-8")
    (out / "native-tests.json").write_text(json.dumps({"source_commit": sha, "passed": run.returncode == 0, "tier": "compiled-native-and-synthetic-DirectML", "hardware_acceptance": False}, indent=2) + "\n", encoding="utf-8")
    print(run.stdout + run.stderr)
    return run.returncode

if __name__ == "__main__":
    raise SystemExit(main())
