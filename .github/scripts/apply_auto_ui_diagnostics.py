from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if new in text:
        return
    if text.count(old) != 1:
        raise RuntimeError(f"{path}: expected one match for {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


replace_once(
    "tracker/shared_settings.py",
    "    uint32  depth_mode         (0=quality, 1=balanced, 2=fast)\n",
    "    uint32  depth_mode         (0=quality, 1=balanced, 2=fast, 3=auto)\n",
)
replace_once(
    "tracker/shared_settings.py",
    "    depth_mode: int = 1\n",
    "    depth_mode: int = 3\n",
)

replace_once(
    "launcher/mainwindow.py",
    '''_DEPTH_MODES = {
    "quality": 0,
    "balanced": 1,
    "fast": 2,
}
''',
    '''_DEPTH_MODES = {
    "quality": 0,
    "balanced": 1,
    "fast": 2,
    "auto": 3,
}
''',
)
replace_once(
    "launcher/mainwindow.py",
    '''_DEPTH_MODE_LABELS = {
    0: "Quality",
    1: "Balanced",
    2: "Fast",
}
''',
    '''_DEPTH_MODE_LABELS = {
    0: "Quality",
    1: "Balanced",
    2: "Fast",
    3: "Auto",
}
''',
)
replace_once(
    "launcher/mainwindow.py",
    '''        for label, code in (("Quality depth", 0), ("Balanced depth", 1), ("Fast depth", 2)):
''',
    '''        for label, code in (("Auto depth", 3), ("Quality depth", 0), ("Balanced depth", 1), ("Fast depth", 2)):
''',
)

replace_once(
    "launcher/wizard.py",
    '    "depth_performance_mode": "balanced",\n',
    '    "depth_performance_mode": "auto",\n',
)
replace_once(
    "config.yaml",
    "  depth_performance_mode: balanced\n",
    "  depth_performance_mode: auto\n",
)

replace_once(
    "launcher/diagnostics.py",
    '''    depth_mode: str | None = None
    stereo_layout: int | None = None
''',
    '''    depth_mode: str | None = None
    active_depth_mode: str | None = None
    depth_model_width: int | None = None
    depth_model_height: int | None = None
    scheduled_tiles: int | None = None
    inference_ms: float | None = None
    blend_ms: float | None = None
    depth_age_ms: int | None = None
    stereo_layout: int | None = None
''',
)
replace_once(
    "launcher/diagnostics.py",
    '''    r"(?:\\s+mode=(?P<depth_mode>[A-Za-z0-9_\\-]+))?\\]\\s+"
''',
    '''    r"(?:\\s+mode=(?P<depth_mode>[A-Za-z0-9_\\-]+))?"
    r"(?:\\s+active=(?P<active_depth_mode>[A-Za-z0-9_\\-]+))?"
    r"(?:\\s+profile=(?P<depth_model_w>\\d+)x(?P<depth_model_h>\\d+))?"
    r"(?:\\s+tiles=(?P<scheduled_tiles>\\d+))?"
    r"(?:\\s+inference_ms=(?P<inference_ms>-?\\d+(?:\\.\\d+)?))?"
    r"(?:\\s+blend_ms=(?P<blend_ms>-?\\d+(?:\\.\\d+)?))?"
    r"(?:\\s+age_ms=(?P<depth_age_ms>\\d+))?\\]\\s+"
''',
)
replace_once(
    "launcher/diagnostics.py",
    '''        depth_mode=match.group("depth_mode"),
        gpu_ms=float(match.group("gpu_ms") or match.group("draw_gpu"))
''',
    '''        depth_mode=match.group("depth_mode"),
        active_depth_mode=match.group("active_depth_mode"),
        depth_model_width=int(match.group("depth_model_w"))
        if match.group("depth_model_w") is not None else None,
        depth_model_height=int(match.group("depth_model_h"))
        if match.group("depth_model_h") is not None else None,
        scheduled_tiles=int(match.group("scheduled_tiles"))
        if match.group("scheduled_tiles") is not None else None,
        inference_ms=float(match.group("inference_ms"))
        if match.group("inference_ms") is not None else None,
        blend_ms=float(match.group("blend_ms"))
        if match.group("blend_ms") is not None else None,
        depth_age_ms=int(match.group("depth_age_ms"))
        if match.group("depth_age_ms") is not None else None,
        gpu_ms=float(match.group("gpu_ms") or match.group("draw_gpu"))
''',
)
replace_once(
    "launcher/diagnostics.py",
    '''        "depth_mode": summary.depth_mode,
        "gpu_ms": summary.gpu_ms,
''',
    '''        "depth_mode": summary.depth_mode,
        "active_depth_mode": summary.active_depth_mode,
        "depth_model_width": summary.depth_model_width,
        "depth_model_height": summary.depth_model_height,
        "scheduled_tiles": summary.scheduled_tiles,
        "inference_ms": summary.inference_ms,
        "blend_ms": summary.blend_ms,
        "depth_age_ms": summary.depth_age_ms,
        "gpu_ms": summary.gpu_ms,
''',
)
replace_once(
    "launcher/diagnostics.py",
    '''                f"- depth_mode: {s.depth_mode or 'unavailable'}",
                f"- gpu_ms: {s.gpu_ms:.2f}" if s.gpu_ms is not None else "- gpu_ms: unavailable",
''',
    '''                f"- depth_mode: {s.depth_mode or 'unavailable'}",
                f"- active_depth_mode: {s.active_depth_mode or 'unavailable'}",
                (
                    f"- depth_profile: {s.depth_model_width}x{s.depth_model_height}"
                    if s.depth_model_width is not None and s.depth_model_height is not None
                    else "- depth_profile: unavailable"
                ),
                f"- scheduled_tiles: {s.scheduled_tiles}" if s.scheduled_tiles is not None else "- scheduled_tiles: unavailable",
                f"- inference_ms: {s.inference_ms:.2f}" if s.inference_ms is not None else "- inference_ms: unavailable",
                f"- blend_ms: {s.blend_ms:.1f}" if s.blend_ms is not None else "- blend_ms: unavailable",
                f"- depth_age_ms: {s.depth_age_ms}" if s.depth_age_ms is not None else "- depth_age_ms: unavailable",
                f"- gpu_ms: {s.gpu_ms:.2f}" if s.gpu_ms is not None else "- gpu_ms: unavailable",
''',
)
replace_once(
    "launcher/diagnostics.py",
    '''        elif overlay_summary.depth_hz <= 0:
            problems.append("overlay log reports no active depth inference")
        if not overlay_summary.has_frame:
''',
    '''        elif overlay_summary.depth_hz <= 0:
            problems.append("overlay log reports no active depth inference")
        if overlay_summary.depth_age_ms is not None and overlay_summary.depth_age_ms > 750:
            message = f"depth result is stale: {overlay_summary.depth_age_ms}ms old"
            if require_live_runtime:
                problems.append(message)
            else:
                warnings.append(message)
        if overlay_summary.inference_ms is not None and overlay_summary.inference_ms > 250.0:
            warnings.append(
                f"depth inference is expensive: {overlay_summary.inference_ms:.1f}ms; auto mode should reduce quality"
            )
        if not overlay_summary.has_frame:
''',
)
