#!/usr/bin/env python3
"""Compatibility entry point for the verified ReShade SDK bootstrap step."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.bootstrap import step_reshade_sdk


if __name__ == "__main__":
    raise SystemExit(0 if step_reshade_sdk() else 1)
