#!/usr/bin/env python3
"""Convenience wrapper for running Glassless3D evaluation benchmarks."""
from __future__ import annotations

from tracker import evaluation_suite


def main(argv: list[str] | None = None) -> int:
    return evaluation_suite.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
