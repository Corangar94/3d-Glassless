"""Generate confidence masks for depth reprojection experiments."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import numpy as np


def compute_spatial_confidence(
    depth: np.ndarray,
    min_depth: float = 0.0,
    max_depth: float = 1.0,
    max_gradient: float | None = None,
) -> np.ndarray:
    """Return pixels with finite, in-range depth and optional smooth gradients."""
    depth_array = _depth_2d(depth)
    mask = (
        np.isfinite(depth_array)
        & (depth_array >= min_depth)
        & (depth_array <= max_depth)
    )
    if max_gradient is not None:
        if max_gradient < 0:
            raise ValueError("max_gradient must be non-negative")
        safe_depth = np.where(np.isfinite(depth_array), depth_array, 0.0)
        high_gradient = np.zeros_like(safe_depth, dtype=bool)
        if safe_depth.shape[1] > 1:
            horizontal = np.abs(np.diff(safe_depth, axis=1)) > max_gradient
            high_gradient[:, :-1] |= horizontal
            high_gradient[:, 1:] |= horizontal
        if safe_depth.shape[0] > 1:
            vertical = np.abs(np.diff(safe_depth, axis=0)) > max_gradient
            high_gradient[:-1, :] |= vertical
            high_gradient[1:, :] |= vertical
        mask &= ~high_gradient
    return mask


def compute_temporal_confidence(
    previous_depth: np.ndarray,
    current_depth: np.ndarray,
    max_delta: float = 0.05,
) -> np.ndarray:
    """Return pixels whose normalized depth did not jump between frames."""
    if max_delta < 0:
        raise ValueError("max_delta must be non-negative")
    previous = _depth_2d(previous_depth)
    current = _depth_2d(current_depth)
    if previous.shape != current.shape:
        raise ValueError("depth frames must have the same shape")
    return np.isfinite(previous) & np.isfinite(current) & (np.abs(current - previous) <= max_delta)


def combine_confidence_masks(*masks: np.ndarray) -> np.ndarray:
    if not masks:
        raise ValueError("at least one confidence mask is required")
    arrays = [np.asarray(mask, dtype=bool) for mask in masks]
    shape = arrays[0].shape
    if any(mask.shape != shape for mask in arrays):
        raise ValueError("confidence masks must have the same shape")
    combined = arrays[0].copy()
    for mask in arrays[1:]:
        combined &= mask
    return combined


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a boolean depth confidence mask")
    parser.add_argument("depth_path", help=".npy current normalized depth map")
    parser.add_argument("output_path", help=".npy boolean confidence mask output")
    parser.add_argument("--previous-depth", help="Optional previous .npy depth map for temporal confidence")
    parser.add_argument("--min-depth", type=float, default=0.0)
    parser.add_argument("--max-depth", type=float, default=1.0)
    parser.add_argument("--max-gradient", type=float)
    parser.add_argument("--max-delta", type=float, default=0.05)
    args = parser.parse_args(argv)

    current = np.load(args.depth_path).astype(np.float32, copy=False)
    masks = [
        compute_spatial_confidence(
            current,
            min_depth=args.min_depth,
            max_depth=args.max_depth,
            max_gradient=args.max_gradient,
        )
    ]
    if args.previous_depth:
        previous = np.load(args.previous_depth).astype(np.float32, copy=False)
        masks.append(compute_temporal_confidence(previous, current, max_delta=args.max_delta))
    confidence = combine_confidence_masks(*masks)

    output = Path(args.output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    np.save(output, confidence)
    print(f"wrote depth confidence mask to {output}")
    return 0


def _depth_2d(depth: np.ndarray) -> np.ndarray:
    depth_array = np.asarray(depth, dtype=np.float32)
    if depth_array.ndim != 2:
        raise ValueError("depth must be a 2D array")
    return depth_array


if __name__ == "__main__":
    raise SystemExit(main())
