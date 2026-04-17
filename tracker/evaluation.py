"""Tracking-quality metrics for Glassless3D diagnostics.

The deep-research roadmap calls out tracking jitter, loss rate, and
reacquisition time as first-class evaluation signals. This module keeps those
calculations pure so the launcher, debug monitor, and offline scripts can share
the same thresholds.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Sequence


@dataclass(frozen=True)
class PoseSample:
    """One tracker observation in display-relative centimeters."""

    timestamp_ms: int
    x_cm: float = 0.0
    y_cm: float = 0.0
    z_cm: float = 0.0
    valid: bool = True


@dataclass(frozen=True)
class TrackingMetrics:
    """Summary metrics for a contiguous tracking capture."""

    sample_count: int
    valid_count: int
    duration_ms: int
    loss_rate: float
    jitter_cm: float
    max_valid_gap_ms: int
    reacquisition_count: int
    avg_reacquisition_ms: float
    max_reacquisition_ms: int


def compute_tracking_metrics(samples: Sequence[PoseSample]) -> TrackingMetrics:
    """Return tracking diagnostics for ordered pose samples.

    `jitter_cm` is RMS frame-to-frame movement between consecutive valid poses.
    It is intentionally ground-truth-free: it measures pose instability visible
    to the overlay, not absolute tracking accuracy.
    """
    if not samples:
        return TrackingMetrics(
            sample_count=0,
            valid_count=0,
            duration_ms=0,
            loss_rate=1.0,
            jitter_cm=0.0,
            max_valid_gap_ms=0,
            reacquisition_count=0,
            avg_reacquisition_ms=0.0,
            max_reacquisition_ms=0,
        )

    ordered = sorted(samples, key=lambda s: s.timestamp_ms)
    valid = [s for s in ordered if s.valid]
    duration_ms = max(0, ordered[-1].timestamp_ms - ordered[0].timestamp_ms)
    loss_rate = 1.0 - (len(valid) / len(ordered))

    deltas_sq: list[float] = []
    valid_gaps: list[int] = []
    prev_valid: PoseSample | None = None
    for sample in ordered:
        if not sample.valid:
            continue
        if prev_valid is not None:
            dx = sample.x_cm - prev_valid.x_cm
            dy = sample.y_cm - prev_valid.y_cm
            dz = sample.z_cm - prev_valid.z_cm
            deltas_sq.append(dx * dx + dy * dy + dz * dz)
            valid_gaps.append(max(0, sample.timestamp_ms - prev_valid.timestamp_ms))
        prev_valid = sample

    jitter_cm = sqrt(sum(deltas_sq) / len(deltas_sq)) if deltas_sq else 0.0

    reacquisition_times = _reacquisition_times_ms(ordered)
    avg_reacq = (
        sum(reacquisition_times) / len(reacquisition_times)
        if reacquisition_times
        else 0.0
    )

    return TrackingMetrics(
        sample_count=len(ordered),
        valid_count=len(valid),
        duration_ms=duration_ms,
        loss_rate=loss_rate,
        jitter_cm=jitter_cm,
        max_valid_gap_ms=max(valid_gaps, default=0),
        reacquisition_count=len(reacquisition_times),
        avg_reacquisition_ms=avg_reacq,
        max_reacquisition_ms=max(reacquisition_times, default=0),
    )


def classify_tracking_quality(metrics: TrackingMetrics) -> str:
    """Classify tracking quality as GOOD, WARN, or DANGER.

    The thresholds are intentionally conservative for a two-view autostereo
    prototype: visible view-zone instability starts before the tracker is
    unusable, so warnings should appear early.
    """
    if metrics.sample_count == 0 or metrics.valid_count == 0:
        return "DANGER"
    if (
        metrics.loss_rate >= 0.25
        or metrics.jitter_cm >= 2.0
        or metrics.max_reacquisition_ms >= 500
    ):
        return "DANGER"
    if (
        metrics.loss_rate >= 0.02
        or metrics.jitter_cm >= 0.5
        or metrics.max_reacquisition_ms >= 150
    ):
        return "WARN"
    return "GOOD"


def _reacquisition_times_ms(samples: Sequence[PoseSample]) -> list[int]:
    times: list[int] = []
    loss_started_at: int | None = None

    for sample in samples:
        if not sample.valid:
            if loss_started_at is None:
                loss_started_at = sample.timestamp_ms
            continue

        if loss_started_at is not None:
            times.append(max(0, sample.timestamp_ms - loss_started_at))
            loss_started_at = None

    return times
