#!/usr/bin/env python3
"""Convenience wrapper for generating synthetic depth benchmark fixtures."""
from __future__ import annotations

from tracker import depth_synthetic


def main(argv: list[str] | None = None) -> int:
    return depth_synthetic.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
