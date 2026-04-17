"""Tracking-to-display latency benchmark utilities."""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class LatencySample:
    timestamp_ms: int
    tracking_to_display_ms: float


@dataclass(frozen=True)
class LatencyMetrics:
    sample_count: int
    target_ms: float
    avg_latency_ms: float
    p95_latency_ms: float
    max_latency_ms: float
    over_target_rate: float


@dataclass(frozen=True)
class LatencyBenchmarkResult:
    source_path: Path
    quality: str
    metrics: LatencyMetrics


def compute_latency_metrics(samples: Sequence[LatencySample], target_ms: float = 20.0) -> LatencyMetrics:
    if target_ms <= 0:
        raise ValueError("target_ms must be positive")
    if not samples:
        return LatencyMetrics(
            sample_count=0,
            target_ms=target_ms,
            avg_latency_ms=0.0,
            p95_latency_ms=0.0,
            max_latency_ms=0.0,
            over_target_rate=1.0,
        )

    values = sorted(sample.tracking_to_display_ms for sample in samples)
    over_target = [value for value in values if value > target_ms]
    return LatencyMetrics(
        sample_count=len(values),
        target_ms=target_ms,
        avg_latency_ms=sum(values) / len(values),
        p95_latency_ms=_nearest_rank_percentile(values, 0.95),
        max_latency_ms=max(values),
        over_target_rate=len(over_target) / len(values),
    )


def classify_latency(metrics: LatencyMetrics) -> str:
    if metrics.sample_count == 0:
        return "DANGER"
    if (
        metrics.avg_latency_ms >= metrics.target_ms * 1.5
        or metrics.p95_latency_ms >= metrics.target_ms * 2.0
        or metrics.over_target_rate >= 0.5
    ):
        return "DANGER"
    if (
        metrics.avg_latency_ms >= metrics.target_ms
        or metrics.p95_latency_ms >= metrics.target_ms * 1.25
        or metrics.over_target_rate >= 0.05
    ):
        return "WARN"
    return "GOOD"


def load_latency_csv(path: str | Path) -> list[LatencySample]:
    source = Path(path)
    with source.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or []
        for field in ("timestamp_ms", "tracking_to_display_ms"):
            if field not in fields:
                raise ValueError(f"latency CSV missing field: {field}")
        return [
            LatencySample(
                timestamp_ms=int(row["timestamp_ms"]),
                tracking_to_display_ms=float(row["tracking_to_display_ms"]),
            )
            for row in reader
        ]


def run_benchmark(path: str | Path, target_ms: float = 20.0) -> LatencyBenchmarkResult:
    source = Path(path)
    metrics = compute_latency_metrics(load_latency_csv(source), target_ms=target_ms)
    return LatencyBenchmarkResult(
        source_path=source,
        quality=classify_latency(metrics),
        metrics=metrics,
    )


def format_benchmark_result(result: LatencyBenchmarkResult) -> str:
    m = result.metrics
    return "\n".join(
        [
            "Glassless3D Latency Evaluation",
            f"quality={result.quality}",
            f"sample_count={m.sample_count}",
            f"target_ms={m.target_ms:.2f}",
            f"avg_latency_ms={m.avg_latency_ms:.2f}",
            f"p95_latency_ms={m.p95_latency_ms:.2f}",
            f"max_latency_ms={m.max_latency_ms:.2f}",
            f"over_target_rate={m.over_target_rate:.4f}",
        ]
    )


def format_benchmark_json(result: LatencyBenchmarkResult) -> str:
    m = result.metrics
    data = {
        "source_path": str(result.source_path),
        "quality": result.quality,
        "metrics": {
            "sample_count": m.sample_count,
            "target_ms": m.target_ms,
            "avg_latency_ms": m.avg_latency_ms,
            "p95_latency_ms": m.p95_latency_ms,
            "max_latency_ms": m.max_latency_ms,
            "over_target_rate": m.over_target_rate,
        },
    }
    return json.dumps(data, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Glassless3D tracking-to-display latency CSV")
    parser.add_argument("latency_csv")
    parser.add_argument("--target-ms", type=float, default=20.0)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    result = run_benchmark(args.latency_csv, target_ms=args.target_ms)
    text = format_benchmark_json(result) if args.format == "json" else format_benchmark_result(result)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote latency evaluation to {output}")
    else:
        print(text)
    return 1 if result.quality == "DANGER" else 0


def _nearest_rank_percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    index = max(0, min(len(values) - 1, int((len(values) * percentile) + 0.999999) - 1))
    return values[index]


if __name__ == "__main__":
    raise SystemExit(main())
