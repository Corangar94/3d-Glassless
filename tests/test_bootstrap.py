from scripts import bootstrap


def test_bootstrap_default_runs_overlay_steps_before_experimental_reshade(monkeypatch):
    calls = []

    monkeypatch.setattr(bootstrap, "step_face_model", lambda: calls.append("face") or True)
    monkeypatch.setattr(bootstrap, "step_onnxruntime", lambda: calls.append("onnx") or True)
    monkeypatch.setattr(bootstrap, "step_depth_model", lambda: calls.append("depth") or True)
    monkeypatch.setattr(bootstrap, "step_build_overlay", lambda: calls.append("overlay") or True)
    monkeypatch.setattr(bootstrap, "step_reshade_dll", lambda: calls.append("reshade_dll") or True)
    monkeypatch.setattr(bootstrap, "step_reshade_sdk", lambda: calls.append("reshade_sdk") or True)
    monkeypatch.setattr(bootstrap, "step_build_addon", lambda: calls.append("addon") or True)

    bootstrap.main([])

    assert calls == ["face", "onnx", "depth", "overlay"]


def test_bootstrap_default_labels_reshade_as_skipped(monkeypatch, capsys):
    monkeypatch.setattr(bootstrap, "_build_steps", lambda with_reshade: [])

    bootstrap.main([])

    out = capsys.readouterr().out.lower()
    assert "overlay-first" in out
    assert "experimental reshade backend skipped" in out


def test_bootstrap_with_reshade_runs_experimental_steps_after_overlay(monkeypatch):
    calls = []

    monkeypatch.setattr(bootstrap, "step_face_model", lambda: calls.append("face") or True)
    monkeypatch.setattr(bootstrap, "step_onnxruntime", lambda: calls.append("onnx") or True)
    monkeypatch.setattr(bootstrap, "step_depth_model", lambda: calls.append("depth") or True)
    monkeypatch.setattr(bootstrap, "step_build_overlay", lambda: calls.append("overlay") or True)
    monkeypatch.setattr(bootstrap, "step_reshade_dll", lambda: calls.append("reshade_dll") or True)
    monkeypatch.setattr(bootstrap, "step_reshade_sdk", lambda: calls.append("reshade_sdk") or True)
    monkeypatch.setattr(bootstrap, "step_build_addon", lambda: calls.append("addon") or True)

    bootstrap.main(["--with-reshade"])

    assert calls == [
        "face",
        "onnx",
        "depth",
        "overlay",
        "reshade_dll",
        "reshade_sdk",
        "addon",
    ]
