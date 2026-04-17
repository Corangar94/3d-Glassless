"""Objective display-zone and crosstalk measurement scoring."""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class DisplayMeasurementSample:
    """One measured point in the display viewing workspace."""

    x_cm: float
    z_cm: float
    crosstalk_percent: float
    view_locked: bool


@dataclass(frozen=True)
class DisplayQualityMetrics:
    sample_count: int
    usable_sample_count: int
    usable_width_cm: float
    avg_crosstalk_percent: float
    max_crosstalk_percent: float


@dataclass(frozen=True)
class DisplayQualityBenchmarkResult:
    source_path: Path
    quality: str
    metrics: DisplayQualityMetrics


_FIELD_NAMES = ("x_cm", "z_cm", "crosstalk_percent", "view_locked")


def compute_display_quality_metrics(samples: Sequence[DisplayMeasurementSample]) -> DisplayQualityMetrics:
    if not samples:
        return DisplayQualityMetrics(
            sample_count=0,
            usable_sample_count=0,
            usable_width_cm=0.0,
            avg_crosstalk_percent=100.0,
            max_crosstalk_percent=100.0,
        )

    usable = [s for s in samples if s.view_locked]
    usable_width = 0.0
    if len(usable) >= 2:
        xs = [s.x_cm for s in usable]
        usable_width = max(xs) - min(xs)

    crosstalk_values = [s.crosstalk_percent for s in samples]
    usable_crosstalk_values = [s.crosstalk_percent for s in usable] or crosstalk_values
    return DisplayQualityMetrics(
        sample_count=len(samples),
        usable_sample_count=len(usable),
        usable_width_cm=usable_width,
        avg_crosstalk_percent=sum(usable_crosstalk_values) / len(usable_crosstalk_values),
        max_crosstalk_percent=max(crosstalk_values),
    )


def classify_display_quality(metrics: DisplayQualityMetrics) -> str:
    if (
        metrics.sample_count == 0
        or metrics.usable_sample_count == 0
        or metrics.usable_width_cm < 5.0
        or metrics.avg_crosstalk_percent >= 20.0
        or metrics.max_crosstalk_percent >= 35.0
    ):
        return "DANGER"
    if (
        metrics.usable_width_cm < 15.0
        or metrics.avg_crosstalk_percent >= 10.0
        or metrics.max_crosstalk_percent >= 20.0
    ):
        return "WARN"
    return "GOOD"


def load_display_quality_csv(path: str | Path) -> list[DisplayMeasurementSample]:
    source = Path(path)
    with source.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [field for field in _FIELD_NAMES if field not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"display quality CSV missing fields: {', '.join(missing)}")
        return [_sample_from_row(row) for row in reader]


def run_benchmark(path: str | Path) -> DisplayQualityBenchmarkResult:
    source = Path(path)
    metrics = compute_display_quality_metrics(load_display_quality_csv(source))
    return DisplayQualityBenchmarkResult(
        source_path=source,
        quality=classify_display_quality(metrics),
        metrics=metrics,
    )


def format_benchmark_result(result: DisplayQualityBenchmarkResult) -> str:
    m = result.metrics
    return "\n".join(
        [
            "Glassless3D Display Quality Evaluation",
            f"quality={result.quality}",
            f"sample_count={m.sample_count}",
            f"usable_sample_count={m.usable_sample_count}",
            f"usable_width_cm={m.usable_width_cm:.2f}",
            f"avg_crosstalk_percent={m.avg_crosstalk_percent:.2f}",
            f"max_crosstalk_percent={m.max_crosstalk_percent:.2f}",
        ]
    )


def format_benchmark_json(result: DisplayQualityBenchmarkResult) -> str:
    m = result.metrics
    data = {
        "source_path": str(result.source_path),
        "quality": result.quality,
        "metrics": {
            "sample_count": m.sample_count,
            "usable_sample_count": m.usable_sample_count,
            "usable_width_cm": m.usable_width_cm,
            "avg_crosstalk_percent": m.avg_crosstalk_percent,
            "max_crosstalk_percent": m.max_crosstalk_percent,
        },
    }
    return json.dumps(data, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate measured Glassless3D display-zone quality")
    parser.add_argument("display_quality_csv")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    result = run_benchmark(args.display_quality_csv)
    text = format_benchmark_json(result) if args.format == "json" else format_benchmark_result(result)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote display quality evaluation to {output}")
    else:
        print(text)
    return 1 if result.quality == "DANGER" else 0


def _sample_from_row(row: dict[str, str]) -> DisplayMeasurementSample:
    return DisplayMeasurementSample(
        x_cm=float(row["x_cm"]),
        z_cm=float(row["z_cm"]),
        crosstalk_percent=float(row["crosstalk_percent"]),
        view_locked=_bool(row["view_locked"]),
    )


def _bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"view_locked must be boolean, got {value!r}")


if __name__ == "__main__":
    raise SystemExit(main())
