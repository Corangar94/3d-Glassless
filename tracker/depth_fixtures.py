"""Manifest-backed depth benchmark fixtures."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from tracker.depth_benchmark import DepthBenchmarkResult, format_benchmark_result, run_benchmark

DEFAULT_FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "depth"


@dataclass(frozen=True)
class DepthFixture:
    name: str
    path: Path
    kind: str
    source: str
    description: str
    frame_count: int
    width: int
    height: int
    expected_quality: str | None = None


@dataclass(frozen=True)
class DepthFixtureBenchmark:
    fixture: DepthFixture
    result: DepthBenchmarkResult


def load_fixture_manifest(root: str | Path = DEFAULT_FIXTURE_ROOT) -> list[DepthFixture]:
    """Load `manifest.json` and resolve fixture directories relative to root."""
    base = Path(root)
    manifest_path = base / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    fixtures = []
    for item in data.get("fixtures", []):
        fixtures.append(
            DepthFixture(
                name=str(item["name"]),
                path=base / str(item["directory"]),
                kind=str(item.get("kind", "captured")),
                source=str(item.get("source", "")),
                description=str(item.get("description", "")),
                frame_count=int(item.get("frame_count", 0)),
                width=int(item.get("width", 0)),
                height=int(item.get("height", 0)),
                expected_quality=item.get("expected_quality"),
            )
        )
    return fixtures


def find_fixture(name: str, root: str | Path = DEFAULT_FIXTURE_ROOT) -> DepthFixture:
    for fixture in load_fixture_manifest(root):
        if fixture.name == name:
            return fixture
    raise ValueError(f"unknown depth fixture: {name}")


def benchmark_fixture(name: str, root: str | Path = DEFAULT_FIXTURE_ROOT) -> DepthFixtureBenchmark:
    fixture = find_fixture(name, root)
    return DepthFixtureBenchmark(fixture=fixture, result=run_benchmark(fixture.path))


def format_fixture_list(fixtures: Sequence[DepthFixture]) -> str:
    lines = ["Depth benchmark fixtures:"]
    for fixture in fixtures:
        expected = f", expected={fixture.expected_quality}" if fixture.expected_quality else ""
        lines.append(
            f"- {fixture.name} [{fixture.kind}] frames={fixture.frame_count} "
            f"size={fixture.width}x{fixture.height}{expected}: {fixture.description}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List or benchmark depth validation fixtures")
    parser.add_argument("--root", default=str(DEFAULT_FIXTURE_ROOT), help="Depth fixture root")
    parser.add_argument("--list", action="store_true", help="List registered fixtures")
    parser.add_argument("--benchmark", help="Benchmark one fixture by name")
    parser.add_argument("--benchmark-all", action="store_true", help="Benchmark every fixture")
    args = parser.parse_args(argv)

    fixtures = load_fixture_manifest(args.root)
    if args.list:
        print(format_fixture_list(fixtures))
        return 0

    if args.benchmark:
        item = benchmark_fixture(args.benchmark, args.root)
        print(f"Fixture: {item.fixture.name}")
        print(format_benchmark_result(item.result))
        return 1 if item.result.quality == "DANGER" else 0

    if args.benchmark_all:
        exit_code = 0
        for fixture in fixtures:
            item = benchmark_fixture(fixture.name, args.root)
            print(f"Fixture: {item.fixture.name}")
            print(format_benchmark_result(item.result))
            if item.result.quality == "DANGER":
                exit_code = 1
        return exit_code

    print(format_fixture_list(fixtures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
