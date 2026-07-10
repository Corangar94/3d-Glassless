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


def test_setup_cli_rejects_target_that_does_not_match_active_profile(monkeypatch, tmp_path):
    setup = _load_setup_module()
    game_dir = tmp_path / "other-game"
    game_dir.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "active_game_profile: story\ngame_profiles:\n  story:\n"
        "    display_name: Story\n"
        f"    executable_path: {tmp_path.as_posix()}/expected-game/Story.exe\n"
        "    play_context: offline_singleplayer\n"
        "    requested_mode: offline_advanced\n"
        "    advanced_acknowledged: true\n",
        encoding="utf-8",
    )
    addon_path = tmp_path / "Glassless3D.addon"
    addon_path.write_bytes(b"addon")
    monkeypatch.setattr(setup, "ADDON_PATH", str(addon_path))
    monkeypatch.setattr(
        setup,
        "install",
        lambda *_args, **_kwargs: pytest.fail("installer must not run for a mismatched target"),
    )

    with pytest.raises(SystemExit, match="does not match active profile"):
        setup.main(
            [
                "--game-dir",
                str(game_dir),
                "--profile-config",
                str(config_path),
            ]
        )
