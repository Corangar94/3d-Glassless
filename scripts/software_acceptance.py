"""Run Glassless3D software-only tracking and virtual-window acceptance."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from tracker.parallax_quality import (
    ParallaxSettings,
    evaluate_parallax_gate,
    write_validation_sequence,
)
from tracker.replay_quality import FilterSettings, benchmark, tune


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run deterministic pose replay and parallax-geometry acceptance "
            "without a webcam, monitor, or GPU"
        )
    )
    parser.add_argument("--output-dir", type=Path, default=Path("software_acceptance"))
    parser.add_argument("--tune", action="store_true")
    parser.add_argument("--generate-demo", action="store_true")
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument("--screen-width-cm", type=float, default=60.0)
    parser.add_argument("--screen-height-cm", type=float, default=34.0)
    parser.add_argument("--head-distance-cm", type=float, default=60.0)
    parser.add_argument("--virtual-depth-cm", type=float, default=30.0)
    parser.add_argument("--focus-plane-cm", type=float, default=10.0)
    return parser


def _markdown(replay_report, parallax_result) -> str:
    lines = [
        "# Glassless3D software-only acceptance",
        "",
        f"- Overall: **{'PASS' if replay_report.passed and parallax_result.passed else 'FAIL'}**",
        f"- Replay score: `{replay_report.weighted_score:.3f}`",
        f"- Parallax focus depth: `{parallax_result.focus_depth:.3f}`",
        f"- Maximum reference shift: `{parallax_result.maximum_shift_uv:.4f} UV`",
        "",
        "## Replay scenarios",
        "",
        "| Scenario | Filter RMSE | Raw RMSE | Ratio | Lag | Jitter |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for result in replay_report.scenarios:
        lines.append(
            f"| {result.name} | {result.filtered.position_rmse_cm:.3f} cm | "
            f"{result.raw_hold.position_rmse_cm:.3f} cm | "
            f"{result.improvement_ratio:.3f} | "
            f"{result.filtered.x_lag_ms:.1f} ms | "
            f"{result.filtered.x_jitter_cm:.3f} cm |"
        )
    lines.extend(
        (
            "",
            "## Parallax geometry",
            "",
            f"- Shallow shift: `{parallax_result.shallow_shift_x:+.6f} UV`",
            f"- Focus shift: `{parallax_result.focus_shift_x:+.6f} UV`",
            f"- Deep shift: `{parallax_result.deep_shift_x:+.6f} UV`",
        )
    )
    failures = list(replay_report.failures) + list(parallax_result.failures)
    if failures:
        lines.extend(("", "## Failures", ""))
        lines.extend(f"- {failure}" for failure in failures)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        replay_report = tune() if args.tune else benchmark(FilterSettings())
        settings = ParallaxSettings(
            screen_width_cm=args.screen_width_cm,
            screen_height_cm=args.screen_height_cm,
            head_distance_cm=args.head_distance_cm,
            virtual_depth_cm=args.virtual_depth_cm,
            focus_plane_cm=args.focus_plane_cm,
        )
        parallax_result = evaluate_parallax_gate(settings)
        output_dir: Path = args.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        replay_report.write_json(output_dir / "replay.json")
        replay_report.write_markdown(output_dir / "replay.md")
        parallax_result.write_json(output_dir / "parallax.json")
        combined = {
            "passed": replay_report.passed and parallax_result.passed,
            "replay": replay_report.to_mapping(),
            "parallax": parallax_result.to_mapping(),
        }
        (output_dir / "software_acceptance.json").write_text(
            json.dumps(combined, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        (output_dir / "software_acceptance.md").write_text(
            _markdown(replay_report, parallax_result),
            encoding="utf-8",
            newline="\n",
        )
        if args.generate_demo:
            write_validation_sequence(output_dir / "virtual_window_demo", settings=settings)
        passed = bool(combined["passed"])
        print(
            "Software-only acceptance:",
            "PASS" if passed else "FAIL",
            f"replay_score={replay_report.weighted_score:.3f}",
            f"max_shift={parallax_result.maximum_shift_uv:.4f}UV",
        )
        return 1 if args.fail_on_regression and not passed else 0
    except Exception as error:
        print(f"Software acceptance failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
