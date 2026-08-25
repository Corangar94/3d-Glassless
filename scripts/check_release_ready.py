"""Validate legal, version, acceptance, and package prerequisites for release."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import tomllib


def project_version(path: Path) -> str:
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    value = data.get("project", {}).get("version")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing [project].version in {path}")
    return value.strip()


def expected_tags(version: str) -> tuple[str, ...]:
    """Return conventional tag spellings for one PEP 440 project version."""
    tags = {f"v{version}"}
    match = re.fullmatch(
        r"(?P<base>\d+(?:\.\d+){1,2})(?P<kind>a|b|rc)(?P<number>\d+)",
        version,
    )
    if match:
        kind = {"a": "alpha", "b": "beta", "rc": "rc"}[match.group("kind")]
        tags.add(f"v{match.group('base')}-{kind}{match.group('number')}")
        if match.group("kind") == "rc":
            tags.add(f"v{match.group('base')}-rc{match.group('number')}")
    return tuple(sorted(tags))


def _non_placeholder_text(path: Path, *, minimum_length: int = 80) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    lowered = text.casefold()
    if len(text) < minimum_length:
        return False
    placeholders = (
        "todo",
        "choose a license",
        "license pending",
        "placeholder",
        "not yet selected",
    )
    return not any(marker in lowered for marker in placeholders)


def validate_release_ready(
    *,
    project_root: Path,
    tag: str,
    acceptance_path: Path,
    package_summary_path: Path,
) -> tuple[bool, tuple[str, ...], dict[str, object]]:
    failures: list[str] = []
    version = project_version(project_root / "pyproject.toml")
    accepted_tags = expected_tags(version)
    if tag not in accepted_tags:
        failures.append(
            f"tag {tag!r} does not match project version {version!r}; "
            f"expected one of {', '.join(accepted_tags)}"
        )

    license_path = project_root / "LICENSE"
    if not _non_placeholder_text(license_path, minimum_length=200):
        failures.append(
            "a reviewed, non-placeholder root LICENSE file is required before publishing"
        )
    notices_path = project_root / "THIRD_PARTY_NOTICES.md"
    if not _non_placeholder_text(notices_path, minimum_length=200):
        failures.append(
            "a reviewed THIRD_PARTY_NOTICES.md is required before publishing"
        )

    acceptance: dict[str, object] = {}
    if not acceptance_path.is_file():
        failures.append(f"software acceptance report is missing: {acceptance_path}")
    else:
        parsed = json.loads(acceptance_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            failures.append("software acceptance report is not a JSON object")
        else:
            acceptance = parsed
            if parsed.get("passed") is not True:
                failures.append("software acceptance did not pass")

    package_summary: dict[str, object] = {}
    if not package_summary_path.is_file():
        failures.append(f"package summary is missing: {package_summary_path}")
    else:
        parsed = json.loads(package_summary_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            failures.append("package summary is not a JSON object")
        else:
            package_summary = parsed
            if parsed.get("license_present") is not True:
                failures.append("packaged bundle does not contain LICENSE")
            if parsed.get("third_party_notices_present") is not True:
                failures.append(
                    "packaged bundle does not contain THIRD_PARTY_NOTICES.md"
                )
            if parsed.get("software_acceptance_passed") is not True:
                failures.append(
                    "packaged bundle does not record passing software acceptance"
                )
            archive = parsed.get("archive")
            if not isinstance(archive, str) or not Path(archive).is_file():
                failures.append("packaged release archive is missing")

    details = {
        "version": version,
        "tag": tag,
        "accepted_tags": accepted_tags,
        "license": str(license_path),
        "third_party_notices": str(notices_path),
        "acceptance": acceptance,
        "package": package_summary,
        "passed": not failures,
        "failures": failures,
    }
    return not failures, tuple(failures), details


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Glassless3D prerelease publication requirements"
    )
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--tag", required=True)
    parser.add_argument(
        "--acceptance",
        type=Path,
        default=Path("software_acceptance/software_acceptance.json"),
    )
    parser.add_argument(
        "--package-summary",
        type=Path,
        default=Path("release/package-summary.json"),
    )
    parser.add_argument("--output-json", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        passed, failures, details = validate_release_ready(
            project_root=args.project_root.resolve(),
            tag=args.tag,
            acceptance_path=args.acceptance.resolve(),
            package_summary_path=args.package_summary.resolve(),
        )
        encoded = json.dumps(details, indent=2, sort_keys=True)
        print(encoded)
        if args.output_json is not None:
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(
                encoded + "\n", encoding="utf-8", newline="\n"
            )
        if not passed:
            for failure in failures:
                print(f"release blocker: {failure}", file=sys.stderr)
            return 1
        return 0
    except Exception as error:
        print(f"Release readiness validation failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
