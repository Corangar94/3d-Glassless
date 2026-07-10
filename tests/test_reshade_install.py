import os
import json
import pytest
from unittest.mock import patch

from launcher.game_profiles import GameProfile, PlayContext, RequestedMode, evaluate_profile
from launcher.reshade_install import install_steps, install, InstallError


_ADDON_BUILD_SIZE = 4_155_904  # bytes expected by the experimental backend size guard


def _acknowledged_offline_policy():
    return evaluate_profile(
        GameProfile(
            profile_id="story",
            display_name="Story",
            executable_path="C:/Games/Story/Story.exe",
            play_context=PlayContext.OFFLINE_SINGLEPLAYER,
            requested_mode=RequestedMode.OFFLINE_ADVANCED,
            advanced_acknowledged=True,
        )
    )

def _make_bundle(tmp_path):
    """Create a fake bundle directory with all required assets."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "ReShade64.dll").write_bytes(b"\x00" * _ADDON_BUILD_SIZE)
    shaders = bundle / "shaders"
    shaders.mkdir()
    (shaders / "Glassless3D.fx").write_text("fx")
    (shaders / "Glassless3D.fxh").write_text("fxh")
    (bundle / "Glassless3D.addon").write_bytes(b"fake_addon")
    profiles = bundle / "profiles"
    profiles.mkdir()
    profile_data = {
        "name": "wow",
        "reshade": {"RESHADE_DEPTH_INPUT_IS_REVERSED": 1},
        "shader_defaults": {"ConvergenceDist": 60.0},
    }
    (profiles / "wow.json").write_text(json.dumps(profile_data))
    (profiles / "default.json").write_text(json.dumps(profile_data))
    return str(bundle)


def test_install_steps_copies_reshade_dll(tmp_path):
    bundle = _make_bundle(tmp_path)
    game_dir = str(tmp_path / "game")
    os.makedirs(game_dir)
    with patch("launcher.reshade_install._bundle_dir", return_value=bundle):
        list(install_steps(game_dir, policy=_acknowledged_offline_policy()))
    # The experimental backend copies ReShade64.dll as dxgi.dll (DX12 proxy for WoW)
    assert os.path.exists(os.path.join(game_dir, "dxgi.dll"))


def test_install_steps_copies_shaders(tmp_path):
    bundle = _make_bundle(tmp_path)
    game_dir = str(tmp_path / "game")
    os.makedirs(game_dir)
    with patch("launcher.reshade_install._bundle_dir", return_value=bundle):
        list(install_steps(game_dir, policy=_acknowledged_offline_policy()))
    shader_dir = os.path.join(game_dir, "reshade-shaders", "Shaders")
    assert os.path.exists(os.path.join(shader_dir, "Glassless3D.fx"))
    assert os.path.exists(os.path.join(shader_dir, "Glassless3D.fxh"))


def test_install_steps_writes_reshade_ini(tmp_path):
    bundle = _make_bundle(tmp_path)
    game_dir = str(tmp_path / "game")
    os.makedirs(game_dir)
    with patch("launcher.reshade_install._bundle_dir", return_value=bundle):
        list(install_steps(game_dir, policy=_acknowledged_offline_policy()))
    ini_path = os.path.join(game_dir, "ReShade.ini")
    content = open(ini_path).read()
    assert "[PREPROCESSOR]" in content
    assert "RESHADE_DEPTH_INPUT_IS_REVERSED" in content
    assert "[Glassless3D.fx]" in content
    assert "ConvergenceDist" in content


def test_install_steps_copies_addon(tmp_path):
    bundle = _make_bundle(tmp_path)
    game_dir = str(tmp_path / "game")
    os.makedirs(game_dir)
    with patch("launcher.reshade_install._bundle_dir", return_value=bundle):
        list(install_steps(game_dir, policy=_acknowledged_offline_policy()))
    assert os.path.exists(os.path.join(game_dir, "Glassless3D.addon"))


def test_install_steps_yields_step_names_in_order(tmp_path):
    bundle = _make_bundle(tmp_path)
    game_dir = str(tmp_path / "game")
    os.makedirs(game_dir)
    with patch("launcher.reshade_install._bundle_dir", return_value=bundle):
        steps = list(install_steps(game_dir, policy=_acknowledged_offline_policy()))
    assert steps == [
        "Copying ReShade",
        "Copying shaders",
        "Writing ReShade.ini",
        "Installing addon",
    ]


def test_install_steps_raises_install_error_on_missing_dll(tmp_path):
    bundle = _make_bundle(tmp_path)
    os.remove(os.path.join(bundle, "ReShade64.dll"))
    game_dir = str(tmp_path / "game")
    os.makedirs(game_dir)
    with patch("launcher.reshade_install._bundle_dir", return_value=bundle):
        with pytest.raises(InstallError) as exc_info:
            list(install_steps(game_dir, policy=_acknowledged_offline_policy()))
    assert exc_info.value.step == "Copying ReShade"


def test_install_steps_preserves_existing_reshade_ini_content(tmp_path):
    bundle = _make_bundle(tmp_path)
    game_dir = str(tmp_path / "game")
    os.makedirs(game_dir)
    ini_path = os.path.join(game_dir, "ReShade.ini")
    with open(ini_path, "w") as f:
        f.write("[ExistingSection]\nSomeKey=value\n")
    with patch("launcher.reshade_install._bundle_dir", return_value=bundle):
        list(install_steps(game_dir, policy=_acknowledged_offline_policy()))
    content = open(ini_path).read()
    assert "SomeKey=value" in content
    assert "[PREPROCESSOR]" in content


def test_install_convenience_wrapper(tmp_path):
    bundle = _make_bundle(tmp_path)
    game_dir = str(tmp_path / "game")
    os.makedirs(game_dir)
    with patch("launcher.reshade_install._bundle_dir", return_value=bundle):
        install(game_dir, policy=_acknowledged_offline_policy())  # must not raise
    assert os.path.exists(os.path.join(game_dir, "dxgi.dll"))


def test_reshade_install_rejects_online_profile_before_creating_files(tmp_path):
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    policy = evaluate_profile(
        GameProfile(
            profile_id="online",
            display_name="Online",
            executable_path="C:/Games/Online/Online.exe",
            play_context=PlayContext.ONLINE_MULTIPLAYER,
            requested_mode=RequestedMode.OFFLINE_ADVANCED,
            advanced_acknowledged=True,
        )
    )

    with pytest.raises(InstallError, match="not permitted"):
        list(install_steps(str(game_dir), policy=policy))
    with pytest.raises(InstallError, match="not permitted"):
        install(str(game_dir), policy=policy)

    assert list(game_dir.iterdir()) == []
