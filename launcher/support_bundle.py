"""Create shareable Glassless3D support bundles."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from launcher.diagnostics import collect_diagnostics, format_diagnostics_json
from tracker.display_acceptance import SOURCE_STEREO_PATH_CHOICES, write_acceptance_report
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
    display_acceptance_path: Path | None = None


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
    hardware_observation: str | Path | None = None,
    require_live_runtime: bool = False,
    require_face_tracking: bool = False,
    crosstalk_limit_percent: float | None = None,
    source_stereo_path: str | None = None,
    source_stereo_notes: str | None = None,
) -> SupportBundleManifest:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    diagnostics_path = out / "diagnostics.json"
    collect_kwargs = {"require_live_runtime": require_live_runtime}
    if require_face_tracking:
        collect_kwargs["require_face_tracking"] = True
    diagnostics = collect_diagnostics(config_path, **collect_kwargs)
    diagnostics_path.write_text(format_diagnostics_json(diagnostics) + "\n", encoding="utf-8")

    overlay_timings_path: Path | None = None
    if diagnostics.overlay_log is not None:
        candidate = out / "overlay_timings.csv"
        if export_overlay_frame_timings(diagnostics.overlay_log, candidate) > 0:
            overlay_timings_path = candidate

    display_acceptance_path: Path | None = None
    if require_live_runtime or hardware_observation is not None:
        acceptance_kwargs = {
            "config_path": config_path,
            "require_live_runtime": require_live_runtime,
            "hardware_observation_path": hardware_observation,
            "crosstalk_limit_percent": crosstalk_limit_percent,
            "source_stereo_path": source_stereo_path,
            "source_stereo_notes": source_stereo_notes,
            "diagnostics_report": diagnostics,
        }
        if require_face_tracking:
            acceptance_kwargs["require_face_tracking"] = True
        acceptance = write_acceptance_report(
            out / "display_acceptance",
            **acceptance_kwargs,
        )
        display_acceptance_path = acceptance.report_path
    acceptance_ready, acceptance_problems = _display_acceptance_status(display_acceptance_path)

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
        "display_acceptance": _relative_name(out, display_acceptance_path),
        "display_acceptance_ready": acceptance_ready,
        "display_acceptance_problems": acceptance_problems,
        "source_stereo": _source_stereo_metadata(source_stereo_path, source_stereo_notes),
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
        display_acceptance_path=display_acceptance_path,
        manifest_path=manifest_path,
    )


def _relative_name(root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    return path.relative_to(root).as_posix()


def _display_acceptance_status(path: Path | None) -> tuple[bool | None, list[str] | None]:
    if path is None:
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"display acceptance status could not be read: {exc}"]
    problems = payload.get("problems")
    return (
        payload.get("ready") is True,
        [str(problem) for problem in problems] if isinstance(problems, list) else [],
    )


def _source_stereo_metadata(source_path: str | None, notes: str | None) -> dict[str, str | None]:
    return {
        "path": str(source_path).strip() if source_path is not None and str(source_path).strip() else None,
        "notes": str(notes).strip() if notes is not None and str(notes).strip() else None,
    }


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
    parser.add_argument("--hardware-observation", help="YAML/JSON hardware observation for display acceptance")
    parser.add_argument("--require-live-runtime", action="store_true")
    parser.add_argument(
        "--require-face-tracking",
        action="store_true",
        help="Require a fresh detected face in G3D_State",
    )
    parser.add_argument(
        "--source-stereo-path",
        choices=SOURCE_STEREO_PATH_CHOICES,
        help="Upstream stereo/depth source represented by display acceptance",
    )
    parser.add_argument("--source-stereo-notes", help="Operator notes about the upstream stereo source")
    parser.add_argument(
        "--require-display-acceptance-ready",
        action="store_true",
        help="Return a nonzero exit code when display acceptance is missing or not ready",
    )
    parser.add_argument("--crosstalk-limit-percent", type=float, default=None)
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
        hardware_observation=args.hardware_observation,
        require_live_runtime=args.require_live_runtime,
        require_face_tracking=args.require_face_tracking,
        crosstalk_limit_percent=args.crosstalk_limit_percent,
        source_stereo_path=args.source_stereo_path,
        source_stereo_notes=args.source_stereo_notes,
    )
    print(f"wrote support bundle to {manifest.output_dir}")
    ready, problems = _display_acceptance_status(manifest.display_acceptance_path)
    if args.require_display_acceptance_ready and ready is None:
        print("display acceptance is required but was not generated")
        return 1
    if ready is not None:
        print(f"display acceptance: {'READY' if ready else 'NOT READY'}")
        for problem in problems or []:
            print(f"- {problem}")
        if args.require_display_acceptance_ready and not ready:
            print("display acceptance is required to be READY")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
