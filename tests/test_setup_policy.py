import importlib.util
from pathlib import Path

import pytest


def _load_setup_module():
    setup_path = Path(__file__).resolve().parents[1] / "setup.py"
    spec = importlib.util.spec_from_file_location("glassless3d_setup", setup_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_cli_requires_an_acknowledged_offline_profile(monkeypatch, tmp_path):
    setup = _load_setup_module()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "active_game_profile: online\ngame_profiles:\n  online:\n"
        "    display_name: Online\n    executable_path: C:/Games/Online.exe\n"
        "    play_context: online_multiplayer\n    requested_mode: non_injecting_desktop\n"
        "    advanced_acknowledged: false\n",
        encoding="utf-8",
    )
    addon_path = tmp_path / "Glassless3D.addon"
    addon_path.write_bytes(b"addon")
    monkeypatch.setattr(setup, "ADDON_PATH", str(addon_path))

    with pytest.raises(SystemExit, match="not permitted"):
        setup.main(
            [
                "--game-dir",
                str(tmp_path / "game"),
                "--profile-config",
                str(config_path),
            ]
        )

    assert not (tmp_path / "game").exists()
