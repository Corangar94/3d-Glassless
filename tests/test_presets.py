# tests/test_presets.py
import pytest
from launcher.presets import PresetConfigError, delete_preset, list_presets, load_preset, save_preset

@pytest.fixture
def tmp_config(tmp_path):
    return str(tmp_path / "config.yaml")

def test_list_empty(tmp_config):
    assert list_presets(tmp_config) == []


def test_list_presets_returns_empty_for_malformed_yaml(tmp_config):
    from pathlib import Path

    Path(tmp_config).write_text("presets: [unterminated\n", encoding="utf-8")

    assert list_presets(tmp_config) == []


def test_save_preset_does_not_overwrite_malformed_yaml(tmp_config):
    from pathlib import Path

    original = "presets: [unterminated\n"
    Path(tmp_config).write_text(original, encoding="utf-8")

    with pytest.raises(PresetConfigError, match="malformed"):
        save_preset(tmp_config, "safe", {"strength_x": 1.0})

    assert Path(tmp_config).read_text(encoding="utf-8") == original


def test_save_and_list(tmp_config):
    save_preset(tmp_config, "wow", {"strength_x": 1.5, "depth_curve": 1})
    assert "wow" in list_presets(tmp_config)

def test_load_roundtrip(tmp_config):
    save_preset(tmp_config, "test", {"strength_x": 2.0, "depth_gamma": 1.5})
    loaded = load_preset(tmp_config, "test")
    assert loaded["strength_x"] == 2.0
    assert loaded["depth_gamma"] == 1.5

def test_load_missing_raises(tmp_config):
    with pytest.raises(KeyError):
        load_preset(tmp_config, "nonexistent")

def test_delete(tmp_config):
    save_preset(tmp_config, "to_delete", {"strength_x": 1.0})
    delete_preset(tmp_config, "to_delete")
    assert "to_delete" not in list_presets(tmp_config)

def test_delete_missing_is_noop(tmp_config):
    from pathlib import Path
    delete_preset(tmp_config, "nonexistent")  # must not raise
    assert not Path(tmp_config).exists()      # must not create the file
