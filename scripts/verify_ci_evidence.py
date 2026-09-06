"""Fail closed unless the exact release SHA has all required successful checks."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import urllib.request

REQUIRED_CHECKS = frozenset((
    "full-windows-tests (3.11)", "full-windows-tests (3.12)",
    "python-dependency-audit", "portable-native-policies",
    "build-windows-overlay", "package-windows-x64",
))

def validate_checks(checks: list[dict], sha: str) -> list[str]:
    newest: dict[str, dict] = {}
    for check in checks:
        name = check.get("name")
        if name not in REQUIRED_CHECKS or check.get("head_sha") != sha:
            continue
        if check.get("app", {}).get("slug") != "github-actions":
            continue
        if name not in newest or check.get("id", 0) > newest[name].get("id", 0):
            newest[name] = check
    return [name for name in sorted(REQUIRED_CHECKS)
            if name not in newest or newest[name].get("status") != "completed"
            or newest[name].get("conclusion") != "success"]

def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "Corangar94/3d-Glassless")
    if repository != "Corangar94/3d-Glassless":
        raise SystemExit("Release evidence must come from the canonical repository")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("GitHub token required to verify release checks")
    checks = []
    for page in range(1, 21):
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repository}/commits/{sha}/check-runs?per_page=100&page={page}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        with urllib.request.urlopen(request, timeout=30) as response:
            batch = json.load(response)["check_runs"]
        checks.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise SystemExit("Check listing exceeded pagination bound")
    missing = validate_checks(checks, sha)
    out = root / "release" / "ci-evidence.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"source_commit": sha, "required": sorted(REQUIRED_CHECKS), "unsatisfied": missing, "passed": not missing}, indent=2) + "\n", encoding="utf-8")
    if missing:
        raise SystemExit("Required exact-SHA checks missing or unsuccessful: " + ", ".join(missing))
    print(f"All {len(REQUIRED_CHECKS)} required checks passed for {sha}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
