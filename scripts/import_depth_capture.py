#!/usr/bin/env python3
"""Convenience wrapper for importing depth-debug screenshots into .npy frames."""
from __future__ import annotations

from tracker import depth_capture_import


def main(argv: list[str] | None = None) -> int:
    return depth_capture_import.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
