"""Utilities for writing frame timing captures."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Sequence, TextIO

from launcher.diagnostics import OverlayRuntimeSummary, parse_overlay_summary_line
from tracker.performance_evaluation import FrameTimingSample


class FrameTimingCsvWriter:
    """Write `timestamp_ms,frame_time_ms` rows for performance benchmarks."""

    def __init__(self, path: str | Path, append: bool = False) -> None:
        self._path = Path(path)
        self._append = append
        self._file: TextIO | None = None
        self._writer: csv.DictWriter[str] | None = None

    def __enter__(self) -> "FrameTimingCsvWriter":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not self._append or not self._path.exists() or self._path.stat().st_size == 0
        self._file = open(
            self._path,
            "a" if self._append else "w",
            newline="",
            encoding="utf-8",
        )
        self._writer = csv.DictWriter(self._file, fieldnames=["timestamp_ms", "frame_time_ms"])
        if write_header:
            self._writer.writeheader()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._file is not None:
            self._file.close()
        self._file = None
        self._writer = None

    def write(self, timestamp_ms: int, frame_time_ms: float) -> None:
        if self._writer is None:
            raise RuntimeError("FrameTimingCsvWriter must be used as a context manager")
        self._writer.writerow(
            {
                "timestamp_ms": str(int(timestamp_ms)),
                "frame_time_ms": f"{frame_time_ms:.4f}",
            }
        )


def extract_overlay_frame_timings(
    log_path: str | Path,
    summary_interval_ms: int = 1000,
) -> list[FrameTimingSample]:
    """Estimate average render cadence from consecutive overlay summary lines."""
    summaries = _read_overlay_summaries(log_path)
    samples: list[FrameTimingSample] = []
    for index, (previous, current) in enumerate(zip(summaries, summaries[1:]), start=1):
        frame_delta = current.frame_count - previous.frame_count
        if frame_delta <= 0:
            continue
        samples.append(
            FrameTimingSample(
                timestamp_ms=index * summary_interval_ms,
                frame_time_ms=summary_interval_ms / frame_delta,
            )
        )
    return samples


def export_overlay_frame_timings(
    log_path: str | Path,
    output_csv: str | Path,
    summary_interval_ms: int = 1000,
) -> int:
    samples = extract_overlay_frame_timings(log_path, summary_interval_ms=summary_interval_ms)
    with FrameTimingCsvWriter(output_csv) as writer:
        for sample in samples:
            writer.write(sample.timestamp_ms, sample.frame_time_ms)
    return len(samples)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export overlay.log cadence samples to timing CSV")
    parser.add_argument("log_path", help="overlay.log path")
    parser.add_argument("output_csv", help="CSV output path for performance benchmark")
    parser.add_argument("--summary-interval-ms", type=int, default=1000)
    args = parser.parse_args(argv)

    count = export_overlay_frame_timings(
        args.log_path,
        args.output_csv,
        summary_interval_ms=args.summary_interval_ms,
    )
    print(f"wrote {count} frame timing samples to {args.output_csv}")
    return 0 if count > 0 else 1


def _read_overlay_summaries(log_path: str | Path) -> list[OverlayRuntimeSummary]:
    summaries: list[OverlayRuntimeSummary] = []
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            summary = parse_overlay_summary_line(line)
            if summary is not None:
                summaries.append(summary)
    return summaries


if __name__ == "__main__":
    raise SystemExit(main())
