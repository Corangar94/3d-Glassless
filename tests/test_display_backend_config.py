import yaml

from launcher import display_backend_config


def test_set_display_backend_updates_overlay_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("overlay:\n  strength_x: 1.5\n", encoding="utf-8")

    backend = display_backend_config.set_display_backend(path, "stereo_autostereo")

    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert backend.id == "stereo_autostereo"
    assert cfg["overlay"]["display_backend"] == "stereo_autostereo"
    assert cfg["overlay"]["strength_x"] == 1.5


def test_set_display_backend_rejects_unknown_backend(tmp_path):
    path = tmp_path / "config.yaml"

    try:
        display_backend_config.set_display_backend(path, "mystery")
    except ValueError as e:
        assert "unknown display backend" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_main_writes_backend_selection(tmp_path, capsys):
    path = tmp_path / "config.yaml"

    code = display_backend_config.main([
        "--config",
        str(path),
        "lightfield_quilt",
    ])

    assert code == 0
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["overlay"]["display_backend"] == "lightfield_quilt"
    assert "selected lightfield_quilt" in capsys.readouterr().out
