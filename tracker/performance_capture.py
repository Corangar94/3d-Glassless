"""Utilities for writing frame timing captures."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import TextIO


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
