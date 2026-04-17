"""Offline frame-pacing benchmark for captured overlay timing samples."""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from tracker.performance_evaluation import (
    FrameTimingMetrics,
    FrameTimingSample,
    classify_frame_pacing,
    compute_frame_timing_metrics,
)


@dataclass(frozen=True)
class FramePacingBenchmarkResult:
    source_path: Path
    target_fps: float
    metrics: FrameTimingMetrics
    quality: str


def load_frame_timings(source_path: str | Path) -> list[FrameTimingSample]:
    path = Path(source_path)
    samples: list[FrameTimingSample] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            samples.append(
                FrameTimingSample(
                    timestamp_ms=int(float(row["timestamp_ms"])),
                    frame_time_ms=float(row["frame_time_ms"]),
                )
            )
    return sorted(samples, key=lambda sample: sample.timestamp_ms)


def run_benchmark(
    source_path: str | Path,
    target_fps: float = 60.0,
) -> FramePacingBenchmarkResult:
    path = Path(source_path)
    samples = load_frame_timings(path)
    if not samples:
        raise ValueError(f"no frame timing samples found in {path}")

    metrics = compute_frame_timing_metrics(samples, target_fps=target_fps)
    return FramePacingBenchmarkResult(
        source_path=path,
        target_fps=target_fps,
        metrics=metrics,
        quality=classify_frame_pacing(metrics),
    )


def format_benchmark_result(result: FramePacingBenchmarkResult) -> str:
    m = result.metrics
    return (
        f"Frame pacing benchmark: {result.source_path}\n"
        f"target_fps={result.target_fps:.1f}\n"
        f"quality={result.quality}\n"
        f"samples={m.sample_count}\n"
        f"avg_fps={m.avg_fps:.2f}\n"
        f"avg_frame_time_ms={m.avg_frame_time_ms:.2f}\n"
        f"p95_frame_time_ms={m.p95_frame_time_ms:.2f}\n"
        f"max_frame_time_ms={m.max_frame_time_ms:.2f}\n"
        f"over_budget_rate={m.over_budget_rate * 100.0:.1f}%"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark overlay frame pacing")
    parser.add_argument("source_path", help="CSV with timestamp_ms,frame_time_ms columns")
    parser.add_argument("--target-fps", type=float, default=60.0)
    args = parser.parse_args(argv)

    result = run_benchmark(args.source_path, target_fps=args.target_fps)
    print(format_benchmark_result(result))
    return 1 if result.quality == "DANGER" else 0


if __name__ == "__main__":
    raise SystemExit(main())
