from launcher import diagnostics


def test_missing_runtime_dll_blocks_diagnostics_readiness(tmp_path, monkeypatch):
    exe = tmp_path / "Glassless3DOverlay.exe"
    model = tmp_path / "models" / "depth_anything_v2_small_fp16.onnx"
    model.parent.mkdir()
    exe.write_bytes(b"exe")
    model.write_bytes(b"model")
    config = tmp_path / "config.yaml"
    config.write_text("camera:\n  index: 0\n", encoding="utf-8")

    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: exe)
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: model)
    monkeypatch.setattr(
        diagnostics,
        "missing_overlay_runtime_assets",
        lambda _exe: ["DirectML.dll"],
    )
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: None)
    monkeypatch.setattr(
        diagnostics,
        "_probe_camera",
        lambda index: diagnostics.CameraProbe(index, True, True),
    )
    monkeypatch.setattr(diagnostics, "_collect_display_inventory", lambda: [])

    report = diagnostics.collect_diagnostics(config)

    assert report.ready is False
    assert "overlay runtime asset missing: DirectML.dll" in report.problems
