"""Convenience wrapper for display-zone/crosstalk evaluation."""
from __future__ import annotations

from tracker import display_quality


def main(argv=None) -> int:
    return display_quality.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
