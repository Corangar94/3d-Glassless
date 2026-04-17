"""CPU reference depth-image reprojection for synthetic display views."""
from __future__ import annotations

from typing import Sequence

import numpy as np


def synthesize_views(
    image: np.ndarray,
    depth: np.ndarray,
    view_offsets: Sequence[float],
    max_parallax_px: float = 8.0,
) -> list[np.ndarray]:
    """Return horizontally reprojected views from an image and normalized depth map."""
    image_array = np.asarray(image)
    depth_array = np.asarray(depth, dtype=np.float32)
    if image_array.ndim < 2:
        raise ValueError("image must have at least height and width dimensions")
    if depth_array.shape != image_array.shape[:2]:
        raise ValueError("depth shape must match image height and width")

    height, width = depth_array.shape
    x_coords = np.arange(width, dtype=np.float32)[None, :]
    views: list[np.ndarray] = []
    for offset in view_offsets:
        shift = (depth_array - 0.5) * 2.0 * max_parallax_px * float(offset)
        sample_x = np.rint(x_coords - shift).astype(np.int32)
        sample_x = np.clip(sample_x, 0, width - 1)
        y_coords = np.arange(height)[:, None]
        views.append(image_array[y_coords, sample_x].copy())
    return views
