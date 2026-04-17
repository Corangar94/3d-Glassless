#!/usr/bin/env python3
"""Convenience wrapper for rendering offline stereo/quilt view grids."""
from __future__ import annotations

from tracker import view_renderer


def main(argv: list[str] | None = None) -> int:
    return view_renderer.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
