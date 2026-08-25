"""Create a deterministic, traceable Windows standalone release bundle."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile


_REQUIRED_RUNTIME_PATHS = (
    Path("Glassless3D.exe"),
    Path("_internal/Glassless3DOverlay.exe"),
    Path("_internal/onnxruntime.dll"),
    Path("_internal/DirectML.dll"),
    Path("_internal/models/face_landmarker.task"),
    Path("_internal/models/depth_anything_v2_small_fp16.onnx"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_version(pyproject_path: Path) -> str:
    with pyproject_path.open("rb") as stream:
        data = tomllib.load(stream)
    value = data.get("project", {}).get("version")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing [project].version in {pyproject_path}")
    return value.strip()


def _git_output(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _source_commit(explicit: str | None) -> str:
    value = explicit or os.environ.get("GITHUB_SHA") or _git_output("rev-parse", "HEAD")
    return value or "unknown"


def _source_date_epoch(explicit: int | None) -> int:
    if explicit is not None:
        return explicit
    environment = os.environ.get("SOURCE_DATE_EPOCH")
    if environment:
        try:
            return max(315532800, int(environment))
        except ValueError:
            pass
    commit_epoch = _git_output("show", "-s", "--format=%ct", "HEAD")
    if commit_epoch:
        try:
            return max(315532800, int(commit_epoch))
        except ValueError:
            pass
    # ZIP timestamps cannot predate 1980. A fixed fallback keeps local builds
    # deterministic when Git metadata is unavailable.
    return 315532800


def _safe_label(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return normalized.strip("-.") or "unknown"


def _verify_bundle(bundle_dir: Path) -> None:
    missing = [
        relative.as_posix()
        for relative in _REQUIRED_RUNTIME_PATHS
        if not (bundle_dir / relative).is_file()
    ]
    if missing:
        raise FileNotFoundError(
            "standalone bundle is incomplete; missing: " + ", ".join(missing)
        )
    launcher = bundle_dir / "Glassless3D.exe"
    overlay = bundle_dir / "_internal/Glassless3DOverlay.exe"
    if launcher.stat().st_size < 100_000:
        raise ValueError(f"launcher executable is unexpectedly small: {launcher.stat().st_size}")
    if overlay.stat().st_size < 100_000:
        raise ValueError(f"overlay executable is unexpectedly small: {overlay.stat().st_size}")


def _copy_optional(source: Path, destination: Path) -> bool:
    if not source.is_file():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def _native_input_inventory() -> list[dict[str, str]]:
    try:
        from scripts import bootstrap
    except Exception:
        return []
    definitions = (
        (
            "MediaPipe face landmarker model",
            getattr(bootstrap, "FACE_MODEL_URL", ""),
            getattr(bootstrap, "FACE_MODEL_SHA256", ""),
        ),
        (
            "ONNX Runtime DirectML NuGet package",
            getattr(bootstrap, "ORT_NUPKG_URL", ""),
            getattr(bootstrap, "ORT_NUPKG_SHA256", ""),
        ),
        (
            "Microsoft DirectML NuGet package",
            getattr(bootstrap, "DML_NUPKG_URL", ""),
            getattr(bootstrap, "DML_NUPKG_SHA256", ""),
        ),
        (
            "Depth Anything V2 ONNX model",
            getattr(bootstrap, "DEPTH_MODEL_URL", ""),
            getattr(bootstrap, "DEPTH_MODEL_SHA256", ""),
        ),
        (
            "MinGW-w64 build toolchain archive",
            getattr(bootstrap, "_MINGW_URL", ""),
            getattr(bootstrap, "MINGW64_ARCHIVE_SHA256", ""),
        ),
    )
    result = []
    for name, url, digest in definitions:
        if not url or not digest:
            continue
        result.append(
            {
                "name": str(name),
                "source_url": str(url),
                "sha256": str(digest).lower(),
            }
        )
    return result


def _python_build_inventory() -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name") or distribution.metadata.get("Summary")
        if not name:
            continue
        license_value = distribution.metadata.get("License") or ""
        homepage = distribution.metadata.get("Home-page") or ""
        entries.append(
            {
                "name": str(name),
                "version": distribution.version,
                "license_metadata": str(license_value),
                "homepage": str(homepage),
            }
        )
    entries.sort(key=lambda entry: entry["name"].casefold())
    return entries


def _file_inventory(root: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in sorted(
        (candidate for candidate in root.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix()
        if relative in {"SHA256SUMS.txt", "release-manifest.json"}:
            continue
        entries.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return entries


def _acceptance_summary(acceptance_dir: Path | None) -> dict[str, object] | None:
    if acceptance_dir is None:
        return None
    report = acceptance_dir / "software_acceptance.json"
    if not report.is_file():
        return None
    parsed = json.loads(report.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("software_acceptance.json must contain a JSON object")
    return parsed


def _write_checksums(root: Path) -> None:
    lines = []
    for path in sorted(
        (candidate for candidate in root.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix()
        if relative == "SHA256SUMS.txt":
            continue
        lines.append(f"{_sha256(path)}  {relative}")
    (root / "SHA256SUMS.txt").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _zip_datetime(epoch: int) -> tuple[int, int, int, int, int, int]:
    value = datetime.fromtimestamp(epoch, timezone.utc)
    year = min(2107, max(1980, value.year))
    # ZIP stores seconds with two-second precision.
    return year, value.month, value.day, value.hour, value.minute, value.second // 2 * 2


def _write_deterministic_zip(source_dir: Path, archive: Path, epoch: int) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    root_name = source_dir.name
    timestamp = _zip_datetime(epoch)
    with zipfile.ZipFile(
        archive,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as bundle:
        for path in sorted(
            (candidate for candidate in source_dir.rglob("*") if candidate.is_file()),
            key=lambda candidate: candidate.relative_to(source_dir).as_posix(),
        ):
            relative = Path(root_name) / path.relative_to(source_dir)
            info = zipfile.ZipInfo(relative.as_posix(), date_time=timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            with path.open("rb") as stream:
                bundle.writestr(info, stream.read(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def package_windows_release(
    *,
    bundle_dir: Path,
    output_dir: Path,
    project_root: Path,
    version: str,
    commit: str,
    source_date_epoch: int,
    acceptance_dir: Path | None = None,
    sbom_path: Path | None = None,
) -> dict[str, object]:
    _verify_bundle(bundle_dir)
    package_name = f"Glassless3D-{_safe_label(version)}-windows-x64"
    staging = output_dir / package_name
    if staging.exists():
        shutil.rmtree(staging)
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(bundle_dir, staging)

    documentation = staging / "documentation"
    documentation.mkdir(parents=True, exist_ok=True)
    _copy_optional(project_root / "README.md", documentation / "README.md")
    _copy_optional(
        project_root / "docs" / "TROUBLESHOOTING.md",
        documentation / "TROUBLESHOOTING.md",
    )
    _copy_optional(
        project_root / "docs" / "ARCHITECTURE.md",
        documentation / "ARCHITECTURE.md",
    )
    license_present = _copy_optional(project_root / "LICENSE", staging / "LICENSE")
    notices_present = _copy_optional(
        project_root / "THIRD_PARTY_NOTICES.md",
        staging / "THIRD_PARTY_NOTICES.md",
    )
    if not license_present:
        (staging / "UNLICENSED_PREVIEW.txt").write_text(
            "This build was produced before the Glassless3D project selected a "
            "software license. It is a CI evaluation artifact and does not grant "
            "redistribution rights.\n",
            encoding="utf-8",
            newline="\n",
        )
    if acceptance_dir is not None and acceptance_dir.is_dir():
        shutil.copytree(
            acceptance_dir,
            documentation / "software_acceptance",
            dirs_exist_ok=True,
        )
    if sbom_path is not None and sbom_path.is_file():
        shutil.copy2(sbom_path, staging / "SBOM.cdx.json")

    acceptance = _acceptance_summary(acceptance_dir)
    manifest = {
        "schema_version": 1,
        "product": {
            "name": "Glassless3D",
            "version": version,
            "platform": "windows-x64",
            "entrypoint": "Glassless3D.exe",
        },
        "source": {
            "repository": "https://github.com/Corangar94/3d-Glassless",
            "commit": commit,
            "source_date_epoch": source_date_epoch,
            "source_date_utc": datetime.fromtimestamp(
                source_date_epoch, timezone.utc
            ).isoformat(),
        },
        "build": {
            "python": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "pyinstaller": importlib.metadata.version("pyinstaller"),
        },
        "release_readiness": {
            "project_license_present": license_present,
            "third_party_notices_present": notices_present,
            "software_acceptance_present": acceptance is not None,
            "software_acceptance_passed": (
                bool(acceptance.get("passed")) if acceptance is not None else None
            ),
        },
        "native_inputs": _native_input_inventory(),
        "python_build_environment": _python_build_inventory(),
        "files": _file_inventory(staging),
    }
    (staging / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _write_checksums(staging)

    archive = output_dir / f"{package_name}.zip"
    if archive.exists():
        archive.unlink()
    _write_deterministic_zip(staging, archive, source_date_epoch)
    archive_digest = _sha256(archive)
    digest_file = archive.with_suffix(archive.suffix + ".sha256")
    digest_file.write_text(
        f"{archive_digest}  {archive.name}\n",
        encoding="utf-8",
        newline="\n",
    )
    top_manifest = output_dir / f"{package_name}.manifest.json"
    shutil.copy2(staging / "release-manifest.json", top_manifest)
    if (staging / "SBOM.cdx.json").is_file():
        shutil.copy2(
            staging / "SBOM.cdx.json",
            output_dir / f"{package_name}.sbom.cdx.json",
        )
    return {
        "package_name": package_name,
        "staging_directory": str(staging),
        "archive": str(archive),
        "archive_sha256": archive_digest,
        "manifest": str(top_manifest),
        "license_present": license_present,
        "third_party_notices_present": notices_present,
        "software_acceptance_passed": (
            bool(acceptance.get("passed")) if acceptance is not None else None
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a deterministic Glassless3D Windows release bundle"
    )
    parser.add_argument("--bundle-dir", type=Path, default=Path("dist/Glassless3D"))
    parser.add_argument("--output-dir", type=Path, default=Path("release"))
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--version")
    parser.add_argument("--commit")
    parser.add_argument("--source-date-epoch", type=int)
    parser.add_argument("--acceptance-dir", type=Path)
    parser.add_argument("--sbom", type=Path)
    parser.add_argument("--summary-json", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        project_root = args.project_root.resolve()
        version = args.version or _project_version(args.pyproject)
        summary = package_windows_release(
            bundle_dir=args.bundle_dir.resolve(),
            output_dir=args.output_dir.resolve(),
            project_root=project_root,
            version=version,
            commit=_source_commit(args.commit),
            source_date_epoch=_source_date_epoch(args.source_date_epoch),
            acceptance_dir=(
                args.acceptance_dir.resolve() if args.acceptance_dir else None
            ),
            sbom_path=args.sbom.resolve() if args.sbom else None,
        )
        encoded = json.dumps(summary, indent=2, sort_keys=True)
        print(encoded)
        if args.summary_json is not None:
            args.summary_json.parent.mkdir(parents=True, exist_ok=True)
            args.summary_json.write_text(
                encoded + "\n", encoding="utf-8", newline="\n"
            )
        return 0
    except Exception as error:
        print(f"Windows release packaging failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
