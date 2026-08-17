#!/usr/bin/env python3
"""Retired legacy game installer.

This filename is intentionally retained as a fail-closed compatibility shim.
It must never mutate a game directory; installation is performed by the
transactional launcher workflow in :mod:`launcher.reshade_install`.
"""
from __future__ import annotations

from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    del argv
    raise SystemExit(
        "ERROR: the legacy setup.py game installer is retired. "
        "Use the Glassless3D launcher's acknowledged offline-only advanced "
        "integration workflow. Online games must use non-injecting capture."
    )


if __name__ == "__main__":
    main()
