import importlib.util
from pathlib import Path

import pytest


def _load_setup_module():
    setup_path = Path(__file__).resolve().parents[1] / "setup.py"
    spec = importlib.util.spec_from_file_location("glassless3d_setup", setup_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_setup_is_retired_and_never_creates_target(tmp_path):
    setup = _load_setup_module()
    target = tmp_path / "game"
    with pytest.raises(SystemExit, match="legacy setup.py game installer is retired"):
        setup.main(["--game-dir", str(target)])
    assert not target.exists()
