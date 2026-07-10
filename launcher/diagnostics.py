"""Collect overlay-first troubleshooting diagnostics."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import cv2
import yaml

from launcher.overlay_process import _project_root, find_depth_model, find_overlay_exe
from launcher.game_profile_store import ProfileStoreError, load_profiles
from launcher.game_profiles import evaluate_profile
from tracker.display_backends import (
    DisplayBackendRegistry,
    backend_code,
    backend_id_from_code,
    build_display_layout,
    built_in_backends,
)

_DEPTH_HZ_READY_MIN = 3
_OVERLAY_LOG_FRESH_SECONDS = 30.0
_CAPTURE_REASON_GUIDANCE = {
    "target_spans_output": "target window spans multiple displays; move it fully onto one display",
    "no_matching_output": "no attached display output matches the target; reconnect or enable the display",
    "duplicate_unavailable": "desktop capture is unavailable or protected for this output; use a normal local desktop session",
    "device_lost": "the graphics device was reset; wait for the overlay to rebind after the display stabilizes",
    "adapter_changed": "the target moved to another graphics adapter; wait for the overlay to rebuild its renderer",
}


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
    gpu_ms: float | None = None
    backend: int | None = None
    depth_mode: str | None = None
    stereo_layout: int | None = None
    eye_order: int | None = None
    ipd_cm: float | None = None
    focus_plane_cm: float | None = None
    panel_width_px: int | None = None
    panel_height_px: int | None = None
    tracking_mode: int | None = None
    capture_state: str | None = None
    capture_reason: str | None = None


@dataclass(frozen=True)
class CameraProbe:
    index: int
    opened: bool
    frame_ok: bool
    width: int | None = None
    height: int | None = None
    inferred_from_tracker: bool = False


@dataclass(frozen=True)
class DisplayInventoryItem:
    name: str
    device_id: str | None = None
    manufacturer: str | None = None
    product_code: str | None = None
    width_px: int | None = None
    height_px: int | None = None
    primary: bool = False


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
    configured_backend_id: str = "desktop_overlay"
    runtime_backend_id: str | None = None
    configured_backend_layout: dict[str, int] = field(
        default_factory=lambda: {"columns": 1, "rows": 1, "view_count": 1}
    )
    display_calibration: dict[str, object] = field(default_factory=dict)
    camera: CameraProbe | None = None
    display_inventory: list[DisplayInventoryItem] = field(default_factory=list)
    active_profile_id: str | None = None
    requested_profile_mode: str | None = None
    active_profile_mode: str | None = None
    profile_reason: str | None = None


_SUMMARY_RE = re.compile(
    r"Frame#(?P<frame>\d+)\s+"
    r"acq\[ok=(?P<ok>\d+)\s+timeout=(?P<timeout>\d+)\s+lost=(?P<lost>\d+)\s+other=(?P<other>\d+)\]\s+"
    r"shm\[(?P<shm_status>.*?)\s+reads=\d+\s+changes=\d+\s+\((?P<changes_sec>-?\d+)/s\)\s+ts=\d+\]\s+"
    r"depth\[total=(?P<depth_total>\d+)\s+(?P<depth_hz>-?\d+)Hz"
    r"(?:\s+mode=(?P<depth_mode>[A-Za-z0-9_\-]+))?\]\s+"
    r"(?:gpu_ms=(?P<gpu_ms>-?\d+(?:\.\d+)?)\s+)?"
    r"(?:backend=(?P<backend>\d+)\s+)?"
    r"(?:(?:layout=(?P<layout>\d+)\s+)?"
    r"(?:eye_order=(?P<eye_order>\d+)\s+)?"
    r"(?:ipd=(?P<ipd>-?\d+(?:\.\d+)?)\s+)?"
    r"(?:focus=(?P<focus>-?\d+(?:\.\d+)?)\s+)?"
    r"(?:panel=(?P<panel_w>\d+)x(?P<panel_h>\d+)\s+)?"
    r"(?:tracking=(?P<tracking>\d+)\s+)?)?"
    r"head=\([^,]+,[^,]+,(?P<head_z>-?\d+(?:\.\d+)?)\).*?"
    r"hasFrame=(?P<has_frame>[01])"
    r"(?:\s+capture=(?P<capture_state>[a-z_]+)\s+capture_reason=(?P<capture_reason>[a-z0-9_]+))?"
)


def collect_diagnostics(
    config_path: str | Path = "config.yaml",
    require_live_runtime: bool = False,
) -> DiagnosticsReport:
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

    config = _load_config(cfg_path, problems)
    config_loaded = config is not None
    active_profile_id: str | None = None
    requested_profile_mode: str | None = None
    active_profile_mode: str | None = None
    profile_reason: str | None = None
    try:
        profiles, active_profile_id = load_profiles(cfg_path)
    except ProfileStoreError as exc:
        problems.append(f"profile configuration unreadable: {exc}")
    else:
        if active_profile_id is not None:
            active_profile = profiles[active_profile_id]
            decision = evaluate_profile(active_profile)
            requested_profile_mode = active_profile.requested_mode.value
            active_profile_mode = decision.active_mode.value
            profile_reason = decision.reason
    overlay_log = _find_overlay_log(overlay_exe)
    overlay_summary = None
    if overlay_log:
        if _overlay_log_is_fresh(overlay_log):
            overlay_summary = _latest_overlay_summary(overlay_log)
        else:
            warnings.append("overlay log is not fresh; skipping runtime health checks")
    registry = DisplayBackendRegistry(built_in_backends())
    default_backend_id = registry.default().id
    experimental_backend_ids = [backend.id for backend in registry.by_status("experimental")]
    configured_backend_id = _configured_backend_id(config, default_backend_id)
    configured_backend_layout = _backend_layout_dict(configured_backend_id, problems)
    runtime_backend_id = _runtime_backend_id(overlay_summary, warnings)
    display_calibration = _display_calibration(config)
    display_inventory = _collect_display_inventory()
    vendor_managed_tracking = _calibration_tracking_mode(display_calibration) == "vendor_managed"
    camera_index = _configured_camera_index(config)
    camera = (
        CameraProbe(index=camera_index, opened=True, frame_ok=True, inferred_from_tracker=True)
        if _tracker_shm_is_live(overlay_summary) or vendor_managed_tracking
        else _probe_camera(camera_index)
    )
    if not camera.opened:
        if vendor_managed_tracking:
            warnings.append("camera probe could not open while vendor-managed tracking is configured")
        elif _tracker_shm_is_live(overlay_summary):
            warnings.append("camera probe could not open while tracker shared memory is live")
        else:
            problems.append(f"camera {camera.index} could not open")
    elif not camera.frame_ok:
        if vendor_managed_tracking:
            warnings.append("camera probe returned no frames while vendor-managed tracking is configured")
        elif _tracker_shm_is_live(overlay_summary):
            warnings.append("camera probe returned no frames while tracker shared memory is live")
        else:
            problems.append(f"camera {camera.index} opened but returned no frames")
    if overlay_summary is not None:
        if not overlay_summary.shm_status.startswith("LIVE"):
            if vendor_managed_tracking:
                warnings.append("vendor-managed tracking configured; Glassless3D tracker SHM is not required")
            else:
                problems.append("tracker shared memory stale")
        if 0 < overlay_summary.depth_hz < _DEPTH_HZ_READY_MIN:
            problems.append(f"depth inference too slow: {overlay_summary.depth_hz}Hz")
        elif overlay_summary.depth_hz <= 0:
            warnings.append("overlay log reports no active depth inference")
        if not overlay_summary.has_frame:
            warnings.append("overlay log reports no captured frame")
            if require_live_runtime:
                problems.append("overlay runtime has no captured frame")
        if overlay_summary.capture_state == "unavailable":
            guidance = _CAPTURE_REASON_GUIDANCE.get(
                overlay_summary.capture_reason or "",
                "desktop capture is unavailable; check the overlay log for its capture reason",
            )
            problems.append(f"overlay capture unavailable: {guidance}")
        elif overlay_summary.capture_state in {"rebinding", "device_recovery"}:
            warnings.append(
                f"overlay capture is {overlay_summary.capture_state}; waiting for native recovery"
            )
        if runtime_backend_id is not None:
            try:
                configured_code = backend_code(configured_backend_id)
            except ValueError:
                configured_code = None
            if configured_code is not None and overlay_summary.backend != configured_code:
                problems.append(
                    f"runtime backend {runtime_backend_id} does not match configured backend {configured_backend_id}"
                )
        _check_runtime_calibration_matches(display_calibration, overlay_summary, problems)
    elif require_live_runtime:
        problems.append("fresh overlay runtime summary missing")
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
        configured_backend_id=configured_backend_id,
        runtime_backend_id=runtime_backend_id,
        configured_backend_layout=configured_backend_layout,
        display_calibration=display_calibration,
        camera=camera,
        display_inventory=display_inventory,
        active_profile_id=active_profile_id,
        requested_profile_mode=requested_profile_mode,
        active_profile_mode=active_profile_mode,
        profile_reason=profile_reason,
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
        f"Active game profile: {report.active_profile_id or 'none'}",
        f"Requested profile mode: {report.requested_profile_mode or 'none'}",
        f"Active profile mode: {report.active_profile_mode or 'none'}",
        f"Profile policy: {report.profile_reason or 'none'}",
        f"Overlay executable: {report.overlay_exe or 'missing'}",
        f"Depth model: {report.depth_model or 'missing'}",
        f"Camera: {_format_camera(report.camera)}",
        f"Overlay log: {report.overlay_log or 'not found'}",
        f"Default backend: {report.default_backend_id}",
        f"Configured backend: {report.configured_backend_id}",
        f"Runtime backend: {report.runtime_backend_id or 'unavailable'}",
        (
            "Configured layout: "
            f"{report.configured_backend_layout['columns']}x{report.configured_backend_layout['rows']} "
            f"({report.configured_backend_layout['view_count']} views)"
        ),
        f"Display calibration: {report.display_calibration or 'none'}",
        f"Experimental backends: {', '.join(report.experimental_backend_ids) or 'none'}",
        "",
        "Connected displays:",
    ]
    if report.display_inventory:
        lines.extend(_format_display_inventory(item) for item in report.display_inventory)
    else:
        lines.append("- unavailable")
    lines.extend([
        "",
        "Problems:",
    ])
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
                f"- depth_mode: {s.depth_mode or 'unavailable'}",
                f"- gpu_ms: {s.gpu_ms:.2f}" if s.gpu_ms is not None else "- gpu_ms: unavailable",
                f"- backend: {s.backend}" if s.backend is not None else "- backend: unavailable",
                f"- layout: {s.stereo_layout}" if s.stereo_layout is not None else "- layout: unavailable",
                f"- eye_order: {s.eye_order}" if s.eye_order is not None else "- eye_order: unavailable",
                f"- ipd: {s.ipd_cm:.2f} cm" if s.ipd_cm is not None else "- ipd: unavailable",
                f"- focus: {s.focus_plane_cm:.2f} cm" if s.focus_plane_cm is not None else "- focus: unavailable",
                (
                    f"- panel: {s.panel_width_px}x{s.panel_height_px}"
                    if s.panel_width_px is not None and s.panel_height_px is not None
                    else "- panel: unavailable"
                ),
                f"- tracking_mode: {s.tracking_mode}" if s.tracking_mode is not None else "- tracking_mode: unavailable",
                f"- headZ: {s.head_z_cm:.2f} cm",
                f"- hasFrame: {s.has_frame}",
                (
                    f"- capture: {s.capture_state or 'unavailable'} "
                    f"({s.capture_reason or 'not reported'})"
                ),
            ]
        )

    lines.extend(
        [
            "",
            "Useful commands:",
            "- python scripts/bootstrap.py",
            "- python -m tracker.debug_monitor",
            "- python -m tracker.calibration_bench --duration 10 --output tracking_bench.json",
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
        "active_profile_id": report.active_profile_id,
        "requested_profile_mode": report.requested_profile_mode,
        "active_profile_mode": report.active_profile_mode,
        "profile_reason": report.profile_reason,
        "ready": report.ready,
        "problems": report.problems,
        "warnings": report.warnings,
        "default_backend_id": report.default_backend_id,
        "experimental_backend_ids": report.experimental_backend_ids,
        "configured_backend_id": report.configured_backend_id,
        "runtime_backend_id": report.runtime_backend_id,
        "configured_backend_layout": report.configured_backend_layout,
        "display_calibration": report.display_calibration,
        "camera": _camera_to_dict(report.camera),
        "display_inventory": [_display_inventory_to_dict(item) for item in report.display_inventory],
        "overlay_summary": _summary_to_dict(report.overlay_summary),
    }
    return json.dumps(data, indent=2, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print Glassless3D diagnostics")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--output", help="Optional path to write the diagnostics report")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument(
        "--require-live-runtime",
        action="store_true",
        help="Fail unless a fresh overlay log summary is available and healthy",
    )
    args = parser.parse_args(argv)

    report = collect_diagnostics(args.config, require_live_runtime=args.require_live_runtime)
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


def _load_config(path: Path, problems: list[str]) -> dict[str, object] | None:
    if not path.is_file():
        problems.append("config file missing")
        return None
    try:
        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        problems.append(f"config unreadable: {e}")
        return None
    if not isinstance(config, dict):
        problems.append("config unreadable: top-level YAML is not a mapping")
        return None
    return config


def _configured_backend_id(config: dict[str, object] | None, default_backend_id: str) -> str:
    overlay = config.get("overlay", {}) if config is not None else {}
    if not isinstance(overlay, dict):
        return default_backend_id
    value = overlay.get("display_backend", default_backend_id)
    return str(value or default_backend_id)


def _backend_layout_dict(backend_id: str, problems: list[str]) -> dict[str, int]:
    try:
        layout = build_display_layout(backend_id)
    except ValueError as e:
        problems.append(str(e))
        layout = build_display_layout("desktop_overlay")
    return {
        "columns": layout.columns,
        "rows": layout.rows,
        "view_count": layout.view_count,
    }


def _runtime_backend_id(
    overlay_summary: OverlayRuntimeSummary | None,
    warnings: list[str],
) -> str | None:
    if overlay_summary is None or overlay_summary.backend is None:
        return None
    try:
        return backend_id_from_code(overlay_summary.backend)
    except ValueError:
        warnings.append(f"overlay log reports unknown runtime backend code {overlay_summary.backend}")
        return f"unknown:{overlay_summary.backend}"


_STEREO_LAYOUT_CODES = {"full_sbs": 0, "half_sbs": 1}
_EYE_ORDER_CODES = {"left_right": 0, "right_left": 1}
_TRACKING_MODE_CODES = {"glassless3d_managed": 0, "vendor_managed": 1}


def _check_runtime_calibration_matches(
    calibration: dict[str, object],
    summary: OverlayRuntimeSummary,
    problems: list[str],
) -> None:
    _check_enum_runtime_field(
        calibration,
        summary.stereo_layout,
        "stereo_layout",
        _STEREO_LAYOUT_CODES,
        problems,
    )
    _check_enum_runtime_field(
        calibration,
        summary.eye_order,
        "eye_order",
        _EYE_ORDER_CODES,
        problems,
    )
    _check_enum_runtime_field(
        calibration,
        summary.tracking_mode,
        "tracking_mode",
        _TRACKING_MODE_CODES,
        problems,
    )
    _check_int_runtime_field(calibration, summary.panel_width_px, "panel_width_px", problems)
    _check_int_runtime_field(calibration, summary.panel_height_px, "panel_height_px", problems)
    _check_float_runtime_field(calibration, summary.focus_plane_cm, "focus_plane_cm", problems, tolerance=0.05)
    if "ipd_mm" in calibration and summary.ipd_cm is not None:
        expected_mm = _coerce_float(calibration["ipd_mm"])
        if expected_mm is None:
            return
        expected_cm = expected_mm * 0.1
        if abs(summary.ipd_cm - expected_cm) > 0.05:
            problems.append(f"runtime ipd_cm {summary.ipd_cm:.2f} does not match configured {expected_cm:.2f}")


def _check_enum_runtime_field(
    calibration: dict[str, object],
    actual: int | None,
    key: str,
    choices: dict[str, int],
    problems: list[str],
) -> None:
    if key not in calibration or actual is None:
        return
    configured = str(calibration[key])
    expected = choices.get(configured)
    if expected is None:
        return
    if actual != expected:
        problems.append(f"runtime {key} {actual} does not match configured {configured}")


def _check_int_runtime_field(
    calibration: dict[str, object],
    actual: int | None,
    key: str,
    problems: list[str],
) -> None:
    if key not in calibration or actual is None:
        return
    expected = _coerce_int(calibration[key])
    if expected is None:
        return
    if expected > 0 and actual != expected:
        problems.append(f"runtime {key} {actual} does not match configured {expected}")


def _check_float_runtime_field(
    calibration: dict[str, object],
    actual: float | None,
    key: str,
    problems: list[str],
    tolerance: float,
) -> None:
    if key not in calibration or actual is None:
        return
    expected = _coerce_float(calibration[key])
    if expected is None:
        return
    if abs(actual - expected) > tolerance:
        problems.append(f"runtime {key} {actual:.2f} does not match configured {expected:.2f}")


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float, str)):
        return float(value)
    return None


def _coerce_int(value: object) -> int | None:
    if isinstance(value, (int, float, str)):
        return int(value)
    return None


def _display_calibration(config: dict[str, object] | None) -> dict[str, object]:
    overlay = config.get("overlay", {}) if config is not None else {}
    if not isinstance(overlay, dict):
        return {}
    calibration = overlay.get("display_calibration", {})
    if not isinstance(calibration, dict):
        return {}
    result: dict[str, object] = {}
    for key in (
        "viewer_distance_cm",
        "view_cone_deg",
        "panel_width_cm",
        "panel_height_cm",
        "ipd_mm",
        "focus_plane_cm",
    ):
        if key in calibration:
            result[key] = float(calibration[key])
    for key in ("panel_width_px", "panel_height_px"):
        if key in calibration:
            result[key] = int(calibration[key])
    for key in ("stereo_layout", "eye_order", "tracking_mode"):
        if key in calibration:
            result[key] = str(calibration[key])
    return result


def _calibration_tracking_mode(calibration: dict[str, object]) -> str:
    return str(calibration.get("tracking_mode", "glassless3d_managed"))


def _configured_camera_index(config: dict[str, object] | None) -> int:
    camera = config.get("camera", {}) if config is not None else {}
    if not isinstance(camera, dict):
        return 0
    try:
        return int(camera.get("index", 0))
    except (TypeError, ValueError):
        return 0


def _tracker_shm_is_live(summary: OverlayRuntimeSummary | None) -> bool:
    return (
        summary is not None
        and summary.shm_status.startswith("LIVE")
        and summary.shm_changes_per_sec > 0
    )


def _probe_camera(index: int) -> CameraProbe:
    opened_without_frame = False
    for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY):
        cap = cv2.VideoCapture(index, backend)
        try:
            if not cap.isOpened():
                continue
            ok, frame = cap.read()
            if not ok or frame is None:
                opened_without_frame = True
                continue
            height, width = frame.shape[:2]
            return CameraProbe(
                index=index,
                opened=True,
                frame_ok=True,
                width=int(width),
                height=int(height),
            )
        finally:
            cap.release()
    return CameraProbe(index=index, opened=opened_without_frame, frame_ok=False)


def _format_camera(camera: CameraProbe | None) -> str:
    if camera is None:
        return "not checked"
    if camera.inferred_from_tracker:
        return f"{camera.index} (live tracker)"
    if not camera.opened:
        return f"{camera.index} (could not open)"
    if not camera.frame_ok:
        return f"{camera.index} (opened, no frames)"
    return f"{camera.index} ({camera.width}x{camera.height})"


def _collect_display_inventory() -> list[DisplayInventoryItem]:
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Windows.Forms
$screens = @{}
foreach ($screen in [System.Windows.Forms.Screen]::AllScreens) {
  $screens[$screen.DeviceName] = @{
    Primary = [bool]$screen.Primary
    Width = [int]$screen.Bounds.Width
    Height = [int]$screen.Bounds.Height
  }
}
$ids = @{}
foreach ($id in Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorID) {
  $instance = [string]$id.InstanceName
  $base = ($instance -replace '_\d+$', '')
  $ids[$base.ToUpperInvariant()] = @{
    Manufacturer = -join (($id.ManufacturerName | Where-Object { $_ -ne 0 }) | ForEach-Object { [char]$_ })
    Product = -join (($id.ProductCodeID | Where-Object { $_ -ne 0 }) | ForEach-Object { [char]$_ })
  }
}
$items = foreach ($monitor in Get-CimInstance Win32_DesktopMonitor) {
  $deviceId = [string]$monitor.PNPDeviceID
  $id = if ($deviceId) { $ids[$deviceId.ToUpperInvariant()] } else { $null }
  $screen = $null
  foreach ($candidate in $screens.Values) {
    if ($null -eq $screen -and $monitor.ScreenWidth -eq $candidate.Width -and $monitor.ScreenHeight -eq $candidate.Height) {
      $screen = $candidate
    }
  }
  [PSCustomObject]@{
    Name = [string]$monitor.Name
    DeviceId = $deviceId
    Manufacturer = if ($id) { [string]$id.Manufacturer } else { $null }
    ProductCode = if ($id) { [string]$id.Product } else { $null }
    WidthPx = if ($monitor.ScreenWidth) { [int]$monitor.ScreenWidth } elseif ($screen) { [int]$screen.Width } else { $null }
    HeightPx = if ($monitor.ScreenHeight) { [int]$monitor.ScreenHeight } elseif ($screen) { [int]$screen.Height } else { $null }
    Primary = if ($screen) { [bool]$screen.Primary } else { $false }
  }
}
$items | ConvertTo-Json -Depth 4
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    return [_display_inventory_from_dict(item) for item in data if isinstance(item, dict)]


def _display_inventory_from_dict(item: dict[str, object]) -> DisplayInventoryItem:
    return DisplayInventoryItem(
        name=str(item.get("Name") or "Unknown display"),
        device_id=_optional_str(item.get("DeviceId")),
        manufacturer=_optional_str(item.get("Manufacturer")),
        product_code=_optional_str(item.get("ProductCode")),
        width_px=_optional_int(item.get("WidthPx")),
        height_px=_optional_int(item.get("HeightPx")),
        primary=bool(item.get("Primary", False)),
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _format_display_inventory(item: DisplayInventoryItem) -> str:
    size = (
        f"{item.width_px}x{item.height_px}"
        if item.width_px is not None and item.height_px is not None
        else "unknown-size"
    )
    primary = " primary" if item.primary else ""
    manufacturer = f" manufacturer={item.manufacturer}" if item.manufacturer else ""
    product = f" product={item.product_code}" if item.product_code else ""
    return f"- {item.name} {size}{primary}{manufacturer}{product}"


def _display_inventory_to_dict(item: DisplayInventoryItem) -> dict[str, object]:
    return {
        "name": item.name,
        "device_id": item.device_id,
        "manufacturer": item.manufacturer,
        "product_code": item.product_code,
        "width_px": item.width_px,
        "height_px": item.height_px,
        "primary": item.primary,
    }


def _camera_to_dict(camera: CameraProbe | None) -> dict[str, object] | None:
    if camera is None:
        return None
    return {
        "index": camera.index,
        "opened": camera.opened,
        "frame_ok": camera.frame_ok,
        "width": camera.width,
        "height": camera.height,
        "inferred_from_tracker": camera.inferred_from_tracker,
    }


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
        "depth_mode": summary.depth_mode,
        "gpu_ms": summary.gpu_ms,
        "backend": summary.backend,
        "stereo_layout": summary.stereo_layout,
        "eye_order": summary.eye_order,
        "ipd_cm": summary.ipd_cm,
        "focus_plane_cm": summary.focus_plane_cm,
        "panel_width_px": summary.panel_width_px,
        "panel_height_px": summary.panel_height_px,
        "tracking_mode": summary.tracking_mode,
        "head_z_cm": summary.head_z_cm,
        "has_frame": summary.has_frame,
        "capture_state": summary.capture_state,
        "capture_reason": summary.capture_reason,
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
        depth_mode=match.group("depth_mode"),
        gpu_ms=float(match.group("gpu_ms")) if match.group("gpu_ms") is not None else None,
        backend=int(match.group("backend")) if match.group("backend") is not None else None,
        stereo_layout=int(match.group("layout")) if match.group("layout") is not None else None,
        eye_order=int(match.group("eye_order")) if match.group("eye_order") is not None else None,
        ipd_cm=float(match.group("ipd")) if match.group("ipd") is not None else None,
        focus_plane_cm=float(match.group("focus")) if match.group("focus") is not None else None,
        panel_width_px=int(match.group("panel_w")) if match.group("panel_w") is not None else None,
        panel_height_px=int(match.group("panel_h")) if match.group("panel_h") is not None else None,
        tracking_mode=int(match.group("tracking")) if match.group("tracking") is not None else None,
        head_z_cm=float(match.group("head_z")),
        has_frame=match.group("has_frame") == "1",
        capture_state=match.group("capture_state"),
        capture_reason=match.group("capture_reason"),
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


def _overlay_log_is_fresh(path: Path) -> bool:
    try:
        age_s = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age_s <= _OVERLAY_LOG_FRESH_SECONDS


if __name__ == "__main__":
    raise SystemExit(main())
