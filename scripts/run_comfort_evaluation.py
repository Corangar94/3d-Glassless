"""Convenience wrapper for subjective comfort/display evaluation."""
from __future__ import annotations

from tracker import comfort_evaluation


def main(argv=None) -> int:
    return comfort_evaluation.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
