"""Collect overlay-first troubleshooting diagnostics."""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import yaml

from launcher.overlay_process import _project_root, find_depth_model, find_overlay_exe


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


def collect_diagnostics(config_path: str | Path = "config.yaml") -> DiagnosticsReport:
    """Return a single overlay-readiness diagnostic report."""
    root = _project_root()
    cfg_path = Path(config_path)
    if not cfg_path.is_absolute():
        cfg_path = root / cfg_path

    problems: list[str] = []
    overlay_exe = find_overlay_exe()
    depth_model = find_depth_model()

    if overlay_exe is None:
        problems.append("overlay executable missing")
    if depth_model is None:
        problems.append("depth model missing")

    config_loaded = _can_load_config(cfg_path, problems)
    ready = not problems

    return DiagnosticsReport(
        project_root=root,
        python_executable=Path(sys.executable),
        overlay_exe=overlay_exe,
        depth_model=depth_model,
        config_path=cfg_path,
        config_loaded=config_loaded,
        ready=ready,
        problems=problems,
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
        "",
        "Problems:",
    ]
    if report.problems:
        lines.extend(f"- {problem}" for problem in report.problems)
    else:
        lines.append("- none")

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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print Glassless3D diagnostics")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    args = parser.parse_args(argv)

    report = collect_diagnostics(args.config)
    print(format_diagnostics_report(report))
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


if __name__ == "__main__":
    raise SystemExit(main())
