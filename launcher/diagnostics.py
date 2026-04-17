"""Collect overlay-first troubleshooting diagnostics."""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import yaml

from launcher.overlay_process import _project_root, find_depth_model, find_overlay_exe
from tracker.display_backends import DisplayBackendRegistry, built_in_backends


@dataclass(frozen=True)
class OverlayRuntimeSummary:
    frame_count: int
    acq_ok: int
    acq_timeout: int
    acq_lost: int
    acq_other: int
    shm_status: str
    shm_changes_per_sec: int
    depth_total: int
    depth_hz: int
    head_z_cm: float
    has_frame: bool


@dataclass(frozen=True)
class DiagnosticsReport:
    project_root: Path
    python_executable: Path
    overlay_exe: Path | None
    depth_model: Path | None
    config_path: Path
    config_loaded: bool
    ready: bool
    problems: list[str]
    default_backend_id: str = "desktop_overlay"
    experimental_backend_ids: list[str] = field(default_factory=list)
    overlay_log: Path | None = None
    overlay_summary: OverlayRuntimeSummary | None = None
    warnings: list[str] = field(default_factory=list)


_SUMMARY_RE = re.compile(
    r"Frame#(?P<frame>\d+)\s+"
    r"acq\[ok=(?P<ok>\d+)\s+timeout=(?P<timeout>\d+)\s+lost=(?P<lost>\d+)\s+other=(?P<other>\d+)\]\s+"
    r"shm\[(?P<shm_status>.*?)\s+reads=\d+\s+changes=\d+\s+\((?P<changes_sec>-?\d+)/s\)\s+ts=\d+\]\s+"
    r"depth\[total=(?P<depth_total>\d+)\s+(?P<depth_hz>-?\d+)Hz\]\s+"
    r"head=\([^,]+,[^,]+,(?P<head_z>-?\d+(?:\.\d+)?)\).*?"
    r"hasFrame=(?P<has_frame>[01])"
)


def collect_diagnostics(config_path: str | Path = "config.yaml") -> DiagnosticsReport:
    """Return a single overlay-readiness diagnostic report."""
    root = _project_root()
    cfg_path = Path(config_path)
    if not cfg_path.is_absolute():
        cfg_path = root / cfg_path

    problems: list[str] = []
    warnings: list[str] = []
    overlay_exe = find_overlay_exe()
    depth_model = find_depth_model()

    if overlay_exe is None:
        problems.append("overlay executable missing")
    if depth_model is None:
        problems.append("depth model missing")

    config_loaded = _can_load_config(cfg_path, problems)
    overlay_log = _find_overlay_log(overlay_exe)
    overlay_summary = _latest_overlay_summary(overlay_log) if overlay_log else None
    registry = DisplayBackendRegistry(built_in_backends())
    default_backend_id = registry.default().id
    experimental_backend_ids = [backend.id for backend in registry.by_status("experimental")]
    if overlay_summary is not None:
        if not overlay_summary.shm_status.startswith("LIVE"):
            warnings.append("overlay log reports stale tracker shared memory")
        if overlay_summary.depth_hz <= 0:
            warnings.append("overlay log reports no active depth inference")
        if not overlay_summary.has_frame:
            warnings.append("overlay log reports no captured frame")
    ready = not problems

    return DiagnosticsReport(
        project_root=root,
        python_executable=Path(sys.executable),
        overlay_exe=overlay_exe,
        depth_model=depth_model,
        overlay_log=overlay_log,
        overlay_summary=overlay_summary,
        config_path=cfg_path,
        config_loaded=config_loaded,
        ready=ready,
        problems=problems,
        default_backend_id=default_backend_id,
        experimental_backend_ids=experimental_backend_ids,
        warnings=warnings,
    )


def format_diagnostics_report(report: DiagnosticsReport) -> str:
    status = "READY" if report.ready else "NOT READY"
    lines = [
        "Glassless3D Diagnostics",
        f"Status: {status}",
        "",
        f"Project root: {report.project_root}",
        f"Python: {report.python_executable}",
        f"Config: {report.config_path} ({'loaded' if report.config_loaded else 'not loaded'})",
        f"Overlay executable: {report.overlay_exe or 'missing'}",
        f"Depth model: {report.depth_model or 'missing'}",
        f"Overlay log: {report.overlay_log or 'not found'}",
        f"Display backend: {report.default_backend_id}",
        f"Experimental backends: {', '.join(report.experimental_backend_ids) or 'none'}",
        "",
        "Problems:",
    ]
    if report.problems:
        lines.extend(f"- {problem}" for problem in report.problems)
    else:
        lines.append("- none")
    lines.append("")
    lines.append("Warnings:")
    if report.warnings:
        lines.extend(f"- {warning}" for warning in report.warnings)
    else:
        lines.append("- none")

    if report.overlay_summary is not None:
        s = report.overlay_summary
        lines.extend(
            [
                "",
                "Latest overlay summary:",
                f"- frame: {s.frame_count}",
                f"- shm: {s.shm_status} ({s.shm_changes_per_sec}/s)",
                f"- depth: {s.depth_hz}Hz total={s.depth_total}",
                f"- headZ: {s.head_z_cm:.2f} cm",
                f"- hasFrame: {s.has_frame}",
            ]
        )

    lines.extend(
        [
            "",
            "Useful commands:",
            "- python scripts/bootstrap.py",
            "- python -m tracker.debug_monitor",
            "- pytest tests/ -q",
        ]
    )
    return "\n".join(lines)


def format_diagnostics_json(report: DiagnosticsReport) -> str:
    data = {
        "project_root": str(report.project_root),
        "python_executable": str(report.python_executable),
        "overlay_exe": str(report.overlay_exe) if report.overlay_exe else None,
        "depth_model": str(report.depth_model) if report.depth_model else None,
        "overlay_log": str(report.overlay_log) if report.overlay_log else None,
        "config_path": str(report.config_path),
        "config_loaded": report.config_loaded,
        "ready": report.ready,
        "problems": report.problems,
        "warnings": report.warnings,
        "default_backend_id": report.default_backend_id,
        "experimental_backend_ids": report.experimental_backend_ids,
        "overlay_summary": _summary_to_dict(report.overlay_summary),
    }
    return json.dumps(data, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print Glassless3D diagnostics")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--output", help="Optional path to write the diagnostics report")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args(argv)

    report = collect_diagnostics(args.config)
    text = (
        format_diagnostics_json(report)
        if args.format == "json"
        else format_diagnostics_report(report)
    )
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote diagnostics report to {output}")
    else:
        print(text)
    return 0 if report.ready else 1


def _can_load_config(path: Path, problems: list[str]) -> bool:
    if not path.is_file():
        problems.append("config file missing")
        return False
    try:
        with open(path, encoding="utf-8") as f:
            yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        problems.append(f"config unreadable: {e}")
        return False
    return True


def _summary_to_dict(summary: OverlayRuntimeSummary | None) -> dict[str, object] | None:
    if summary is None:
        return None
    return {
        "frame_count": summary.frame_count,
        "acq_ok": summary.acq_ok,
        "acq_timeout": summary.acq_timeout,
        "acq_lost": summary.acq_lost,
        "acq_other": summary.acq_other,
        "shm_status": summary.shm_status,
        "shm_changes_per_sec": summary.shm_changes_per_sec,
        "depth_total": summary.depth_total,
        "depth_hz": summary.depth_hz,
        "head_z_cm": summary.head_z_cm,
        "has_frame": summary.has_frame,
    }


def parse_overlay_summary_line(line: str) -> OverlayRuntimeSummary | None:
    match = _SUMMARY_RE.search(line)
    if match is None:
        return None
    return OverlayRuntimeSummary(
        frame_count=int(match.group("frame")),
        acq_ok=int(match.group("ok")),
        acq_timeout=int(match.group("timeout")),
        acq_lost=int(match.group("lost")),
        acq_other=int(match.group("other")),
        shm_status=match.group("shm_status"),
        shm_changes_per_sec=int(match.group("changes_sec")),
        depth_total=int(match.group("depth_total")),
        depth_hz=int(match.group("depth_hz")),
        head_z_cm=float(match.group("head_z")),
        has_frame=match.group("has_frame") == "1",
    )


def _find_overlay_log(overlay_exe: Path | None) -> Path | None:
    root = _project_root()
    candidates: list[Path] = []
    if overlay_exe is not None:
        candidates.append(overlay_exe.parent / "overlay.log")
    candidates.extend(
        [
            root / "overlay.log",
            root / "overlay" / "build_mingw" / "overlay.log",
            root / "overlay" / "build" / "overlay.log",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _latest_overlay_summary(path: Path) -> OverlayRuntimeSummary | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    for line in reversed(lines):
        summary = parse_overlay_summary_line(line)
        if summary is not None:
            return summary
    return None


if __name__ == "__main__":
    raise SystemExit(main())
