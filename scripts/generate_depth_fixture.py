#!/usr/bin/env python3
"""Convenience wrapper for generating synthetic depth benchmark fixtures."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import depth_synthetic


def main(argv: list[str] | None = None) -> int:
    return depth_synthetic.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
