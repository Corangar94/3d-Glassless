"""Compare captured depth stability against a baseline sequence."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from tracker.depth_benchmark import DepthBenchmarkResult, run_benchmark


@dataclass(frozen=True)
class DepthComparisonResult:
    captured: DepthBenchmarkResult
    baseline: DepthBenchmarkResult
    mean_delta_ratio: float
    max_ratio: float
    regressed: bool


def compare_depth_stability(
    captured_dir: str | Path,
    baseline_dir: str | Path,
    max_ratio: float = 2.0,
) -> DepthComparisonResult:
    if max_ratio <= 0:
        raise ValueError("max_ratio must be positive")
    captured = run_benchmark(captured_dir)
    baseline = run_benchmark(baseline_dir)
    baseline_mean = baseline.metrics.mean_abs_delta
    if baseline_mean <= 0:
        ratio = float("inf") if captured.metrics.mean_abs_delta > 0 else 1.0
    else:
        ratio = captured.metrics.mean_abs_delta / baseline_mean
    return DepthComparisonResult(
        captured=captured,
        baseline=baseline,
        mean_delta_ratio=round(ratio, 6),
        max_ratio=max_ratio,
        regressed=ratio > max_ratio,
    )


def format_comparison_result(result: DepthComparisonResult) -> str:
    return (
        "Depth stability comparison\n"
        f"captured={result.captured.source_dir}\n"
        f"baseline={result.baseline.source_dir}\n"
        f"captured_quality={result.captured.quality}\n"
        f"baseline_quality={result.baseline.quality}\n"
        f"mean_delta_ratio={result.mean_delta_ratio:.2f}\n"
        f"max_ratio={result.max_ratio:.2f}\n"
        f"regressed={result.regressed}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare captured depth stability against a baseline")
    parser.add_argument("captured_dir", help="Directory containing captured .npy depth frames")
    parser.add_argument("baseline_dir", help="Directory containing baseline .npy depth frames")
    parser.add_argument("--max-ratio", type=float, default=2.0)
    args = parser.parse_args(argv)

    result = compare_depth_stability(
        args.captured_dir,
        args.baseline_dir,
        max_ratio=args.max_ratio,
    )
    print(format_comparison_result(result))
    return 1 if result.regressed or result.captured.quality == "DANGER" else 0


if __name__ == "__main__":
    raise SystemExit(main())
