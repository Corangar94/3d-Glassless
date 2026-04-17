"""Validated config updates for display backend selection."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import yaml

from tracker.display_backends import DisplayBackend, DisplayBackendRegistry, built_in_backends


def set_display_backend(config_path: str | Path, backend_id: str) -> DisplayBackend:
    registry = DisplayBackendRegistry(built_in_backends())
    backend = _find_backend(registry, backend_id)
    path = Path(config_path)
    cfg = _load_config(path)
    cfg.setdefault("overlay", {})["display_backend"] = backend.id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return backend


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Select Glassless3D display backend in config.yaml")
    parser.add_argument("backend_id")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)

    backend = set_display_backend(args.config, args.backend_id)
    print(f"selected {backend.id} ({backend.label}) in {args.config}")
    return 0


def _find_backend(registry: DisplayBackendRegistry, backend_id: str) -> DisplayBackend:
    for backend in registry.all():
        if backend.id == backend_id:
            return backend
    raise ValueError(f"unknown display backend: {backend_id}")


def _load_config(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("config top-level YAML must be a mapping")
    return data


if __name__ == "__main__":
    raise SystemExit(main())
