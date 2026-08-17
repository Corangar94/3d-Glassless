#!/usr/bin/env python3
"""Convenience wrapper for comparing captured depth fixtures to a baseline."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import depth_comparison


def main(argv: list[str] | None = None) -> int:
    return depth_comparison.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
