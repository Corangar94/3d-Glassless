"""Analyze and enforce Glassless3D standalone bundle-size contracts."""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
from typing import Iterable


BASELINE_ARCHIVE_BYTES = 214_061_394
BASELINE_UNPACKED_BYTES = 465_479_795
DEFAULT_MAX_ARCHIVE_BYTES = 200_000_000
DEFAULT_MAX_UNPACKED_BYTES = 420_000_000

FORBIDDEN_PATH_PREFIXES: tuple[str, ...] = (
    "_internal/matplotlib/",
    "_internal/PIL/",
    "_internal/_sounddevice_data/",
    "_internal/mediapipe/tasks/python/audio/",
    "_internal/mediapipe/tasks/python/genai/",
    "_internal/mediapipe/tasks/python/metadata/",
    "_internal/mediapipe/tasks/python/text/",
    "_internal/mediapipe/tasks/python/test/",
    "_internal/mediapipe/tasks/python/vision/drawing_styles",
    "_internal/mediapipe/tasks/python/vision/drawing_utils",
    "_internal/mediapipe/tasks/python/vision/face_detector",
    "_internal/mediapipe/tasks/python/vision/gesture_recognizer",
    "_internal/mediapipe/tasks/python/vision/hand_landmarker",
    "_internal/mediapipe/tasks/python/vision/holistic_landmarker",
    "_internal/mediapipe/tasks/python/vision/image_classifier",
    "_internal/mediapipe/tasks/python/vision/image_embedder",
    "_internal/mediapipe/tasks/python/vision/image_segmenter",
    "_internal/mediapipe/tasks/python/vision/interactive_segmenter",
    "_internal/mediapipe/tasks/python/vision/object_detector",
    "_internal/mediapipe/tasks/python/vision/pose_landmarker",
)


@dataclass(frozen=True)
class SizeEntry:
    path: str
    size: int


@dataclass(frozen=True)
class BundleSizeReport:
    passed: bool
    file_count: int
    unpacked_bytes: int
    archive_bytes: int | None
    max_unpacked_bytes: int
    max_archive_bytes: int
    unpacked_reduction_bytes: int
    unpacked_reduction_percent: float
    archive_reduction_bytes: int | None
    archive_reduction_percent: float | None
    forbidden_paths: tuple[str, ...]
    failures: tuple[str, ...]
    largest_files: tuple[SizeEntry, ...]
    largest_groups: tuple[SizeEntry, ...]

    def to_mapping(self) -> dict[str, object]:
        payload = asdict(self)
        payload["largest_files"] = [asdict(entry) for entry in self.largest_files]
        payload["largest_groups"] = [asdict(entry) for entry in self.largest_groups]
        return payload

    def write_json(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.to_mapping(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def write_markdown(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        archive_text = (
            f"{self.archive_bytes:,} bytes"
            if self.archive_bytes is not None
            else "not supplied"
        )
        lines = [
            "# Glassless3D standalone bundle-size report",
            "",
            f"- Result: **{'PASS' if self.passed else 'FAIL'}**",
            f"- Files: `{self.file_count}`",
            f"- Unpacked: `{self.unpacked_bytes:,}` bytes",
            f"- Archive: `{archive_text}`",
            (
                "- Unpacked reduction from baseline: "
                f"`{self.unpacked_reduction_bytes:,}` bytes "
                f"(`{self.unpacked_reduction_percent:.1f}%`)"
            ),
        ]
        if self.archive_reduction_bytes is not None:
            lines.append(
                "- Archive reduction from baseline: "
                f"`{self.archive_reduction_bytes:,}` bytes "
                f"(`{self.archive_reduction_percent:.1f}%`)"
            )
        lines.extend(
            (
                "",
                "## Largest groups",
                "",
                "| Group | Bytes |",
                "|---|---:|",
            )
        )
        lines.extend(
            f"| `{entry.path}` | {entry.size:,} |"
            for entry in self.largest_groups
        )
        lines.extend(
            (
                "",
                "## Largest files",
                "",
                "| File | Bytes |",
                "|---|---:|",
            )
        )
        lines.extend(
            f"| `{entry.path}` | {entry.size:,} |"
            for entry in self.largest_files
        )
        if self.failures:
            lines.extend(("", "## Failures", ""))
            lines.extend(f"- {failure}" for failure in self.failures)
        destination.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _load_files(manifest_path: Path) -> list[SizeEntry]:
    parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict) or not isinstance(parsed.get("files"), list):
        raise ValueError("release manifest must contain a files array")
    entries: list[SizeEntry] = []
    for raw in parsed["files"]:
        if not isinstance(raw, dict):
            raise ValueError("release manifest contains a non-object file entry")
        path = raw.get("path")
        size = raw.get("size")
        if not isinstance(path, str) or not isinstance(size, int) or size < 0:
            raise ValueError(f"invalid release manifest file entry: {raw!r}")
        entries.append(SizeEntry(path=path.replace("\\", "/"), size=size))
    return entries


def _group_name(path: str) -> str:
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "_internal":
        return "/".join(parts[:2])
    return parts[0]


def analyze_bundle(
    *,
    manifest_path: str | Path,
    archive_path: str | Path | None = None,
    max_unpacked_bytes: int = DEFAULT_MAX_UNPACKED_BYTES,
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    forbidden_prefixes: Iterable[str] = FORBIDDEN_PATH_PREFIXES,
) -> BundleSizeReport:
    manifest = Path(manifest_path)
    files = _load_files(manifest)
    unpacked_bytes = sum(entry.size for entry in files)
    archive = Path(archive_path) if archive_path is not None else None
    archive_bytes = archive.stat().st_size if archive is not None else None
    forbidden = tuple(
        sorted(
            entry.path
            for entry in files
            if any(entry.path.startswith(prefix) for prefix in forbidden_prefixes)
        )
    )
    failures: list[str] = []
    if unpacked_bytes > max_unpacked_bytes:
        failures.append(
            f"unpacked bundle exceeds {max_unpacked_bytes:,} bytes: "
            f"{unpacked_bytes:,}"
        )
    if archive_bytes is not None and archive_bytes > max_archive_bytes:
        failures.append(
            f"archive exceeds {max_archive_bytes:,} bytes: {archive_bytes:,}"
        )
    if forbidden:
        failures.append(
            f"bundle contains {len(forbidden)} forbidden unrelated runtime files"
        )

    groups: dict[str, int] = defaultdict(int)
    for entry in files:
        groups[_group_name(entry.path)] += entry.size
    largest_files = tuple(sorted(files, key=lambda entry: entry.size, reverse=True)[:25])
    largest_groups = tuple(
        SizeEntry(path=name, size=size)
        for name, size in sorted(groups.items(), key=lambda item: item[1], reverse=True)[:20]
    )
    unpacked_reduction = BASELINE_UNPACKED_BYTES - unpacked_bytes
    archive_reduction = (
        BASELINE_ARCHIVE_BYTES - archive_bytes
        if archive_bytes is not None
        else None
    )
    return BundleSizeReport(
        passed=not failures,
        file_count=len(files),
        unpacked_bytes=unpacked_bytes,
        archive_bytes=archive_bytes,
        max_unpacked_bytes=max_unpacked_bytes,
        max_archive_bytes=max_archive_bytes,
        unpacked_reduction_bytes=unpacked_reduction,
        unpacked_reduction_percent=(
            unpacked_reduction / BASELINE_UNPACKED_BYTES * 100.0
        ),
        archive_reduction_bytes=archive_reduction,
        archive_reduction_percent=(
            archive_reduction / BASELINE_ARCHIVE_BYTES * 100.0
            if archive_reduction is not None
            else None
        ),
        forbidden_paths=forbidden,
        failures=tuple(failures),
        largest_files=largest_files,
        largest_groups=largest_groups,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze and enforce a Glassless3D standalone bundle budget"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument(
        "--max-unpacked-bytes",
        type=int,
        default=DEFAULT_MAX_UNPACKED_BYTES,
    )
    parser.add_argument(
        "--max-archive-bytes",
        type=int,
        default=DEFAULT_MAX_ARCHIVE_BYTES,
    )
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-markdown", type=Path)
    parser.add_argument("--fail-on-regression", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = analyze_bundle(
            manifest_path=args.manifest,
            archive_path=args.archive,
            max_unpacked_bytes=args.max_unpacked_bytes,
            max_archive_bytes=args.max_archive_bytes,
        )
        encoded = json.dumps(report.to_mapping(), indent=2, sort_keys=True)
        print(encoded)
        if args.output_json is not None:
            report.write_json(args.output_json)
        if args.output_markdown is not None:
            report.write_markdown(args.output_markdown)
        return 1 if args.fail_on_regression and not report.passed else 0
    except Exception as error:
        print(f"Bundle-size analysis failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
