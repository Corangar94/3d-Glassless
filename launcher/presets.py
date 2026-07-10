# launcher/presets.py
"""Named preset management — stored under `presets:` key in config.yaml."""
from __future__ import annotations

from pathlib import Path
import yaml


class PresetConfigError(RuntimeError):
    """Raised when preset changes cannot safely preserve config.yaml."""


def _read(config_path: str, *, strict: bool = False) -> dict[str, object]:
    p = Path(config_path)
    if not p.exists():
        return {}
    try:
        loaded = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        if strict:
            raise PresetConfigError("configuration is malformed") from exc
        return {}
    if isinstance(loaded, dict):
        return loaded
    if strict:
        raise PresetConfigError("configuration root must be a mapping")
    return {}


def _write(config_path: str, cfg: dict[str, object]) -> None:
    Path(config_path).write_text(str(yaml.safe_dump(cfg, sort_keys=False)), encoding="utf-8")


def _ensure_mapping_child(data: dict[str, object], key: str) -> dict[str, object]:
    child = data.get(key)
    if isinstance(child, dict):
        return child
    child = {}
    data[key] = child
    return child


def list_presets(config_path: str) -> list[str]:
    presets = _read(config_path).get("presets")
    return list(presets.keys()) if isinstance(presets, dict) else []


def save_preset(config_path: str, name: str, settings: dict) -> None:
    cfg = _read(config_path, strict=True)
    _ensure_mapping_child(cfg, "presets")[name] = settings
    _write(config_path, cfg)


def load_preset(config_path: str, name: str) -> dict:
    presets = _read(config_path).get("presets")
    if not isinstance(presets, dict):
        presets = {}
    if name not in presets:
        raise KeyError(f"Preset '{name}' not found in {config_path}")
    selected = presets[name]
    return dict(selected) if isinstance(selected, dict) else {}


def delete_preset(config_path: str, name: str) -> None:
    cfg = _read(config_path)
    presets = cfg.get("presets")
    if not isinstance(presets, dict):
        return
    if name not in presets:
        return  # true no-op: nothing on disk changes
    presets.pop(name)
    cfg["presets"] = presets
    _write(config_path, cfg)
