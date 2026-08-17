#!/usr/bin/env python3
"""Convenience wrapper for writing display backend calibration metadata."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import display_calibration


def main(argv: list[str] | None = None) -> int:
    return display_calibration.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
