"""Offline renderer for display backend view stacks."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import numpy as np
import yaml
from PIL import Image

from tracker.depth_reprojection import synthesize_views
from tracker.display_backends import build_display_layout

_STEREO_LAYOUTS = {
    "full_sbs",
    "half_sbs",
    "top_bottom",
    "half_top_bottom",
    "anaglyph",
    "crossview",
    "parallelview",
}
_EYE_ORDERS = {"left_right", "right_left"}

STEREO_LAYOUT_CHOICES = tuple(sorted(_STEREO_LAYOUTS))
EYE_ORDER_CHOICES = tuple(sorted(_EYE_ORDERS))


def render_backend_views(
    image: np.ndarray,
    depth: np.ndarray,
    backend_id: str,
    max_parallax_px: float = 8.0,
    confidence_mask: np.ndarray | None = None,
    fill_value: float | int = 0,
) -> list[np.ndarray]:
    layout = build_display_layout(backend_id)
    return synthesize_views(
        image,
        depth,
        layout.view_offsets,
        max_parallax_px=max_parallax_px,
        confidence_mask=confidence_mask,
        fill_value=fill_value,
    )


def compose_view_grid(views: Sequence[np.ndarray], columns: int, rows: int) -> np.ndarray:
    if len(views) != columns * rows:
        raise ValueError("view count must match columns * rows")
    if not views:
        raise ValueError("at least one view is required")
    arrays = [np.asarray(view) for view in views]
    shape = arrays[0].shape
    if any(view.shape != shape for view in arrays):
        raise ValueError("all views must have the same shape")

    height, width = shape[:2]
    grid_shape = (height * rows, width * columns, *shape[2:])
    grid = np.zeros(grid_shape, dtype=arrays[0].dtype)
    for index, view in enumerate(arrays):
        row = index // columns
        column = index % columns
        grid[
            row * height:(row + 1) * height,
            column * width:(column + 1) * width,
            ...,
        ] = view
    return grid


def compose_stereo_grid(
    views: Sequence[np.ndarray],
    stereo_layout: str = "full_sbs",
    eye_order: str = "left_right",
) -> np.ndarray:
    """Compose two views into a stereo inspection layout."""
    if len(views) != 2:
        raise ValueError("stereo output requires exactly two views")
    layout = _validate_choice("stereo_layout", stereo_layout, _STEREO_LAYOUTS)
    order = _validate_choice("eye_order", eye_order, _EYE_ORDERS)
    left, right = [np.asarray(view) for view in views]
    ordered = [left, right] if order == "left_right" else [right, left]

    if layout in {"full_sbs", "parallelview"}:
        return compose_view_grid(ordered, columns=2, rows=1)
    if layout == "crossview":
        return compose_view_grid([right, left], columns=2, rows=1)
    if layout == "top_bottom":
        return compose_view_grid(ordered, columns=1, rows=2)
    if layout == "half_top_bottom":
        half_height = max(1, ordered[0].shape[0] // 2)
        half_views = [_resize_nearest(view, view.shape[1], half_height) for view in ordered]
        return compose_view_grid(half_views, columns=1, rows=2)
    if layout == "anaglyph":
        return _compose_red_cyan_anaglyph(left, right)

    half_width = max(1, ordered[0].shape[1] // 2)
    half_views = [_resize_nearest(view, half_width, view.shape[0]) for view in ordered]
    return compose_view_grid(half_views, columns=2, rows=1)


def render_backend_grid(
    image: np.ndarray,
    depth: np.ndarray,
    backend_id: str,
    max_parallax_px: float = 8.0,
    confidence_mask: np.ndarray | None = None,
    fill_value: float | int = 0,
    stereo_layout: str = "full_sbs",
    eye_order: str = "left_right",
) -> np.ndarray:
    layout = build_display_layout(backend_id)
    views = render_backend_views(
        image,
        depth,
        backend_id,
        max_parallax_px=max_parallax_px,
        confidence_mask=confidence_mask,
        fill_value=fill_value,
    )
    if backend_id == "stereo_autostereo":
        return compose_stereo_grid(
            views,
            stereo_layout=stereo_layout,
            eye_order=eye_order,
        )
    return compose_view_grid(views, columns=layout.columns, rows=layout.rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render offline stereo/quilt views from image + depth")
    parser.add_argument("image_path", help="RGB image path")
    parser.add_argument("depth_path", help=".npy normalized depth map")
    parser.add_argument("output_path", help="PNG output path")
    parser.add_argument("--backend", default="stereo_autostereo")
    parser.add_argument("--max-parallax-px", type=float, default=8.0)
    parser.add_argument("--confidence-mask", help=".npy boolean confidence mask")
    parser.add_argument("--fill-value", type=float, default=0.0)
    parser.add_argument("--config", type=Path, help="config.yaml with overlay display calibration")
    parser.add_argument("--stereo-layout", choices=STEREO_LAYOUT_CHOICES)
    parser.add_argument("--eye-order", choices=EYE_ORDER_CHOICES)
    args = parser.parse_args(argv)
    config_options = stereo_options_from_config(args.config)
    stereo_layout = args.stereo_layout or config_options.get("stereo_layout", "full_sbs")
    eye_order = args.eye_order or config_options.get("eye_order", "left_right")
    backend_id = str(args.backend)
    if config_options:
        if backend_id == parser.get_default("backend"):
            backend_id = config_options.get("backend_id", backend_id)

    image = np.asarray(Image.open(args.image_path).convert("RGB"))
    depth = np.load(args.depth_path).astype(np.float32, copy=False)
    confidence = (
        np.load(args.confidence_mask).astype(bool, copy=False)
        if args.confidence_mask
        else None
    )
    grid = render_backend_grid(
        image,
        depth,
        backend_id,
        max_parallax_px=args.max_parallax_px,
        confidence_mask=confidence,
        fill_value=args.fill_value,
        stereo_layout=stereo_layout,
        eye_order=eye_order,
    )
    output = Path(args.output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(grid, dtype=np.uint8)).save(output)
    print(f"wrote {backend_id} view grid to {output}")
    return 0


def _validate_choice(name: str, value: str, allowed: set[str]) -> str:
    normalized = str(value).strip().lower()
    if normalized not in allowed:
        expected = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {expected}")
    return normalized


def stereo_options_from_config(config_path: Path | None) -> dict[str, str]:
    if config_path is None:
        return {}
    with Path(config_path).open(encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    config = loaded if isinstance(loaded, dict) else {}
    overlay = config.get("overlay", {})
    if not isinstance(overlay, dict):
        return {}
    calibration = overlay.get("display_calibration", {})
    if not isinstance(calibration, dict):
        calibration = {}
    options: dict[str, str] = {}
    backend_id = overlay.get("display_backend")
    if backend_id:
        options["backend_id"] = str(backend_id)
    if "stereo_layout" in calibration:
        options["stereo_layout"] = _validate_choice(
            "stereo_layout",
            str(calibration["stereo_layout"]),
            _STEREO_LAYOUTS,
        )
    if "eye_order" in calibration:
        options["eye_order"] = _validate_choice(
            "eye_order",
            str(calibration["eye_order"]),
            _EYE_ORDERS,
        )
    return options


def _resize_nearest(image: np.ndarray, width: int, height: int) -> np.ndarray:
    array = np.asarray(image)
    src_h, src_w = array.shape[:2]
    x_idx = np.linspace(0, src_w - 1, width).round().astype(np.int32)
    y_idx = np.linspace(0, src_h - 1, height).round().astype(np.int32)
    return array[y_idx[:, None], x_idx[None, :]].copy()


def _compose_red_cyan_anaglyph(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    if left.shape != right.shape:
        raise ValueError("anaglyph views must have the same shape")
    if left.ndim < 3 or left.shape[2] < 3:
        raise ValueError("anaglyph views must have at least three color channels")
    output = right.copy()
    output[..., 0] = left[..., 0]
    return output


if __name__ == "__main__":
    raise SystemExit(main())
