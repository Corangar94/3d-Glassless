#!/usr/bin/env python3
"""Convenience wrapper for stereo/quilt validation-card generation."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import stereo_validation


def main(argv: list[str] | None = None) -> int:
    return stereo_validation.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
