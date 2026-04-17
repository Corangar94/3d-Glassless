#!/usr/bin/env python3
"""Convenience wrapper for selecting the configured display backend."""
from __future__ import annotations

from launcher import display_backend_config


def main(argv: list[str] | None = None) -> int:
    return display_backend_config.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
