"""Validate a complete preset before touching widgets or live settings."""
from __future__ import annotations
from dataclasses import replace
from collections.abc import Mapping

from tracker.shared_settings import OverlaySettings, _pack_settings
from tracker.shared_settings_validation import finite_float, enum_uint32

EDITABLE_FLOATS = (
    "strength_x", "strength_y", "virtual_depth_cm", "focus_radius", "smoothing_alpha",
    "deadzone_mm", "depth_gamma", "ipd_mm", "screen_w_cm", "screen_h_cm", "head_dist_cm",
    "camera_fov_deg",
)


def validated_preset(data: object, current: OverlaySettings) -> OverlaySettings:
    if not isinstance(data, Mapping):
        raise ValueError("Preset must be a mapping")
    defaults = OverlaySettings()
    values = {name: finite_float(data.get(name, getattr(defaults, name)), name)
              for name in EDITABLE_FLOATS}
    values["depth_curve"] = enum_uint32(data.get("depth_curve", 1), "depth_curve", (0, 1, 2))
    mode = data.get("depth_performance_mode", data.get("depth_mode", 1))
    if isinstance(mode, str):
        modes = {"quality": 0, "balanced": 1, "fast": 2, "auto": 3}
        if mode not in modes:
            raise ValueError("Unknown depth performance mode")
        mode = modes[mode]
    values["depth_mode"] = enum_uint32(mode, "depth_mode", (0, 1, 2, 3))
    candidate = replace(current, **values)
    _pack_settings(candidate, 0)  # Includes binary representation/enum validation.
    return candidate
