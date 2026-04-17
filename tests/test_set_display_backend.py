from scripts import set_display_backend


def test_set_display_backend_script_delegates_to_config_main(monkeypatch):
    calls = []
    monkeypatch.setattr(set_display_backend.display_backend_config, "main", lambda argv: calls.append(argv) or 12)

    code = set_display_backend.main(["stereo_autostereo"])

    assert code == 12
    assert calls == [["stereo_autostereo"]]
