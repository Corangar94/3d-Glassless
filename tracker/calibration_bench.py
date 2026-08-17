"""Command-line tracking calibration bench for Glassless3D.

Captures G3D shared-memory poses for a short fixed window and reports the same
tracking-quality metrics used by the debug monitor. This gives us an objective
baseline before judging visual artifacts from a game screenshot.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from math import ceil
from pathlib import Path
from typing import Callable, Sequence

from tracker.debug_monitor import _is_stale
from tracker.evaluation import (
    PoseSample,
    classify_tracking_quality,
    compute_tracking_metrics,
)
from tracker.shared_memory import SharedMemoryReader


def capture_tracking_samples(
    duration_s: float = 10.0,
    interval_s: float = 0.05,
    reader: SharedMemoryReader | None = None,
    monotonic_ms: Callable[[], int] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[PoseSample]:
    """Sample G3D shared memory for duration_s and return pose samples."""
    own_reader = reader is None
    pose_reader = reader or SharedMemoryReader("G3D")
    now_ms_fn = monotonic_ms or (lambda: int(time.monotonic() * 1000))
    samples: list[PoseSample] = []
    sample_count = max(1, ceil(duration_s / max(interval_s, 0.001)))
    try:
        for _ in range(sample_count):
            now_ms = now_ms_fn()
            pose = pose_reader.read()
            if pose is None:
                samples.append(PoseSample(timestamp_ms=now_ms, valid=False))
            else:
                x, y, z, ts = pose
                samples.append(
                    PoseSample(
                        timestamp_ms=now_ms,
                        x_cm=x,
                        y_cm=y,
                        z_cm=z,
                        valid=not _is_stale(ts, now_ms),
                    )
                )
            sleep(interval_s)
    finally:
        if own_reader:
            pose_reader.close()
    return samples


def format_benchmark_json(samples: Sequence[PoseSample]) -> str:
    metrics = compute_tracking_metrics(samples)
    data = {
        "quality": classify_tracking_quality(metrics),
        "metrics": asdict(metrics),
        "samples": [asdict(sample) for sample in samples],
    }
    return json.dumps(data, indent=2, sort_keys=True)


def format_benchmark_text(samples: Sequence[PoseSample]) -> str:
    metrics = compute_tracking_metrics(samples)
    quality = classify_tracking_quality(metrics)
    return "\n".join(
        [
            "Glassless3D Tracking Calibration Bench",
            f"Quality: {quality}",
            f"Samples: {metrics.valid_count}/{metrics.sample_count} valid",
            f"Duration: {metrics.duration_ms} ms",
            f"Loss: {metrics.loss_rate * 100.0:.1f}%",
            f"Jitter: {metrics.jitter_cm:.2f} cm",
            f"Max reacquisition: {metrics.max_reacquisition_ms} ms",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture Glassless3D tracking calibration metrics")
    parser.add_argument("--duration", type=float, default=10.0, help="capture duration in seconds")
    parser.add_argument("--interval", type=float, default=0.05, help="sample interval in seconds")
    parser.add_argument("--output", type=Path, help="optional JSON output path")
    args = parser.parse_args(argv)

    samples = capture_tracking_samples(duration_s=args.duration, interval_s=args.interval)
    text = format_benchmark_text(samples)
    print(text)
    if args.output:
        args.output.write_text(format_benchmark_json(samples) + "\n", encoding="utf-8")
        print(f"wrote tracking calibration bench to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
