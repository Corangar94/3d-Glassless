"""Offline renderer for display backend view stacks."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image

from tracker.depth_reprojection import synthesize_views
from tracker.display_backends import build_display_layout


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


def render_backend_grid(
    image: np.ndarray,
    depth: np.ndarray,
    backend_id: str,
    max_parallax_px: float = 8.0,
    confidence_mask: np.ndarray | None = None,
    fill_value: float | int = 0,
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
    args = parser.parse_args(argv)

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
        args.backend,
        max_parallax_px=args.max_parallax_px,
        confidence_mask=confidence,
        fill_value=args.fill_value,
    )
    output = Path(args.output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.asarray(grid, dtype=np.uint8)).save(output)
    print(f"wrote {args.backend} view grid to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
