"""Subjective comfort and display-quality scoring for evaluation runs."""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class ComfortSurveySample:
    """One operator/user survey response.

    Scores use a 1-5 scale. Discomfort and crosstalk are severity scores where
    1 is best; depth realism and UI readability are quality scores where 5 is
    best.
    """

    eye_strain: int
    headache: int
    nausea: int
    disorientation: int
    depth_realism: int
    ui_readability: int
    crosstalk: int


@dataclass(frozen=True)
class ComfortMetrics:
    sample_count: int
    avg_discomfort: float
    max_discomfort: int
    avg_depth_realism: float
    avg_ui_readability: float
    avg_crosstalk: float


@dataclass(frozen=True)
class ComfortBenchmarkResult:
    source_path: Path
    quality: str
    metrics: ComfortMetrics


_FIELD_NAMES = (
    "eye_strain",
    "headache",
    "nausea",
    "disorientation",
    "depth_realism",
    "ui_readability",
    "crosstalk",
)


def compute_comfort_metrics(samples: Sequence[ComfortSurveySample]) -> ComfortMetrics:
    if not samples:
        return ComfortMetrics(
            sample_count=0,
            avg_discomfort=5.0,
            max_discomfort=5,
            avg_depth_realism=0.0,
            avg_ui_readability=0.0,
            avg_crosstalk=5.0,
        )

    discomfort_values = [
        value
        for sample in samples
        for value in (
            sample.eye_strain,
            sample.headache,
            sample.nausea,
            sample.disorientation,
        )
    ]
    return ComfortMetrics(
        sample_count=len(samples),
        avg_discomfort=sum(discomfort_values) / len(discomfort_values),
        max_discomfort=max(discomfort_values),
        avg_depth_realism=sum(s.depth_realism for s in samples) / len(samples),
        avg_ui_readability=sum(s.ui_readability for s in samples) / len(samples),
        avg_crosstalk=sum(s.crosstalk for s in samples) / len(samples),
    )


def classify_comfort_quality(metrics: ComfortMetrics) -> str:
    """Classify subjective run quality as GOOD, WARN, or DANGER."""
    if metrics.sample_count == 0:
        return "DANGER"
    if (
        metrics.max_discomfort >= 5
        or metrics.avg_discomfort >= 4.0
        or metrics.avg_ui_readability < 3.0
        or metrics.avg_crosstalk >= 4.0
    ):
        return "DANGER"
    if (
        metrics.avg_discomfort >= 2.5
        or metrics.avg_ui_readability < 4.0
        or metrics.avg_depth_realism < 3.0
        or metrics.avg_crosstalk >= 2.5
    ):
        return "WARN"
    return "GOOD"


def load_comfort_csv(path: str | Path) -> list[ComfortSurveySample]:
    source = Path(path)
    with source.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [field for field in _FIELD_NAMES if field not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"comfort CSV missing fields: {', '.join(missing)}")
        return [_sample_from_row(row) for row in reader]


def run_benchmark(path: str | Path) -> ComfortBenchmarkResult:
    source = Path(path)
    metrics = compute_comfort_metrics(load_comfort_csv(source))
    return ComfortBenchmarkResult(
        source_path=source,
        quality=classify_comfort_quality(metrics),
        metrics=metrics,
    )


def format_benchmark_result(result: ComfortBenchmarkResult) -> str:
    m = result.metrics
    return "\n".join(
        [
            "Glassless3D Comfort Evaluation",
            f"quality={result.quality}",
            f"sample_count={m.sample_count}",
            f"avg_discomfort={m.avg_discomfort:.2f}",
            f"max_discomfort={m.max_discomfort}",
            f"avg_depth_realism={m.avg_depth_realism:.2f}",
            f"avg_ui_readability={m.avg_ui_readability:.2f}",
            f"avg_crosstalk={m.avg_crosstalk:.2f}",
        ]
    )


def format_benchmark_json(result: ComfortBenchmarkResult) -> str:
    m = result.metrics
    data = {
        "source_path": str(result.source_path),
        "quality": result.quality,
        "metrics": {
            "sample_count": m.sample_count,
            "avg_discomfort": m.avg_discomfort,
            "max_discomfort": m.max_discomfort,
            "avg_depth_realism": m.avg_depth_realism,
            "avg_ui_readability": m.avg_ui_readability,
            "avg_crosstalk": m.avg_crosstalk,
        },
    }
    return json.dumps(data, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Glassless3D comfort/display survey CSV")
    parser.add_argument("comfort_csv")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    result = run_benchmark(args.comfort_csv)
    text = format_benchmark_json(result) if args.format == "json" else format_benchmark_result(result)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote comfort evaluation to {output}")
    else:
        print(text)
    return 1 if result.quality == "DANGER" else 0


def _sample_from_row(row: dict[str, str]) -> ComfortSurveySample:
    values = {field: _score(row[field], field) for field in _FIELD_NAMES}
    return ComfortSurveySample(**values)


def _score(value: str, field: str) -> int:
    try:
        score = int(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an integer score from 1 to 5") from exc
    if score < 1 or score > 5:
        raise ValueError(f"{field} must be an integer score from 1 to 5")
    return score


if __name__ == "__main__":
    raise SystemExit(main())
