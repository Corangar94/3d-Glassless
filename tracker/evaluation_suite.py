"""Combined evaluation runner for depth and frame-pacing benchmarks."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from tracker.depth_benchmark import DepthBenchmarkResult, run_benchmark as run_depth_benchmark
from tracker.performance_benchmark import (
    FramePacingBenchmarkResult,
    run_benchmark as run_performance_benchmark,
)

_QUALITY_RANK = {"GOOD": 0, "WARN": 1, "DANGER": 2}


@dataclass(frozen=True)
class EvaluationSuiteResult:
    depth: DepthBenchmarkResult | None
    performance: FramePacingBenchmarkResult | None
    overall_quality: str


def run_suite(
    depth_dir: str | Path | None = None,
    timing_csv: str | Path | None = None,
    target_fps: float = 60.0,
) -> EvaluationSuiteResult:
    depth = run_depth_benchmark(depth_dir) if depth_dir is not None else None
    performance = (
        run_performance_benchmark(timing_csv, target_fps=target_fps)
        if timing_csv is not None
        else None
    )
    qualities = [
        result.quality
        for result in (depth, performance)
        if result is not None
    ]
    overall = max(qualities, key=lambda q: _QUALITY_RANK[q]) if qualities else "WARN"
    return EvaluationSuiteResult(
        depth=depth,
        performance=performance,
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
    return "\n".join(lines)


def format_suite_json(result: EvaluationSuiteResult) -> str:
    data = {
        "overall_quality": result.overall_quality,
        "depth": _depth_to_dict(result.depth),
        "performance": _performance_to_dict(result.performance),
    }
    return json.dumps(data, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Glassless3D benchmark suite")
    parser.add_argument("--depth-dir", help="Directory containing .npy depth frames")
    parser.add_argument("--timing-csv", help="CSV with timestamp_ms,frame_time_ms columns")
    parser.add_argument("--target-fps", type=float, default=60.0)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--output", help="Optional path to write the suite report")
    args = parser.parse_args(argv)

    result = run_suite(
        depth_dir=args.depth_dir,
        timing_csv=args.timing_csv,
        target_fps=args.target_fps,
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


if __name__ == "__main__":
    raise SystemExit(main())
