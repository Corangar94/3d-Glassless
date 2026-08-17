"""Convenience wrapper for subjective comfort/display evaluation."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import comfort_evaluation


def main(argv=None) -> int:
    return comfort_evaluation.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
