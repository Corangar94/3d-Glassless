"""Run deterministic tracker replay acceptance and optional auto-tuning."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import yaml

from tracker.replay_quality import FilterSettings, ReplayReport, benchmark, tune


def _load_settings(path: Path | None) -> FilterSettings:
    if path is None or not path.exists():
        return FilterSettings()
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("config top level must be a mapping")
    tracking = loaded.get("tracking", {})
    if not isinstance(tracking, dict):
        raise ValueError("tracking config must be a mapping")
    return FilterSettings(
        process_noise=float(tracking.get("smoothing_q", 2.0)),
        measurement_noise=float(tracking.get("smoothing_r", 0.1)),
        prediction_horizon_ms=float(
            tracking.get("prediction_horizon_ms", 0.0)
        ),
        max_prediction_ms=float(tracking.get("max_prediction_ms", 80.0)),
    )


def _write_settings(path: Path, settings: FilterSettings) -> None:
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ValueError("config top level must be a mapping")
    else:
        loaded = {}
    tracking = loaded.setdefault("tracking", {})
    if not isinstance(tracking, dict):
        raise ValueError("tracking config must be a mapping")
    tracking["smoothing_q"] = settings.process_noise
    tracking["smoothing_r"] = settings.measurement_noise
    tracking["prediction_horizon_ms"] = settings.prediction_horizon_ms
    tracking["max_prediction_ms"] = settings.max_prediction_ms
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(
        yaml.safe_dump(loaded, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _print_report(report: ReplayReport) -> None:
    print(
        "Software replay:",
        "PASS" if report.passed else "FAIL",
        f"score={report.weighted_score:.3f}",
    )
    print(
        "Settings:",
        f"q={report.settings.process_noise:g}",
        f"r={report.settings.measurement_noise:g}",
        f"horizon={report.settings.prediction_horizon_ms:g}ms",
        f"max={report.settings.max_prediction_ms:g}ms",
    )
    for result in report.scenarios:
        print(
            f"  {result.name:20s}",
            f"rmse={result.filtered.position_rmse_cm:.3f}cm",
            f"raw={result.raw_hold.position_rmse_cm:.3f}cm",
            f"ratio={result.improvement_ratio:.3f}",
            f"p95={result.filtered.position_p95_cm:.3f}cm",
            f"lag={result.filtered.x_lag_ms:.1f}ms",
            f"jitter={result.filtered.x_jitter_cm:.3f}cm",
        )
    for failure in report.failures:
        print(f"  gate failure: {failure}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic software-only tracking replay, regression gate, "
            "and filter tuner"
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="load current tracking filter values from this YAML config",
    )
    parser.add_argument(
        "--tune",
        action="store_true",
        help="search the bounded deterministic tuning grid",
    )
    parser.add_argument(
        "--write-config",
        action="store_true",
        help="atomically write the evaluated/recommended settings to --config",
    )
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-markdown", type=Path)
    parser.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="return exit code 1 when software acceptance thresholds fail",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        current = _load_settings(args.config)
        report = tune() if args.tune else benchmark(current)
        _print_report(report)
        if args.output_json is not None:
            report.write_json(args.output_json)
        if args.output_markdown is not None:
            report.write_markdown(args.output_markdown)
        if args.write_config:
            _write_settings(args.config, report.settings)
            print(f"Wrote recommended tracking settings to {args.config}")
        return 1 if args.fail_on_regression and not report.passed else 0
    except Exception as error:
        print(f"Replay benchmark failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
