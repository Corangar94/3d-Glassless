"""Create shareable Glassless3D support bundles."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from launcher.diagnostics import collect_diagnostics, format_diagnostics_json
from tracker.depth_fixtures import DEFAULT_FIXTURE_ROOT
from tracker.evaluation_suite import format_suite_json, run_suite
from tracker.feasibility_gate import decide_gate, format_assessment_json, wow_default_checks
from tracker.performance_capture import export_overlay_frame_timings


@dataclass(frozen=True)
class SupportBundleManifest:
    output_dir: Path
    diagnostics_path: Path
    manifest_path: Path
    feasibility_wow_path: Path
    evaluation_path: Path | None = None
    overlay_timings_path: Path | None = None


def create_support_bundle(
    output_dir: str | Path,
    config_path: str | Path = "config.yaml",
    depth_dir: str | Path | None = None,
    depth_fixture: str | None = None,
    depth_fixture_root: str | Path = DEFAULT_FIXTURE_ROOT,
    timing_csv: str | Path | None = None,
    comfort_csv: str | Path | None = None,
    display_quality_csv: str | Path | None = None,
    latency_csv: str | Path | None = None,
    target_fps: float = 60.0,
    latency_target_ms: float = 20.0,
) -> SupportBundleManifest:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    diagnostics_path = out / "diagnostics.json"
    diagnostics = collect_diagnostics(config_path)
    diagnostics_path.write_text(format_diagnostics_json(diagnostics) + "\n", encoding="utf-8")

    overlay_timings_path: Path | None = None
    if diagnostics.overlay_log is not None:
        candidate = out / "overlay_timings.csv"
        if export_overlay_frame_timings(diagnostics.overlay_log, candidate) > 0:
            overlay_timings_path = candidate

    feasibility_wow_path = out / "feasibility_wow.json"
    feasibility_wow = decide_gate("World of Warcraft", wow_default_checks())
    feasibility_wow_path.write_text(format_assessment_json(feasibility_wow) + "\n", encoding="utf-8")

    evaluation_path: Path | None = None
    if (
        depth_dir is not None
        or depth_fixture is not None
        or timing_csv is not None
        or comfort_csv is not None
        or display_quality_csv is not None
        or latency_csv is not None
    ):
        evaluation_path = out / "evaluation.json"
        evaluation = run_suite(
            depth_dir=depth_dir,
            depth_fixture=depth_fixture,
            depth_fixture_root=depth_fixture_root,
            timing_csv=timing_csv,
            comfort_csv=comfort_csv,
            display_quality_csv=display_quality_csv,
            latency_csv=latency_csv,
            target_fps=target_fps,
            latency_target_ms=latency_target_ms,
        )
        evaluation_path.write_text(format_suite_json(evaluation) + "\n", encoding="utf-8")

    manifest_path = out / "manifest.json"
    manifest_data = {
        "diagnostics": diagnostics_path.name,
        "evaluation": evaluation_path.name if evaluation_path else None,
        "feasibility_wow": feasibility_wow_path.name,
        "overlay_timings": overlay_timings_path.name if overlay_timings_path else None,
    }
    manifest_path.write_text(json.dumps(manifest_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return SupportBundleManifest(
        output_dir=out,
        diagnostics_path=diagnostics_path,
        feasibility_wow_path=feasibility_wow_path,
        evaluation_path=evaluation_path,
        overlay_timings_path=overlay_timings_path,
        manifest_path=manifest_path,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a Glassless3D support bundle")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--depth-dir")
    parser.add_argument("--depth-fixture")
    parser.add_argument("--depth-fixture-root", default=str(DEFAULT_FIXTURE_ROOT))
    parser.add_argument("--timing-csv")
    parser.add_argument("--comfort-csv")
    parser.add_argument("--display-quality-csv")
    parser.add_argument("--latency-csv")
    parser.add_argument("--target-fps", type=float, default=60.0)
    parser.add_argument("--latency-target-ms", type=float, default=20.0)
    args = parser.parse_args(argv)

    manifest = create_support_bundle(
        output_dir=args.output_dir,
        config_path=args.config,
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
    print(f"wrote support bundle to {manifest.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
