import json
import numpy as np

from launcher import support_bundle


def test_create_support_bundle_writes_diagnostics_and_manifest(tmp_path):
    out_dir = tmp_path / "bundle"

    manifest = support_bundle.create_support_bundle(output_dir=out_dir)

    assert (out_dir / "diagnostics.json").exists()
    assert (out_dir / "manifest.json").exists()
    manifest_data = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_data["diagnostics"] == "diagnostics.json"
    assert manifest.output_dir == out_dir


def test_create_support_bundle_includes_evaluation_when_inputs_provided(tmp_path):
    depth_dir = tmp_path / "depth"
    depth_dir.mkdir()
    np.save(depth_dir / "a.npy", np.zeros((2, 2), dtype=np.float32))
    np.save(depth_dir / "b.npy", np.full((2, 2), 0.01, dtype=np.float32))
    out_dir = tmp_path / "bundle"

    manifest = support_bundle.create_support_bundle(
        output_dir=out_dir,
        depth_dir=depth_dir,
    )

    assert (out_dir / "evaluation.json").exists()
    manifest_data = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_data["evaluation"] == "evaluation.json"
    assert manifest.evaluation_path == out_dir / "evaluation.json"


def test_main_writes_bundle(tmp_path, capsys):
    out_dir = tmp_path / "bundle"

    code = support_bundle.main(["--output-dir", str(out_dir)])

    assert code == 0
    assert (out_dir / "manifest.json").exists()
    assert "wrote support bundle" in capsys.readouterr().out
