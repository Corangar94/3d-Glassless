"""Display-backend acceptance report generation."""
from __future__ import annotations

import argparse
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import yaml

from launcher import diagnostics
from tracker.stereo_validation import ValidationAssets, write_validation_assets
from tracker.target_displays import inventory_text_is_known_target


@dataclass(frozen=True)
class AcceptanceManifest:
    output_dir: Path
    report_path: Path
    diagnostics_path: Path
    validation_assets: ValidationAssets


def write_acceptance_report(
    output_dir: str | Path,
    config_path: str | Path = "config.yaml",
    width: int = 640,
    height: int = 360,
    max_parallax_px: float = 8.0,
    require_live_runtime: bool = False,
    require_face_tracking: bool = False,
    hardware_observation_path: str | Path | None = None,
    crosstalk_limit_percent: float | None = None,
    diagnostics_report: diagnostics.DiagnosticsReport | None = None,
    source_stereo_path: str | None = None,
    source_stereo_notes: str | None = None,
) -> AcceptanceManifest:
    """Write validation assets and an operator acceptance JSON report."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    collect_kwargs = {"require_live_runtime": require_live_runtime}
    if require_face_tracking:
        collect_kwargs["require_face_tracking"] = True
    report = diagnostics_report or diagnostics.collect_diagnostics(
        config_path,
        **collect_kwargs,
    )
    diagnostics_path = out / "diagnostics.json"
    diagnostics_path.write_text(diagnostics.format_diagnostics_json(report) + "\n", encoding="utf-8")

    validation_assets = write_validation_assets(
        out / "validation",
        backend_id=report.configured_backend_id,
        config_path=report.config_path,
        width=width,
        height=height,
        max_parallax_px=max_parallax_px,
    )

    report_path = out / "acceptance_report.json"
    hardware_observation_source = Path(hardware_observation_path) if hardware_observation_path is not None else None
    hardware_observation: dict[str, object] | None = None
    hardware_observation_problems: list[str] = []
    try:
        hardware_observation = _load_hardware_observation(hardware_observation_source)
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        hardware_observation_problems.append(f"hardware observation could not be loaded: {exc}")
    try:
        hardware_observation_copy = _copy_hardware_observation(out, hardware_observation_source)
    except OSError as exc:
        hardware_observation_copy = None
        hardware_observation_problems.append(f"hardware observation could not be copied: {exc}")
    effective_crosstalk_limit = _effective_crosstalk_limit(
        hardware_observation,
        crosstalk_limit_percent,
    )
    hardware_template_path = _write_hardware_observation_template(
        out,
        report.configured_backend_id,
        hardware_observation,
        crosstalk_limit_percent=effective_crosstalk_limit,
    )
    payload = _acceptance_payload(
        report,
        validation_assets,
        report_root=out,
        diagnostics_path=diagnostics_path,
        hardware_observation=hardware_observation,
        hardware_observation_path=hardware_observation_copy,
        hardware_template_path=hardware_template_path,
        crosstalk_limit_percent=effective_crosstalk_limit,
        extra_problems=hardware_observation_problems,
        source_stereo_path=source_stereo_path,
        source_stereo_notes=source_stereo_notes,
        face_tracking_required=require_face_tracking,
    )
    report_path.write_text(
        json.dumps(_json_safe(payload), allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return AcceptanceManifest(
        output_dir=out,
        report_path=report_path,
        diagnostics_path=diagnostics_path,
        validation_assets=validation_assets,
    )


def _acceptance_payload(
    report: diagnostics.DiagnosticsReport,
    validation_assets: ValidationAssets,
    report_root: Path | None = None,
    diagnostics_path: Path | None = None,
    hardware_observation: dict[str, object] | None = None,
    hardware_observation_path: Path | None = None,
    hardware_template_path: Path | None = None,
    crosstalk_limit_percent: object = 10.0,
    extra_problems: Sequence[str] = (),
    source_stereo_path: str | None = None,
    source_stereo_notes: str | None = None,
    face_tracking_required: bool = False,
) -> dict[str, object]:
    backend_match = (
        report.runtime_backend_id is not None
        and report.runtime_backend_id == report.configured_backend_id
    )
    calibration_match = not any(
        "runtime " in problem and " does not match configured " in problem
        for problem in report.problems
    )
    has_captured_frame = bool(
        report.overlay_summary is not None
        and report.overlay_summary.has_frame
    )
    runtime_ready = report.ready and has_captured_frame
    face_tracking_active = (
        report.tracking_state == "tracking" and report.tracking_state_fresh
    )
    runtime_problems = []
    if report.overlay_summary is not None and not has_captured_frame:
        runtime_problems.append("overlay runtime has no captured frame")
    crosstalk_limit_problem = _crosstalk_limit_problem(crosstalk_limit_percent)
    hardware_problems = _hardware_observation_problems(
        hardware_observation,
        configured_backend_id=report.configured_backend_id,
        crosstalk_limit_percent=crosstalk_limit_percent,
    )
    target_display_type_problem = _target_display_type_problem(
        hardware_observation,
        report.configured_backend_id,
    )
    hardware_required = report.configured_backend_id != "desktop_overlay"
    target_display_observation_matched = _target_display_observation_matched(
        hardware_observation,
        getattr(report, "display_inventory", []),
        report.configured_backend_id,
    )
    target_display_detected = _target_display_detected(
        report.configured_backend_id,
        getattr(report, "display_inventory", []),
    )
    observation_device_id_in_inventory = _observation_device_id_in_inventory(
        hardware_observation,
        getattr(report, "display_inventory", []),
    )
    explicit_observation_device_id_mismatch = _explicit_observation_device_id_mismatch(
        hardware_observation,
        observation_device_id_in_inventory,
    )
    target_display_problem = _target_display_problem(
        report.configured_backend_id,
        target_display_detected,
        target_display_observation_matched,
        observation_device_id_in_inventory,
        hardware_observation,
    )
    observation_device_id_problem = (
        "target display observation device_id does not match connected display inventory"
        if explicit_observation_device_id_mismatch
        else None
    )
    threshold_passed = crosstalk_limit_problem is None
    hardware_passed = (
        threshold_passed
        and not hardware_problems
        and not extra_problems
        and not explicit_observation_device_id_mismatch
        and target_display_type_problem is None
    )
    validation_assets_generated = _validation_assets_generated(validation_assets)
    validation_asset_problems = _validation_asset_problems(validation_assets)
    problems = [
        *report.problems,
        *[problem for problem in runtime_problems if problem not in report.problems],
        *extra_problems,
        *([observation_device_id_problem] if observation_device_id_problem else []),
        *([crosstalk_limit_problem] if crosstalk_limit_problem else []),
        *([target_display_type_problem] if target_display_type_problem else []),
        *([target_display_problem] if target_display_problem else []),
        *validation_asset_problems,
        *hardware_problems,
    ]
    next_steps = _next_steps(
        report.configured_backend_id,
        runtime_ready,
        backend_match,
        calibration_match,
        validation_assets_generated,
        hardware_required,
        target_display_detected,
        target_display_observation_matched,
        hardware_observation,
        hardware_passed,
        target_display_type_problem is not None,
        target_display_problem is not None,
    )
    return {
        "schema_version": 1,
        "ready": bool(
            runtime_ready
            and backend_match
            and calibration_match
            and threshold_passed
            and not extra_problems
            and not explicit_observation_device_id_mismatch
            and target_display_type_problem is None
            and target_display_problem is None
            and hardware_passed
            and validation_assets_generated
        ),
        "configured_backend_id": report.configured_backend_id,
        "runtime_backend_id": report.runtime_backend_id,
        "display_calibration": report.display_calibration,
        "source_stereo": _source_stereo_metadata(source_stereo_path, source_stereo_notes),
        "face_tracking_required": face_tracking_required,
        "display_inventory": [
            _display_inventory_to_dict(item)
            for item in getattr(report, "display_inventory", [])
        ],
        "diagnostics_path": _report_path(report_root, diagnostics_path),
        "hardware_observation": hardware_observation,
        "hardware_observation_path": _report_path(report_root, hardware_observation_path),
        "hardware_observation_template": _report_path(report_root, hardware_template_path),
        "problems": problems,
        "next_steps": next_steps,
        "warnings": report.warnings,
        "checklist": {
            "runtime_ready": bool(runtime_ready),
            "face_tracking_active": bool(face_tracking_active),
            "backend_match": bool(backend_match),
            "calibration_match": bool(calibration_match),
            "hardware_observation_required": bool(hardware_required),
            "hardware_observation_passed": bool(hardware_passed),
            "target_display_detected": bool(target_display_detected),
            "target_display_observation_matched": bool(target_display_observation_matched),
            "crosstalk_limit_percent": _finite_float_or_none(crosstalk_limit_percent),
            "validation_assets_generated": validation_assets_generated,
        },
        "validation": {
            "image_path": _report_path(report_root, validation_assets.image_path),
            "depth_path": _report_path(report_root, validation_assets.depth_path),
            "output_path": _report_path(report_root, validation_assets.output_path),
        },
    }


def _next_steps(
    configured_backend_id: str,
    runtime_ready: bool,
    backend_match: bool,
    calibration_match: bool,
    validation_assets_generated: bool,
    hardware_required: bool,
    target_display_detected: bool,
    target_display_observation_matched: bool,
    hardware_observation: dict[str, object] | None,
    hardware_passed: bool,
    target_display_type_failed: bool,
    target_display_failed: bool,
) -> list[str]:
    steps: list[str] = []
    if not runtime_ready:
        steps.append("run scripts/run_live_runtime_check.py and fix diagnostics problems")
    if not backend_match:
        steps.append(f"start the overlay with configured backend {configured_backend_id}")
    if not calibration_match:
        steps.append("update display calibration so runtime values match config.yaml")
    if not validation_assets_generated:
        steps.append("regenerate validation assets with scripts/run_display_acceptance.py")
    if hardware_required and not target_display_detected:
        steps.append("connect or select the target glassless/autostereo/light-field display")
    if hardware_required and hardware_observation is None:
        steps.append("fill hardware_observation.yaml after viewing validation output on the target display")
    elif hardware_required and target_display_type_failed:
        steps.append("fix hardware_observation.yaml so every hardware checklist field passes")
    elif hardware_required and target_display_detected and not target_display_observation_matched:
        steps.append("fill hardware_observation.yaml after viewing validation output on the target display")
    elif hardware_required and target_display_failed and hardware_observation is not None:
        steps.append("fix hardware_observation.yaml so every hardware checklist field passes")
    elif hardware_required and not hardware_passed:
        steps.append("fix hardware_observation.yaml so every hardware checklist field passes")
    elif hardware_observation is not None and not hardware_passed:
        steps.append("fix hardware_observation.yaml so every hardware checklist field passes")
    return steps


def _report_path(root: Path | None, path: Path | None) -> str | None:
    if path is None:
        return None
    if root is None:
        return str(path)
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _display_inventory_to_dict(item: object) -> dict[str, object]:
    return {
        "name": getattr(item, "name", "Unknown display"),
        "device_id": getattr(item, "device_id", None),
        "manufacturer": getattr(item, "manufacturer", None),
        "product_code": getattr(item, "product_code", None),
        "width_px": getattr(item, "width_px", None),
        "height_px": getattr(item, "height_px", None),
        "primary": bool(getattr(item, "primary", False)),
    }


_TARGET_DISPLAY_TYPES = {
    "autostereo",
    "glassless",
    "lightfield",
    "spatial",
    "simulated_reality",
    "sr",
}
_BACKEND_TARGET_DISPLAY_TYPES = {
    "stereo_autostereo": {
        "autostereo",
        "glassless",
        "simulated_reality",
        "spatial",
        "sr",
    },
    "lightfield_quilt": {
        "glassless",
        "lightfield",
        "spatial",
    },
}
_SOURCE_STEREO_PATHS = {
    "overlay_depth_reprojection",
    "native_stereo",
    "geo11",
    "geo3d",
    "depth3d",
    "rendepth",
    "3dgamebridge",
    "external_sbs",
    "external_ou",
    "other",
}
SOURCE_STEREO_PATH_CHOICES = tuple(sorted(_SOURCE_STEREO_PATHS))


def _target_display_detected(backend_id: str, inventory: object) -> bool:
    if backend_id == "desktop_overlay":
        return True
    if not isinstance(inventory, list):
        return False
    for item in inventory:
        if _display_inventory_item_is_target(item):
            return True
    return False


def _display_inventory_item_is_target(item: object) -> bool:
    haystack = " ".join(
        str(getattr(item, field, "") or "").lower()
        for field in ("name", "device_id", "manufacturer", "product_code")
    )
    return inventory_text_is_known_target(haystack)


def _target_display_observation_matched(
    observation: dict[str, object] | None,
    inventory: object,
    configured_backend_id: str,
) -> bool:
    if observation is None or not isinstance(inventory, list):
        return False
    target_id = _normalized_observation_text(observation.get("target_display_device_id"))
    target_type = _normalized_observation_text(observation.get("target_display_type"))
    if not target_id or target_type not in _TARGET_DISPLAY_TYPES:
        return False
    allowed_for_backend = _BACKEND_TARGET_DISPLAY_TYPES.get(configured_backend_id)
    if allowed_for_backend is not None and target_type not in allowed_for_backend:
        return False
    return any(
        _normalized_observation_text(getattr(item, "device_id", None)) == target_id
        and _display_inventory_item_is_target(item)
        for item in inventory
    )


def _observation_device_id_in_inventory(
    observation: dict[str, object] | None,
    inventory: object,
) -> bool:
    if observation is None or not isinstance(inventory, list):
        return False
    target_id = _normalized_observation_text(observation.get("target_display_device_id"))
    if not target_id:
        return False
    return any(_normalized_observation_text(getattr(item, "device_id", None)) == target_id for item in inventory)


def _explicit_observation_device_id_mismatch(
    observation: dict[str, object] | None,
    observation_device_id_in_inventory: bool,
) -> bool:
    return (
        observation is not None
        and "target_display_device_id" in observation
        and not observation_device_id_in_inventory
    )


def _target_display_type_problem(
    observation: dict[str, object] | None,
    configured_backend_id: str,
) -> str | None:
    if observation is None:
        return None
    if "target_display_type" not in observation and "target_display_device_id" not in observation:
        return None
    target_type = _normalized_observation_text(observation.get("target_display_type"))
    if target_type not in _TARGET_DISPLAY_TYPES:
        allowed = ", ".join(sorted(_TARGET_DISPLAY_TYPES))
        return f"target_display_type must be one of: {allowed}"
    allowed_for_backend = _BACKEND_TARGET_DISPLAY_TYPES.get(configured_backend_id)
    if allowed_for_backend is not None and target_type not in allowed_for_backend:
        allowed = ", ".join(sorted(allowed_for_backend))
        return (
            f"target_display_type {target_type} is not compatible with "
            f"{configured_backend_id}; expected one of: {allowed}"
        )
    return None


def _normalized_observation_text(value: object) -> str:
    return str(value or "").strip().lower()


def _target_display_problem(
    backend_id: str,
    target_display_detected: bool,
    target_display_observation_matched: bool,
    observation_device_id_in_inventory: bool,
    observation: dict[str, object] | None,
) -> str | None:
    if backend_id == "desktop_overlay":
        return None
    if observation is not None and "target_display_device_id" not in observation:
        return f"hardware observation target_display_device_id required for {backend_id} acceptance"
    if not target_display_detected:
        return f"target display inventory missing for {backend_id} acceptance"
    if (
        observation is not None
        and "target_display_device_id" in observation
        and not target_display_observation_matched
    ):
        if observation_device_id_in_inventory:
            return "target display observation device_id does not identify a known target display"
        return None
    return None


def _validation_assets_generated(validation_assets: ValidationAssets) -> bool:
    return (
        validation_assets.image_path.is_file()
        and validation_assets.depth_path.is_file()
        and validation_assets.output_path.is_file()
    )


def _validation_asset_problems(validation_assets: ValidationAssets) -> list[str]:
    expected = (
        ("validation source image", validation_assets.image_path),
        ("validation depth map", validation_assets.depth_path),
        ("validation output image", validation_assets.output_path),
    )
    return [
        f"{label} missing: {path.name}"
        for label, path in expected
        if not path.is_file()
    ]


def _finite_float_or_none(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _effective_crosstalk_limit(
    observation: dict[str, object] | None,
    explicit_limit: float | None,
) -> object:
    if explicit_limit is not None:
        return explicit_limit
    if observation is not None and "crosstalk_limit_percent" in observation:
        return observation["crosstalk_limit_percent"]
    return 10.0


def _json_safe(value: object) -> object:
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _crosstalk_limit_problem(crosstalk_limit_percent: object) -> str | None:
    limit = _finite_float_or_none(crosstalk_limit_percent)
    if limit is None or limit < 0.0:
        return "hardware crosstalk limit must be finite and non-negative"
    return None


def _load_hardware_observation(path: str | Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    with Path(path).open(encoding="utf-8") as f:
        if str(path).lower().endswith(".json"):
            data = json.load(f)
        else:
            data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("hardware observation must be a mapping")
    return data


def _copy_hardware_observation(output_dir: Path, path: Path | None) -> Path | None:
    if path is None:
        return None
    suffix = ".json" if path.suffix.lower() == ".json" else ".yaml"
    target = output_dir / f"hardware_observation{suffix}"
    try:
        if path.resolve() == target.resolve():
            return target
    except OSError:
        pass
    shutil.copyfile(path, target)
    return target


def _write_hardware_observation_template(
    output_dir: Path,
    configured_backend_id: str,
    observation: dict[str, object] | None,
    crosstalk_limit_percent: object = 10.0,
) -> Path | None:
    if observation is not None or configured_backend_id == "desktop_overlay":
        return None
    template_path = output_dir / "hardware_observation_template.yaml"
    template = {
        "target_display_device_id": "",
        "target_display_type": _default_target_display_type(configured_backend_id),
        "eye_order_correct": False,
        "depth_direction_correct": False,
        "ui_readable": False,
        "head_tracking_stable": False,
        "crosstalk_percent": 0.0,
        "crosstalk_limit_percent": _finite_float_or_none(crosstalk_limit_percent),
        "notes": "Fill this after viewing validation output on the target display.",
    }
    template_text = yaml.safe_dump(template, sort_keys=False)
    if not isinstance(template_text, str):
        template_text = str(template_text)
    template_path.write_text(template_text, encoding="utf-8")
    return template_path


def _default_target_display_type(configured_backend_id: str) -> str:
    if configured_backend_id == "lightfield_quilt":
        return "lightfield"
    return "autostereo"


def _source_stereo_metadata(source_path: str | None, notes: str | None) -> dict[str, str | None]:
    normalized_path = _normalize_source_stereo_path(source_path)
    return {
        "path": normalized_path,
        "notes": str(notes).strip() if notes is not None and str(notes).strip() else None,
    }


def _normalize_source_stereo_path(source_path: str | None) -> str:
    if source_path is None or not str(source_path).strip():
        return "overlay_depth_reprojection"
    normalized = str(source_path).strip().lower().replace("-", "_")
    if normalized not in _SOURCE_STEREO_PATHS:
        allowed = ", ".join(sorted(_SOURCE_STEREO_PATHS))
        raise ValueError(f"source_stereo_path must be one of: {allowed}")
    return normalized


def _hardware_observation_problems(
    observation: dict[str, object] | None,
    configured_backend_id: str,
    crosstalk_limit_percent: object,
) -> list[str]:
    if observation is None:
        if configured_backend_id != "desktop_overlay":
            return [f"hardware observation required for {configured_backend_id} acceptance"]
        return []
    problems: list[str] = []
    crosstalk_limit = _finite_float_or_none(crosstalk_limit_percent)
    if crosstalk_limit is None:
        crosstalk_limit = float("nan")

    target_display_device_id = str(observation.get("target_display_device_id", "")).strip().lower()
    if "replace_with" in target_display_device_id or "replace-" in target_display_device_id:
        problems.append("hardware observation target_display_device_id is still a placeholder")

    required_fields = (
        "eye_order_correct",
        "depth_direction_correct",
        "ui_readable",
        "head_tracking_stable",
        "crosstalk_percent",
    )
    for field in required_fields:
        if field not in observation:
            problems.append(f"hardware observation missing required field: {field}")
    boolean_fields = {
        "eye_order_correct": "hardware eye order is incorrect",
        "depth_direction_correct": "hardware depth direction is incorrect",
        "ui_readable": "hardware UI readability failed",
        "head_tracking_stable": "hardware head tracking is unstable",
    }
    for field, failure in boolean_fields.items():
        if field not in observation:
            continue
        value = observation[field]
        if not isinstance(value, bool):
            problems.append(f"hardware observation field must be true/false: {field}")
        elif value is False:
            problems.append(failure)
    if "crosstalk_percent" in observation:
        raw_crosstalk = observation["crosstalk_percent"]
        try:
            if not isinstance(raw_crosstalk, (int, float, str)):
                raise TypeError
            crosstalk = float(raw_crosstalk)
        except (TypeError, ValueError):
            problems.append("hardware crosstalk_percent must be numeric")
        else:
            if not math.isfinite(crosstalk):
                problems.append("hardware crosstalk_percent must be finite")
            elif crosstalk < 0.0:
                problems.append("hardware crosstalk_percent must be non-negative")
            elif math.isfinite(crosstalk_limit) and crosstalk > crosstalk_limit:
                problems.append(
                    f"hardware crosstalk {crosstalk:.1f}% exceeds limit {crosstalk_limit:.1f}%"
                )
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a display-backend acceptance report")
    parser.add_argument("output_dir")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    parser.add_argument("--max-parallax-px", type=float, default=8.0)
    parser.add_argument("--hardware-observation", help="YAML or JSON manual hardware observation")
    parser.add_argument("--crosstalk-limit-percent", type=float, default=None)
    parser.add_argument(
        "--source-stereo-path",
        choices=SOURCE_STEREO_PATH_CHOICES,
        help="Upstream stereo/depth source represented by this acceptance artifact",
    )
    parser.add_argument("--source-stereo-notes", help="Operator notes about the upstream stereo source")
    parser.add_argument(
        "--require-live-runtime",
        action="store_true",
        help="Require a fresh overlay runtime summary in diagnostics",
    )
    parser.add_argument(
        "--require-face-tracking",
        action="store_true",
        help="Require a fresh detected face in G3D_State",
    )
    args = parser.parse_args(argv)

    manifest = write_acceptance_report(
        args.output_dir,
        config_path=args.config,
        width=args.width,
        height=args.height,
        max_parallax_px=args.max_parallax_px,
        require_live_runtime=args.require_live_runtime,
        require_face_tracking=args.require_face_tracking,
        hardware_observation_path=args.hardware_observation,
        crosstalk_limit_percent=args.crosstalk_limit_percent,
        source_stereo_path=args.source_stereo_path,
        source_stereo_notes=args.source_stereo_notes,
    )
    print(f"wrote display acceptance report to {manifest.report_path}")
    payload = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    return 0 if payload.get("ready") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
