"""Offline depth-stability benchmark for captured depth-frame sequences."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from tracker.depth_evaluation import (
    DepthStabilityMetrics,
    classify_depth_stability,
    compute_depth_stability,
)


@dataclass(frozen=True)
class DepthBenchmarkResult:
    source_dir: Path
    metrics: DepthStabilityMetrics
    quality: str


def load_depth_frames(source_dir: str | Path) -> list[np.ndarray]:
    """Load `.npy` depth frames from `source_dir` in filename order."""
    path = Path(source_dir)
    files = sorted(path.glob("*.npy"))
    return [np.load(file).astype(np.float32, copy=False) for file in files]


def run_benchmark(source_dir: str | Path) -> DepthBenchmarkResult:
    path = Path(source_dir)
    frames = load_depth_frames(path)
    if not frames:
        raise ValueError(f"no .npy depth frames found in {path}")

    metrics = compute_depth_stability(frames)
    return DepthBenchmarkResult(
        source_dir=path,
        metrics=metrics,
        quality=classify_depth_stability(metrics),
    )


def format_benchmark_result(result: DepthBenchmarkResult) -> str:
    m = result.metrics
    return (
        f"Depth stability benchmark: {result.source_dir}\n"
        f"quality={result.quality}\n"
        f"frames={m.frame_count}\n"
        f"mean_abs_delta={m.mean_abs_delta:.4f}\n"
        f"p95_abs_delta={m.p95_abs_delta:.4f}\n"
        f"max_abs_delta={m.max_abs_delta:.4f}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark depth temporal stability")
    parser.add_argument("source_dir", help="Directory containing .npy depth frames")
    args = parser.parse_args(argv)

    result = run_benchmark(args.source_dir)
    print(format_benchmark_result(result))
    return 1 if result.quality == "DANGER" else 0


if __name__ == "__main__":
    raise SystemExit(main())
