import sys

import pytest

from scripts import export_vda_onnx


def test_replace_active_backs_up_existing_model_and_copies_output(tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    output = models / "video_depth_anything_vits_518.onnx"
    active = models / "depth_anything_v2_small_fp16.onnx"
    backup = models / "depth_anything_v2_small_fp16.onnx.bak"
    output.write_bytes(b"vda")
    active.write_bytes(b"dav2")

    monkeypatch.setattr(export_vda_onnx, "ROOT", tmp_path)
    monkeypatch.setattr(export_vda_onnx, "OUTPUT", output)
    monkeypatch.setattr(export_vda_onnx, "ACTIVE", active)
    monkeypatch.setattr(export_vda_onnx, "ACTIVE_BAK", backup)

    export_vda_onnx.replace_active()

    assert backup.read_bytes() == b"dav2"
    assert active.read_bytes() == b"vda"


def test_main_install_exports_verifies_and_replaces(tmp_path, monkeypatch):
    calls: list[str] = []
    repo = tmp_path / "vendor" / "video-depth-anything"
    weights = tmp_path / "models" / "video_depth_anything_vits.pth"
    output = tmp_path / "models" / "video_depth_anything_vits_518.onnx"

    monkeypatch.setattr(export_vda_onnx, "ROOT", tmp_path)
    monkeypatch.setattr(export_vda_onnx, "VDA_REPO", repo)
    monkeypatch.setattr(export_vda_onnx, "WEIGHTS", weights)
    monkeypatch.setattr(export_vda_onnx, "OUTPUT", output)
    monkeypatch.setattr(export_vda_onnx, "_require_torch", lambda: calls.append("torch"))
    monkeypatch.setattr(export_vda_onnx, "_clone_vda", lambda: calls.append("clone"))
    monkeypatch.setattr(export_vda_onnx, "_download", lambda *_args: calls.append("download"))
    monkeypatch.setattr(export_vda_onnx, "verify", lambda: calls.append("verify"))
    monkeypatch.setattr(export_vda_onnx, "replace_active", lambda: calls.append("replace"))

    def fake_export() -> None:
        calls.append("export")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"onnx")

    monkeypatch.setattr(export_vda_onnx, "export", fake_export)
    monkeypatch.setattr(sys, "argv", ["export_vda_onnx.py", "--install", "--replace"])

    export_vda_onnx.main()

    assert calls == ["torch", "clone", "download", "export", "verify", "replace"]


def test_main_without_install_requires_existing_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(export_vda_onnx, "ROOT", tmp_path)
    monkeypatch.setattr(export_vda_onnx, "VDA_REPO", tmp_path / "missing-repo")
    monkeypatch.setattr(export_vda_onnx, "WEIGHTS", tmp_path / "models" / "missing.pth")
    monkeypatch.setattr(export_vda_onnx, "_require_torch", lambda: None)
    monkeypatch.setattr(sys, "argv", ["export_vda_onnx.py"])

    with pytest.raises(SystemExit) as exc:
        export_vda_onnx.main()

    assert exc.value.code == 1
