"""Convenience wrapper for depth confidence mask generation."""
from __future__ import annotations

from tracker import depth_confidence


def main(argv=None) -> int:
    return depth_confidence.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
