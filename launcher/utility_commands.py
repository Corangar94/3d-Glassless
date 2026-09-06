"""Explicit dispatch for utilities in both source and frozen launchers."""
from __future__ import annotations
import argparse
import importlib
import sys

MODULES = {"debug-monitor": "tracker.debug_monitor", "diagnostics": "launcher.diagnostics",
           "support-bundle": "scripts.collect_support"}


def build_utility_command(kind: str, arguments: list[str], *, executable: str | None = None,
                          frozen: bool | None = None) -> list[str]:
    if kind not in MODULES:
        raise ValueError(f"Unknown utility: {kind}")
    exe = executable or sys.executable
    bundled = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    return [exe, *([] if bundled else ["-m", "launcher"]), "--utility-child", kind, *arguments]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Glassless3D utility child")
    parser.add_argument("utility", choices=tuple(MODULES))
    args, remaining = parser.parse_known_args(argv)
    if any(flag in remaining for flag in ("--tracker-child", "--self-test", "--camera-calibration-child", "--utility-child")):
        parser.error("Conflicting child modes")
    module = importlib.import_module(MODULES[args.utility])
    if args.utility == "debug-monitor":
        if remaining:
            parser.error("debug-monitor accepts no additional arguments")
        result = module.main()
    else:
        result = module.main(remaining)
    return int(result or 0)
