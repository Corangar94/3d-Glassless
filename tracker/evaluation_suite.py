"""Combined evaluation runner for depth and frame-pacing benchmarks."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from tracker.comfort_evaluation import (
    ComfortBenchmarkResult,
    run_benchmark as run_comfort_benchmark,
)
from tracker.depth_benchmark import DepthBenchmarkResult, run_benchmark as run_depth_benchmark
from tracker.depth_fixtures import DEFAULT_FIXTURE_ROOT, benchmark_fixture
from tracker.display_quality import (
    DisplayQualityBenchmarkResult,
    run_benchmark as run_display_quality_benchmark,
)
from tracker.latency_evaluation import LatencyBenchmarkResult, run_benchmark as run_latency_benchmark
from tracker.performance_benchmark import (
    FramePacingBenchmarkResult,
    run_benchmark as run_performance_benchmark,
)

_QUALITY_RANK = {"GOOD": 0, "WARN": 1, "DANGER": 2}


@dataclass(frozen=True)
class EvaluationSuiteResult:
    depth: DepthBenchmarkResult | None
    performance: FramePacingBenchmarkResult | None
    comfort: ComfortBenchmarkResult | None
    display_quality: DisplayQualityBenchmarkResult | None
    latency: LatencyBenchmarkResult | None
    overall_quality: str


def run_suite(
    depth_dir: str | Path | None = None,
    depth_fixture: str | None = None,
    depth_fixture_root: str | Path = DEFAULT_FIXTURE_ROOT,
    timing_csv: str | Path | None = None,
    comfort_csv: str | Path | None = None,
    display_quality_csv: str | Path | None = None,
    latency_csv: str | Path | None = None,
    target_fps: float = 60.0,
    latency_target_ms: float = 20.0,
) -> EvaluationSuiteResult:
    if depth_dir is not None:
        depth = run_depth_benchmark(depth_dir)
    elif depth_fixture is not None:
        depth = benchmark_fixture(depth_fixture, depth_fixture_root).result
    else:
        depth = None
    performance = (
        run_performance_benchmark(timing_csv, target_fps=target_fps)
        if timing_csv is not None
        else None
    )
    comfort = run_comfort_benchmark(comfort_csv) if comfort_csv is not None else None
    display_quality = (
        run_display_quality_benchmark(display_quality_csv)
        if display_quality_csv is not None
        else None
    )
    latency = (
        run_latency_benchmark(latency_csv, target_ms=latency_target_ms)
        if latency_csv is not None
        else None
    )
    qualities = [
        result.quality
        for result in (depth, performance, comfort, display_quality, latency)
        if result is not None
    ]
    overall = max(qualities, key=lambda q: _QUALITY_RANK[q]) if qualities else "WARN"
    return EvaluationSuiteResult(
        depth=depth,
        performance=performance,
        comfort=comfort,
        display_quality=display_quality,
        latency=latency,
        overall_quality=overall,
    )


def format_suite_result(result: EvaluationSuiteResult) -> str:
    lines = [
        "Glassless3D Evaluation Suite",
        f"overall_quality={result.overall_quality}",
    ]
    if result.depth is not None:
        lines.extend(
            [
                "",
                "Depth:",
                f"- quality={result.depth.quality}",
                f"- mean_abs_delta={result.depth.metrics.mean_abs_delta:.4f}",
                f"- p95_abs_delta={result.depth.metrics.p95_abs_delta:.4f}",
            ]
        )
    if result.performance is not None:
        lines.extend(
            [
                "",
                "Performance:",
                f"- quality={result.performance.quality}",
                f"- avg_fps={result.performance.metrics.avg_fps:.2f}",
                f"- p95_frame_time_ms={result.performance.metrics.p95_frame_time_ms:.2f}",
            ]
        )
    if result.comfort is not None:
        lines.extend(
            [
                "",
                "Comfort:",
                f"- quality={result.comfort.quality}",
                f"- avg_discomfort={result.comfort.metrics.avg_discomfort:.2f}",
                f"- avg_ui_readability={result.comfort.metrics.avg_ui_readability:.2f}",
                f"- avg_crosstalk={result.comfort.metrics.avg_crosstalk:.2f}",
            ]
        )
    if result.display_quality is not None:
        lines.extend(
            [
                "",
                "Display Quality:",
                f"- quality={result.display_quality.quality}",
                f"- usable_width_cm={result.display_quality.metrics.usable_width_cm:.2f}",
                f"- avg_crosstalk_percent={result.display_quality.metrics.avg_crosstalk_percent:.2f}",
            ]
        )
    if result.latency is not None:
        lines.extend(
            [
                "",
                "Latency:",
                f"- quality={result.latency.quality}",
                f"- p95_latency_ms={result.latency.metrics.p95_latency_ms:.2f}",
                f"- over_target_rate={result.latency.metrics.over_target_rate:.4f}",
            ]
        )
    return "\n".join(lines)


def format_suite_json(result: EvaluationSuiteResult) -> str:
    data = {
        "overall_quality": result.overall_quality,
        "depth": _depth_to_dict(result.depth),
        "performance": _performance_to_dict(result.performance),
        "comfort": _comfort_to_dict(result.comfort),
        "display_quality": _display_quality_to_dict(result.display_quality),
        "latency": _latency_to_dict(result.latency),
    }
    return json.dumps(data, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Glassless3D benchmark suite")
    parser.add_argument("--depth-dir", help="Directory containing .npy depth frames")
    parser.add_argument("--depth-fixture", help="Registered depth fixture name")
    parser.add_argument("--depth-fixture-root", default=str(DEFAULT_FIXTURE_ROOT))
    parser.add_argument("--timing-csv", help="CSV with timestamp_ms,frame_time_ms columns")
    parser.add_argument("--comfort-csv", help="CSV with 1-5 comfort/display survey scores")
    parser.add_argument("--display-quality-csv", help="CSV with measured viewing-zone/crosstalk samples")
    parser.add_argument("--latency-csv", help="CSV with timestamp_ms,tracking_to_display_ms columns")
    parser.add_argument("--target-fps", type=float, default=60.0)
    parser.add_argument("--latency-target-ms", type=float, default=20.0)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--output", help="Optional path to write the suite report")
    args = parser.parse_args(argv)

    result = run_suite(
        depth_dir=args.depth_dir,
        depth_fixture=args.depth_fixture,
        depth_fixture_root=args.depth_fixture_root,
        timing_csv=args.timing_csv,
        comfort_csv=args.comfort_csv,
        display_quality_csv=args.display_quality_csv,
        latency_csv=args.latency_csv,
        target_fps=args.target_fps,
        latency_target_ms=args.latency_target_ms,
    )
    text = format_suite_json(result) if args.format == "json" else format_suite_result(result)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote evaluation report to {output}")
    else:
        print(text)
    return 1 if result.overall_quality == "DANGER" else 0


def _depth_to_dict(result: DepthBenchmarkResult | None) -> dict[str, object] | None:
    if result is None:
        return None
    m = result.metrics
    return {
        "source_dir": str(result.source_dir),
        "quality": result.quality,
        "frame_count": m.frame_count,
        "mean_abs_delta": m.mean_abs_delta,
        "p95_abs_delta": m.p95_abs_delta,
        "max_abs_delta": m.max_abs_delta,
    }


def _performance_to_dict(
    result: FramePacingBenchmarkResult | None,
) -> dict[str, object] | None:
    if result is None:
        return None
    m = result.metrics
    return {
        "source_path": str(result.source_path),
        "target_fps": result.target_fps,
        "quality": result.quality,
        "sample_count": m.sample_count,
        "avg_fps": m.avg_fps,
        "avg_frame_time_ms": m.avg_frame_time_ms,
        "p95_frame_time_ms": m.p95_frame_time_ms,
        "max_frame_time_ms": m.max_frame_time_ms,
        "over_budget_rate": m.over_budget_rate,
    }


def _comfort_to_dict(result: ComfortBenchmarkResult | None) -> dict[str, object] | None:
    if result is None:
        return None
    m = result.metrics
    return {
        "source_path": str(result.source_path),
        "quality": result.quality,
        "sample_count": m.sample_count,
        "avg_discomfort": m.avg_discomfort,
        "max_discomfort": m.max_discomfort,
        "avg_depth_realism": m.avg_depth_realism,
        "avg_ui_readability": m.avg_ui_readability,
        "avg_crosstalk": m.avg_crosstalk,
    }


def _display_quality_to_dict(
    result: DisplayQualityBenchmarkResult | None,
) -> dict[str, object] | None:
    if result is None:
        return None
    m = result.metrics
    return {
        "source_path": str(result.source_path),
        "quality": result.quality,
        "sample_count": m.sample_count,
        "usable_sample_count": m.usable_sample_count,
        "usable_width_cm": m.usable_width_cm,
        "avg_crosstalk_percent": m.avg_crosstalk_percent,
        "max_crosstalk_percent": m.max_crosstalk_percent,
    }


def _latency_to_dict(result: LatencyBenchmarkResult | None) -> dict[str, object] | None:
    if result is None:
        return None
    m = result.metrics
    return {
        "source_path": str(result.source_path),
        "quality": result.quality,
        "sample_count": m.sample_count,
        "target_ms": m.target_ms,
        "avg_latency_ms": m.avg_latency_ms,
        "p95_latency_ms": m.p95_latency_ms,
        "max_latency_ms": m.max_latency_ms,
        "over_target_rate": m.over_target_rate,
    }


if __name__ == "__main__":
    raise SystemExit(main())
