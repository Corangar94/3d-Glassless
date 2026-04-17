"""Depth temporal-stability metrics for overlay evaluation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class DepthStabilityMetrics:
    frame_count: int
    mean_abs_delta: float
    p95_abs_delta: float
    max_abs_delta: float


def compute_depth_stability(
    frames: Sequence[np.ndarray],
    mask: np.ndarray | None = None,
) -> DepthStabilityMetrics:
    """Measure frame-to-frame depth-map instability.

    Inputs are expected to be normalized depth frames in `[0, 1]`, but the
    calculation works for any numeric range. `mask=True` marks pixels to include.
    """
    if not frames:
        return DepthStabilityMetrics(
            frame_count=0,
            mean_abs_delta=0.0,
            p95_abs_delta=0.0,
            max_abs_delta=0.0,
        )

    arrays = [np.asarray(frame, dtype=np.float32) for frame in frames]
    shape = arrays[0].shape
    if any(frame.shape != shape for frame in arrays):
        raise ValueError("all depth frames must have the same shape")

    if mask is not None:
        mask_arr = np.asarray(mask, dtype=bool)
        if mask_arr.shape != shape:
            raise ValueError("mask must have the same shape as depth frames")
    else:
        mask_arr = np.ones(shape, dtype=bool)

    if len(arrays) < 2 or not np.any(mask_arr):
        return DepthStabilityMetrics(
            frame_count=len(arrays),
            mean_abs_delta=0.0,
            p95_abs_delta=0.0,
            max_abs_delta=0.0,
        )

    deltas = []
    for prev, cur in zip(arrays, arrays[1:]):
        deltas.append(np.abs(cur - prev)[mask_arr])
    all_deltas = np.concatenate(deltas)

    return DepthStabilityMetrics(
        frame_count=len(arrays),
        mean_abs_delta=float(np.mean(all_deltas)),
        p95_abs_delta=float(np.percentile(all_deltas, 95)),
        max_abs_delta=float(np.max(all_deltas)),
    )


def classify_depth_stability(metrics: DepthStabilityMetrics) -> str:
    """Classify normalized depth temporal stability as GOOD, WARN, or DANGER."""
    if metrics.frame_count < 2:
        return "WARN"
    if metrics.p95_abs_delta >= 0.15 or metrics.mean_abs_delta >= 0.05:
        return "DANGER"
    if metrics.p95_abs_delta >= 0.05 or metrics.mean_abs_delta >= 0.015:
        return "WARN"
    return "GOOD"
