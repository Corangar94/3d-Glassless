"""Frame pacing metrics for overlay performance diagnostics."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class FrameTimingSample:
    timestamp_ms: int
    frame_time_ms: float


@dataclass(frozen=True)
class FrameTimingMetrics:
    sample_count: int
    frame_budget_ms: float
    avg_frame_time_ms: float
    p95_frame_time_ms: float
    max_frame_time_ms: float
    avg_fps: float
    over_budget_count: int
    over_budget_rate: float


def compute_frame_timing_metrics(
    samples: Sequence[FrameTimingSample],
    target_fps: float = 60.0,
) -> FrameTimingMetrics:
    if target_fps <= 0:
        raise ValueError("target_fps must be positive")

    budget_ms = 1000.0 / target_fps
    if not samples:
        return FrameTimingMetrics(
            sample_count=0,
            frame_budget_ms=budget_ms,
            avg_frame_time_ms=0.0,
            p95_frame_time_ms=0.0,
            max_frame_time_ms=0.0,
            avg_fps=0.0,
            over_budget_count=0,
            over_budget_rate=1.0,
        )

    values = np.asarray([s.frame_time_ms for s in samples], dtype=np.float32)
    over_budget_count = int(np.count_nonzero(values > budget_ms))
    avg_ms = float(np.mean(values))

    return FrameTimingMetrics(
        sample_count=len(samples),
        frame_budget_ms=budget_ms,
        avg_frame_time_ms=avg_ms,
        p95_frame_time_ms=float(np.percentile(values, 95)),
        max_frame_time_ms=float(np.max(values)),
        avg_fps=1000.0 / avg_ms if avg_ms > 0 else 0.0,
        over_budget_count=over_budget_count,
        over_budget_rate=over_budget_count / len(samples),
    )


def classify_frame_pacing(metrics: FrameTimingMetrics) -> str:
    if metrics.sample_count == 0:
        return "DANGER"
    if metrics.p95_frame_time_ms >= metrics.frame_budget_ms * 2.0:
        return "DANGER"
    if metrics.p95_frame_time_ms >= metrics.frame_budget_ms * 1.2:
        return "WARN"
    if metrics.over_budget_rate >= 0.10:
        return "WARN"
    return "GOOD"
