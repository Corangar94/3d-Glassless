#!/usr/bin/env python3
"""Convenience wrapper for display-backend acceptance reports."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import display_acceptance


def main(argv: list[str] | None = None) -> int:
    return display_acceptance.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
