import hashlib
import json
import struct
from pathlib import Path
from unittest.mock import patch

import pytest

from launcher.game_profiles import GameProfile, PlayContext, RequestedMode, evaluate_profile
from launcher.reshade_install import InstallError, install, install_steps, pe_architecture, uninstall


@pytest.fixture(autouse=True)
def _use_fixture_asset_trust(monkeypatch):
    # Unit bundles are synthetic but valid PE files. Production ReShade hashes
    # are separately pinned in the installer and bootstrap code.
    monkeypatch.setattr("launcher.reshade_install._TRUSTED_RESHADE_HASHES", {})


def _policy(*, online: bool = False):
    return evaluate_profile(
        GameProfile(
            profile_id="story",
            display_name="Story",
            executable_path="C:/Games/Story/Story.exe",
            play_context=(PlayContext.ONLINE_MULTIPLAYER if online else PlayContext.OFFLINE_SINGLEPLAYER),
            requested_mode=RequestedMode.OFFLINE_ADVANCED,
            advanced_acknowledged=True,
        )
    )


def _write_pe(path: Path, arch: str, payload: bytes = b"") -> None:
    machine = {"x86": 0x014C, "x64": 0x8664}[arch]
    data = bytearray(0x100)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, machine)
    path.write_bytes(bytes(data) + payload)


def _make_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    (bundle / "shaders").mkdir(parents=True)
    (bundle / "profiles").mkdir()
    for name, arch in (
        ("ReShade32.dll", "x86"), ("ReShade64.dll", "x64"),
        ("Glassless3D.addon32", "x86"), ("Glassless3D.addon64", "x64"),
    ):
        _write_pe(bundle / name, arch, name.encode())
    for name in ("Glassless3D.fx", "Glassless3D.fxh", "ReShade.fxh"):
        (bundle / "shaders" / name).write_text(name, encoding="utf-8")
    profile = {
        "reshade": {"RESHADE_DEPTH_INPUT_IS_REVERSED": 1},
        "shader_defaults": {"ConvergenceDist": 0.5},
    }
    (bundle / "profiles" / "default.json").write_text(json.dumps(profile), encoding="utf-8")
    assets = {}
    for name, arch in (
        ("ReShade32.dll", "x86"), ("ReShade64.dll", "x64"),
        ("Glassless3D.addon32", "x86"), ("Glassless3D.addon64", "x64"),
    ):
        assets[name] = {
            "arch": arch,
            "sha256": hashlib.sha256((bundle / name).read_bytes()).hexdigest(),
        }
    (bundle / "reshade-assets.json").write_text(
        json.dumps({"reshade_version": "6.7.3", "assets": assets}), encoding="utf-8"
    )
    return bundle


def _make_game(tmp_path: Path, arch: str = "x64") -> tuple[Path, Path]:
    game = tmp_path / f"game-{arch}"
    game.mkdir()
    executable = game / "Story.exe"
    _write_pe(executable, arch)
    return game, executable


def _kwargs(executable: Path, api: str = "d3d11") -> dict:
    return {
        "policy": _policy(),
        "game_executable": str(executable),
        "graphics_api": api,
    }


def test_pe_architecture_reads_real_coff_machine(tmp_path):
    x86 = tmp_path / "x86.exe"
    x64 = tmp_path / "x64.exe"
    _write_pe(x86, "x86")
    _write_pe(x64, "x64")
    assert pe_architecture(x86) == "x86"
    assert pe_architecture(x64) == "x64"


@pytest.mark.parametrize(
    ("arch", "api", "proxy", "addon"),
    [("x64", "d3d11", "dxgi.dll", "Glassless3D.addon64"),
     ("x86", "d3d9", "d3d9.dll", "Glassless3D.addon32")],
)
def test_install_selects_matching_architecture(tmp_path, arch, api, proxy, addon):
    bundle = _make_bundle(tmp_path)
    game, executable = _make_game(tmp_path, arch)
    with patch("launcher.reshade_install._bundle_dir", return_value=str(bundle)):
        steps = list(install_steps(str(game), **_kwargs(executable, api)))
    assert (game / proxy).is_file()
    assert pe_architecture(game / proxy) == arch
    assert (game / addon).is_file()
    assert steps == [
        "Copying verified ReShade assets",
        "Writing section-safe configuration",
        "Recording rollback manifest",
    ]


def test_install_copies_complete_shader_include_set(tmp_path):
    bundle = _make_bundle(tmp_path)
    game, executable = _make_game(tmp_path)
    with patch("launcher.reshade_install._bundle_dir", return_value=str(bundle)):
        install(str(game), **_kwargs(executable))
    shaders = game / "reshade-shaders" / "Shaders"
    assert {p.name for p in shaders.iterdir()} == {"Glassless3D.fx", "Glassless3D.fxh", "ReShade.fxh"}


def test_missing_shader_include_fails_before_writing(tmp_path):
    bundle = _make_bundle(tmp_path)
    (bundle / "shaders" / "ReShade.fxh").unlink()
    game, executable = _make_game(tmp_path)
    with patch("launcher.reshade_install._bundle_dir", return_value=str(bundle)):
        with pytest.raises(InstallError, match="required asset is missing"):
            install(str(game), **_kwargs(executable))
    assert {p.name for p in game.iterdir()} == {"Story.exe"}


def test_checksum_tampering_fails_before_writing(tmp_path):
    bundle = _make_bundle(tmp_path)
    with (bundle / "ReShade64.dll").open("ab") as stream:
        stream.write(b"tampered")
    game, executable = _make_game(tmp_path)
    with patch("launcher.reshade_install._bundle_dir", return_value=str(bundle)):
        with pytest.raises(InstallError, match="SHA-256 mismatch"):
            install(str(game), **_kwargs(executable))
    assert not (game / "dxgi.dll").exists()


def test_pinned_reshade_hash_cannot_be_replaced_by_manifest(tmp_path, monkeypatch):
    bundle = _make_bundle(tmp_path)
    game, executable = _make_game(tmp_path)
    monkeypatch.setattr(
        "launcher.reshade_install._TRUSTED_RESHADE_HASHES",
        {"ReShade64.dll": "0" * 64},
    )
    with patch("launcher.reshade_install._bundle_dir", return_value=str(bundle)):
        with pytest.raises(InstallError, match="untrusted ReShade"):
            install(str(game), **_kwargs(executable))


def test_online_policy_rejected_before_target_or_bundle_access(tmp_path):
    game = tmp_path / "game"
    game.mkdir()
    with pytest.raises(InstallError, match="only for acknowledged offline"):
        list(install_steps(str(game), policy=_policy(online=True), game_executable="missing.exe", graphics_api="d3d11"))
    assert list(game.iterdir()) == []


@pytest.mark.parametrize("api", ["opengl", "vulkan", "unknown"])
def test_unsupported_graphics_api_fails_closed(tmp_path, api):
    game, executable = _make_game(tmp_path)
    with pytest.raises(InstallError, match="graphics API|not valid"):
        list(install_steps(str(game), **_kwargs(executable, api)))


def test_proxy_must_match_graphics_api(tmp_path):
    game, executable = _make_game(tmp_path)
    with pytest.raises(InstallError, match="not valid"):
        list(install_steps(str(game), **_kwargs(executable, "d3d9"), proxy_name="dxgi.dll"))


def test_preflight_refuses_unowned_proxy(tmp_path):
    bundle = _make_bundle(tmp_path)
    game, executable = _make_game(tmp_path)
    (game / "dxgi.dll").write_bytes(b"user mod")
    with patch("launcher.reshade_install._bundle_dir", return_value=str(bundle)):
        with pytest.raises(InstallError, match="unowned files"):
            install(str(game), **_kwargs(executable))
    assert (game / "dxgi.dll").read_bytes() == b"user mod"


def test_ini_update_preserves_other_sections_and_keys(tmp_path):
    bundle = _make_bundle(tmp_path)
    game, executable = _make_game(tmp_path)
    original = "[ExistingSection]\nSomeKey=value\n[INPUT]\nOtherInput=1\n"
    (game / "ReShade.ini").write_text(original, encoding="utf-8")
    with patch("launcher.reshade_install._bundle_dir", return_value=str(bundle)):
        install(str(game), **_kwargs(executable))
    text = (game / "ReShade.ini").read_text(encoding="utf-8")
    assert "[ExistingSection]\nSomeKey=value" in text
    assert "[INPUT]\nOtherInput=1\nKeyOverlay=36,0,0,0" in text
    assert text.count("[INPUT]") == 1
    assert "[GENERAL]" in text and "[PREPROCESSOR]" in text


def test_uninstall_restores_configuration_and_removes_created_files(tmp_path):
    bundle = _make_bundle(tmp_path)
    game, executable = _make_game(tmp_path)
    original = b"[GENERAL]\nUserSetting=keep\n"
    (game / "ReShade.ini").write_bytes(original)
    with patch("launcher.reshade_install._bundle_dir", return_value=str(bundle)):
        install(str(game), **_kwargs(executable))
    uninstall(str(game))
    assert (game / "ReShade.ini").read_bytes() == original
    assert not (game / "dxgi.dll").exists()
    assert not (game / "Glassless3D.addon64").exists()
    assert not (game / ".glassless3d-reshade.json").exists()


def test_repair_requires_explicit_flag_and_keeps_original_backup(tmp_path):
    bundle = _make_bundle(tmp_path)
    game, executable = _make_game(tmp_path)
    original = b"[GENERAL]\nOriginal=1\n"
    (game / "ReShade.ini").write_bytes(original)
    with patch("launcher.reshade_install._bundle_dir", return_value=str(bundle)):
        install(str(game), **_kwargs(executable))
        with pytest.raises(InstallError, match="repair=True"):
            install(str(game), **_kwargs(executable))
        install(str(game), **_kwargs(executable), repair=True)
    uninstall(str(game))
    assert (game / "ReShade.ini").read_bytes() == original


def test_transaction_rolls_back_when_configuration_fails(tmp_path):
    bundle = _make_bundle(tmp_path)
    game, executable = _make_game(tmp_path)
    with patch("launcher.reshade_install._bundle_dir", return_value=str(bundle)), patch(
        "launcher.reshade_install._write_configuration", side_effect=OSError("disk full")
    ):
        with pytest.raises(InstallError, match="disk full"):
            install(str(game), **_kwargs(executable))
    assert {p.name for p in game.iterdir()} == {"Story.exe"}


def test_addon_registers_lifecycle_and_neutralizes_stale_tracking():
    source = Path("addon/Glassless3D.cpp").read_text(encoding="utf-8")
    register = source.index("reshade::register_addon(module)")
    event = source.index("reshade::register_event")
    assert register < event
    assert "reshade::unregister_addon(module)" in source
    assert "now - s_lastChangeMs > 500" in source
    assert "!std::isfinite(second.X)" in source


def test_shader_uses_unshifted_pixel_for_out_of_bounds_samples():
    source = Path("shaders/Glassless3D.fx").read_text(encoding="utf-8")
    assert "sampleUV  = uv + offset" in source
    assert "sampleUV = uv;" in source
    assert "saturate(uv + offset)" not in source


def test_mingw_build_is_statically_linked_and_arch_named():
    source = Path("addon/CMakeLists.txt").read_text(encoding="utf-8")
    assert '".addon64"' in source and '".addon32"' in source
    assert "-static-libgcc" in source
    assert "-static-libstdc++" in source
    assert "-static" in source
