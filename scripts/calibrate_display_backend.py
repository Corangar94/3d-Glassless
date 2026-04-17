#!/usr/bin/env python3
"""Convenience wrapper for writing display backend calibration metadata."""
from __future__ import annotations

from tracker import display_calibration


def main(argv: list[str] | None = None) -> int:
    return display_calibration.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
