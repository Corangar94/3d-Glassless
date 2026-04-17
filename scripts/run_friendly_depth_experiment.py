#!/usr/bin/env python3
"""Convenience wrapper for approved friendly-title depth experiments."""
from __future__ import annotations

from tracker import friendly_depth_experiment


def main(argv: list[str] | None = None) -> int:
    return friendly_depth_experiment.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
