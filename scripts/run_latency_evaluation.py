"""Convenience wrapper for tracking-to-display latency evaluation."""
from __future__ import annotations

from tracker import latency_evaluation


def main(argv=None) -> int:
    return latency_evaluation.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
