# tests/test_presets.py
import pytest
from launcher.presets import list_presets, save_preset, load_preset, delete_preset

@pytest.fixture
def tmp_config(tmp_path):
    return str(tmp_path / "config.yaml")

def test_list_empty(tmp_config):
    assert list_presets(tmp_config) == []

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
    delete_preset(tmp_config, "nonexistent")  # must not raise
