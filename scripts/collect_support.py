#!/usr/bin/env python3
"""Convenience wrapper for creating Glassless3D support bundles."""
from __future__ import annotations

from launcher import support_bundle


def main(argv: list[str] | None = None) -> int:
    return support_bundle.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
