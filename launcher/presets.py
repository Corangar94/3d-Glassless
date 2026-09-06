# launcher/presets.py
"""Named preset management — stored under `presets:` key in config.yaml."""
from __future__ import annotations
from tracker.config_store import ConfigStoreError, read_config, update_config, merge_config

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
    # Compatibility helper: this module only owns the presets section.
    update_config(config_path, lambda root: root.__setitem__("presets", cfg.get("presets", {})))


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
    def mutate(root):
        presets = root.setdefault("presets", {})
        if not isinstance(presets, dict):
            raise PresetConfigError("presets must be a mapping")
        presets[name] = settings
    try:
        update_config(config_path, mutate)
    except ConfigStoreError as error:
        raise PresetConfigError(str(error)) from error


def load_preset(config_path: str, name: str) -> dict:
    presets = _read(config_path).get("presets")
    if not isinstance(presets, dict):
        presets = {}
    if name not in presets:
        raise KeyError(f"Preset '{name}' not found in {config_path}")
    selected = presets[name]
    return dict(selected) if isinstance(selected, dict) else {}


def delete_preset(config_path: str, name: str) -> None:
    def mutate(root):
        presets = root.get("presets")
        if isinstance(presets, dict):
            presets.pop(name, None)
    try:
        if Path(config_path).exists():
            update_config(config_path, mutate)
    except ConfigStoreError as error:
        raise PresetConfigError(str(error)) from error
