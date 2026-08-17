#!/usr/bin/env python3
"""Audit Glassless3D completion evidence from saved artifact JSON files."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker.target_displays import inventory_text_is_known_target


DEFAULT_INVENTORY = Path(r"E:\CodexTemp\glassless_current_display_inventory.json")
DEFAULT_DESKTOP_ACCEPTANCE = Path(r"E:\CodexTemp\glassless_desktop_acceptance_20260514_framegate\acceptance_report.json")
DEFAULT_DESKTOP_SUPPORT = Path(r"E:\CodexTemp\glassless_desktop_strict_support_ready_20260514\manifest.json")
DEFAULT_STEREO_ACCEPTANCE = Path(
    r"E:\CodexTemp\glassless_stereo_devicebind_gate_20260514\acceptance\acceptance_report.json"
)
DEFAULT_STEREO_SUPPORT = Path(r"E:\CodexTemp\glassless_stereo_strict_support_gate_20260514\manifest.json")
DEFAULT_QUILT_ACCEPTANCE = Path(
    r"E:\CodexTemp\glassless_quilt_devicebind_gate_20260514\acceptance\acceptance_report.json"
)
DEFAULT_QUILT_SUPPORT = Path(r"E:\CodexTemp\glassless_quilt_strict_support_gate_20260514\manifest.json")

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit Glassless3D completion artifacts")
    parser.add_argument("--inventory", default=str(DEFAULT_INVENTORY), help="diagnostics JSON with display_inventory")
    parser.add_argument("--desktop-acceptance", default=str(DEFAULT_DESKTOP_ACCEPTANCE))
    parser.add_argument("--desktop-support", default=str(DEFAULT_DESKTOP_SUPPORT))
    parser.add_argument("--stereo-acceptance", default=str(DEFAULT_STEREO_ACCEPTANCE))
    parser.add_argument("--stereo-support", default=str(DEFAULT_STEREO_SUPPORT))
    parser.add_argument("--quilt-acceptance", default=str(DEFAULT_QUILT_ACCEPTANCE))
    parser.add_argument("--quilt-support", default=str(DEFAULT_QUILT_SUPPORT))
    parser.add_argument(
        "--require-target-display-hardware",
        action="store_true",
        help="also require optional autostereo/light-field target-display evidence",
    )
    parser.add_argument(
        "--max-evidence-age-hours",
        type=float,
        default=24.0,
        help="Reject saved acceptance evidence older than this many hours (default: 24).",
    )
    args = parser.parse_args(argv)

    if args.max_evidence_age_hours <= 0:
        parser.error("--max-evidence-age-hours must be greater than zero")

    failures: list[str] = []
    max_age_hours = float(args.max_evidence_age_hours)
    _check_acceptance(Path(args.desktop_acceptance), "desktop acceptance", failures, max_age_hours=max_age_hours)
    _check_support(Path(args.desktop_support), "desktop support bundle", failures, max_age_hours=max_age_hours)
    if args.require_target_display_hardware:
        _check_inventory(Path(args.inventory), failures, max_age_hours=max_age_hours)
        _check_acceptance(Path(args.stereo_acceptance), "stereo acceptance", failures, require_hardware=True, max_age_hours=max_age_hours)
        _check_support(Path(args.stereo_support), "stereo support bundle", failures, require_hardware=True, max_age_hours=max_age_hours)
        _check_acceptance(Path(args.quilt_acceptance), "quilt acceptance", failures, require_hardware=True, max_age_hours=max_age_hours)
        _check_support(Path(args.quilt_support), "quilt support bundle", failures, require_hardware=True, max_age_hours=max_age_hours)

    if failures:
        print("completion audit: NOT READY")
        for failure in failures:
            print(f"- {failure}")
        if args.require_target_display_hardware:
            print("next step: follow docs/HARDWARE_ACCEPTANCE_CHECKLIST.md with a connected target display")
        else:
            print("next step: rerun the desktop webcam/head-tracked acceptance and support bundle checks")
        return 1
    print("completion audit: READY")
    return 0


def _check_inventory(path: Path, failures: list[str], *, max_age_hours: float) -> None:
    payload = _read_json(path, "display inventory", failures, max_age_hours=max_age_hours)
    if payload is None:
        return
    inventory = payload.get("display_inventory")
    if not isinstance(inventory, list):
        failures.append("display inventory is missing display_inventory[]")
        return
    if not any(_inventory_item_is_target(item) for item in inventory):
        failures.append("target display inventory does not include a known glassless/autostereo/light-field panel")


def _check_acceptance(
    path: Path,
    label: str,
    failures: list[str],
    require_hardware: bool = False,
    *,
    max_age_hours: float,
) -> None:
    payload = _read_json(path, label, failures, max_age_hours=max_age_hours)
    if payload is None:
        return
    problems = _problem_list(payload.get("problems"))
    if payload.get("ready") is True and problems:
        failures.append(f"{label} reports problems despite ready=true: {'; '.join(problems)}")
        return
    if payload.get("ready") is True and not problems:
        if label.startswith("desktop"):
            _check_face_tracking_evidence(payload, label, failures)
        if require_hardware:
            _check_hardware_acceptance_payload(payload, label, failures)
        return
    suffix = f": {'; '.join(problems)}" if problems else ""
    failures.append(f"{label} is not ready{suffix}")


def _check_hardware_acceptance_payload(payload: dict[str, object], label: str, failures: list[str]) -> None:
    if not payload.get("hardware_observation_path"):
        failures.append(f"{label} missing hardware_observation_path")
    observation = payload.get("hardware_observation")
    if not isinstance(observation, dict):
        failures.append(f"{label} missing hardware_observation payload")
    elif _observation_device_id_is_placeholder(observation):
        failures.append(f"{label} hardware observation target_display_device_id is still a placeholder")
    checklist = payload.get("checklist")
    if not isinstance(checklist, dict):
        failures.append(f"{label} missing checklist")
        return
    for field in (
        "runtime_ready",
        "backend_match",
        "calibration_match",
        "hardware_observation_passed",
        "target_display_observation_matched",
    ):
        if checklist.get(field) is not True:
            failures.append(f"{label} checklist.{field} is not true")


def _check_support(
    path: Path,
    label: str,
    failures: list[str],
    require_hardware: bool = False,
    *,
    max_age_hours: float,
) -> None:
    payload = _read_json(path, label, failures, max_age_hours=max_age_hours)
    if payload is None:
        return
    problems = _problem_list(payload.get("display_acceptance_problems"))
    if payload.get("display_acceptance_ready") is True and not problems:
        display_acceptance = payload.get("display_acceptance")
        if not display_acceptance:
            failures.append(f"{label} missing display_acceptance path")
            return
        if not isinstance(display_acceptance, str):
            failures.append(f"{label} display_acceptance path must be a string")
            return
        acceptance_path = path.parent / display_acceptance
        if not _path_is_relative_to(acceptance_path, path.parent):
            failures.append(f"{label} display_acceptance path escapes support bundle: {display_acceptance}")
            return
        if not acceptance_path.exists():
            failures.append(f"{label} display_acceptance artifact missing: {acceptance_path}")
            return
        _check_referenced_support_acceptance(
            acceptance_path,
            label,
            failures,
            require_hardware=require_hardware,
            max_age_hours=max_age_hours,
        )
        return
    if payload.get("display_acceptance_ready") is True and problems:
        failures.append(f"{label} reports display acceptance problems despite ready=true: {'; '.join(problems)}")
        return
    suffix = f": {'; '.join(problems)}" if problems else ""
    failures.append(f"{label} display acceptance is not ready{suffix}")


def _read_json(
    path: Path,
    label: str,
    failures: list[str],
    *,
    max_age_hours: float,
) -> dict[str, object] | None:
    try:
        age_seconds = max(0.0, time.time() - path.stat().st_mtime)
        if age_seconds > max_age_hours * 3600.0:
            failures.append(
                f"{label} artifact is stale: {age_seconds / 3600.0:.1f} hours old "
                f"(limit {max_age_hours:.1f})"
            )
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        failures.append(f"{label} artifact missing: {path}")
        return None
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"{label} artifact could not be read: {exc}")
        return None
    if not isinstance(payload, dict):
        failures.append(f"{label} artifact must be a JSON object: {path}")
        return None
    return payload


def _check_referenced_support_acceptance(
    path: Path,
    label: str,
    failures: list[str],
    require_hardware: bool = False,
    *,
    max_age_hours: float,
) -> None:
    payload = _read_json(
        path,
        f"{label} display_acceptance",
        failures,
        max_age_hours=max_age_hours,
    )
    if payload is None:
        return
    problems = _problem_list(payload.get("problems"))
    if payload.get("ready") is True and not problems:
        if label.startswith("desktop"):
            _check_face_tracking_evidence(payload, f"{label} display_acceptance", failures)
        if require_hardware:
            _check_hardware_acceptance_payload(payload, f"{label} display_acceptance", failures)
        return
    suffix = f": {'; '.join(problems)}" if problems else ""
    failures.append(f"{label} display_acceptance artifact is not ready{suffix}")


def _check_face_tracking_evidence(
    payload: dict[str, object],
    label: str,
    failures: list[str],
) -> None:
    checklist = payload.get("checklist")
    if payload.get("face_tracking_required") is not True:
        failures.append(f"{label} was not generated with face tracking required")
    if not isinstance(checklist, dict) or checklist.get("face_tracking_active") is not True:
        failures.append(f"{label} does not prove active face tracking")


def _inventory_item_is_target(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    haystack = " ".join(str(item.get(field) or "").lower() for field in ("name", "device_id", "manufacturer", "product_code"))
    return inventory_text_is_known_target(haystack)


def _problem_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _observation_device_id_is_placeholder(observation: dict[str, object]) -> bool:
    value = str(observation.get("target_display_device_id", "")).strip().lower()
    return "replace_with" in value or "replace-" in value


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
