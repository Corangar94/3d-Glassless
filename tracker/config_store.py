"""One read-modify-write transaction for every live YAML configuration writer."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any
import yaml

from tracker.file_transactions import atomic_write, path_lock, reject_link


class ConfigStoreError(ValueError):
    pass


def read_config(path: str | Path, fallback: Mapping | None = None) -> dict[str, Any]:
    path = Path(path)
    reject_link(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = deepcopy(dict(fallback or {}))
    except (yaml.YAMLError, UnicodeError) as error:
        raise ConfigStoreError(f"Cannot update malformed configuration: {path}") from error
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigStoreError("Configuration root must be a mapping")
    return data


def update_config(path: str | Path, mutate: Callable[[dict[str, Any]], object],
                  *, fallback: Mapping | None = None) -> dict[str, Any]:
    path = Path(path).absolute()
    with path_lock(path):
        current = read_config(path, fallback)
        updated = deepcopy(current)
        mutate(updated)
        # Calibration cancellation is rechecked under the file lock immediately
        # before publication, including callers waiting behind another writer.
        from tracker.calibration_cancel import check_cancelled
        check_cancelled()
        # Serialization must succeed before opening any publication temporary.
        data = yaml.safe_dump(updated, sort_keys=False, allow_unicode=True).encode("utf-8")
        if updated != current or not path.exists():
            atomic_write(path, data)
        return updated


def merge_config(path: str | Path, patch: Mapping, *, fallback: Mapping | None = None) -> dict:
    """Merge only fields the caller owns; never replace an old root snapshot."""
    def merge(target, source):
        for key, value in source.items():
            if isinstance(value, Mapping):
                child = target.setdefault(key, {})
                if not isinstance(child, dict):
                    raise ConfigStoreError(f"Configuration section {key!r} must be a mapping")
                merge(child, value)
            else:
                target[key] = deepcopy(value)
    return update_config(path, lambda root: merge(root, patch), fallback=fallback)
