# launcher/presets.py
"""Named preset management — stored under `presets:` key in config.yaml."""
from __future__ import annotations

from pathlib import Path
import yaml


def _read(config_path: str) -> dict:
    p = Path(config_path)
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


def _write(config_path: str, cfg: dict) -> None:
    Path(config_path).write_text(yaml.safe_dump(cfg, sort_keys=False))


def list_presets(config_path: str) -> list[str]:
    return list((_read(config_path).get("presets") or {}).keys())


def save_preset(config_path: str, name: str, settings: dict) -> None:
    cfg = _read(config_path)
    cfg.setdefault("presets", {})[name] = settings
    _write(config_path, cfg)


def load_preset(config_path: str, name: str) -> dict:
    presets = (_read(config_path).get("presets") or {})
    if name not in presets:
        raise KeyError(f"Preset '{name}' not found in {config_path}")
    return dict(presets[name])


def delete_preset(config_path: str, name: str) -> None:
    cfg = _read(config_path)
    presets = cfg.get("presets") or {}
    if name not in presets:
        return  # true no-op: nothing on disk changes
    presets.pop(name)
    cfg["presets"] = presets
    _write(config_path, cfg)
