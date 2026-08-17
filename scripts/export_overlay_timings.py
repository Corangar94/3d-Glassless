#!/usr/bin/env python3
"""Convenience wrapper for exporting overlay.log cadence to timing CSV."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import performance_capture


def main(argv: list[str] | None = None) -> int:
    return performance_capture.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
