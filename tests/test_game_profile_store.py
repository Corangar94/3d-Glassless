import pytest
import yaml

from launcher.game_profile_store import ProfileStoreError, load_profiles, save_profiles
from launcher.game_profiles import GameProfile, PlayContext, RequestedMode


def test_save_profiles_preserves_camera_and_overlay_sections(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "camera:\n  index: 2\noverlay:\n  strength_x: 1.5\n",
        encoding="utf-8",
    )
    profiles = {
        "arena": GameProfile(
            profile_id="arena",
            display_name="Arena",
            executable_path="C:/Games/Arena/Arena.exe",
        )
    }

    save_profiles(config_path, profiles, active_profile_id="arena")

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["camera"]["index"] == 2
    assert saved["overlay"]["strength_x"] == 1.5
    assert saved["active_game_profile"] == "arena"
    assert saved["game_profiles"]["arena"]["requested_mode"] == "non_injecting_desktop"


def test_save_profiles_can_atomically_write_an_updated_base_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("camera:\n  index: 0\n", encoding="utf-8")
    updated_config = {"camera": {"index": 2}, "overlay": {"strength_x": 1.5}}

    save_profiles(
        config_path,
        {},
        active_profile_id=None,
        base_config=updated_config,
    )

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["camera"]["index"] == 2
    assert saved["overlay"]["strength_x"] == 1.5
    assert saved["game_profiles"] == {}


def test_load_profiles_treats_invalid_profile_values_as_non_injecting(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "game_profiles:\n  unsafe:\n    display_name: Unsafe\n    executable_path: C:/Unsafe.exe\n"
        "    play_context: anything\n    requested_mode: reshade\n",
        encoding="utf-8",
    )

    profiles, active_profile_id = load_profiles(config_path)

    assert active_profile_id == "unsafe"
    assert profiles["unsafe"].play_context is PlayContext.ONLINE_MULTIPLAYER
    assert profiles["unsafe"].requested_mode is RequestedMode.NON_INJECTING_DESKTOP


def test_load_profiles_uses_in_memory_fallback_when_config_does_not_exist(tmp_path):
    fallback = {
        "game_profiles": {
            "default": {
                "display_name": "Default profile",
                "executable_path": "",
                "play_context": "online_multiplayer",
                "requested_mode": "non_injecting_desktop",
                "advanced_acknowledged": False,
            }
        },
        "active_game_profile": "default",
    }

    profiles, active_profile_id = load_profiles(tmp_path / "missing.yaml", fallback=fallback)

    assert active_profile_id == "default"
    assert profiles["default"].requested_mode is RequestedMode.NON_INJECTING_DESKTOP


def test_save_profiles_refuses_to_replace_malformed_existing_yaml(tmp_path):
    config_path = tmp_path / "config.yaml"
    original = "game_profiles: [unterminated\n"
    config_path.write_text(original, encoding="utf-8")

    with pytest.raises(ProfileStoreError, match="cannot update malformed"):
        save_profiles(config_path, {}, active_profile_id=None)

    assert config_path.read_text(encoding="utf-8") == original


def test_save_profiles_refuses_to_replace_non_mapping_configuration(tmp_path):
    config_path = tmp_path / "config.yaml"
    original = "- unexpected\n"
    config_path.write_text(original, encoding="utf-8")

    with pytest.raises(ProfileStoreError, match="configuration root must be a mapping"):
        save_profiles(config_path, {}, active_profile_id=None)

    assert config_path.read_text(encoding="utf-8") == original
