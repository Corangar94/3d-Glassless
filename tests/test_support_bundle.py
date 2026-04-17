import json
import numpy as np

from launcher import support_bundle
from launcher.diagnostics import DiagnosticsReport


def test_create_support_bundle_writes_diagnostics_and_manifest(tmp_path):
    out_dir = tmp_path / "bundle"

    manifest = support_bundle.create_support_bundle(output_dir=out_dir)

    assert (out_dir / "diagnostics.json").exists()
    assert (out_dir / "feasibility_wow.json").exists()
    assert (out_dir / "manifest.json").exists()
    manifest_data = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_data["diagnostics"] == "diagnostics.json"
    assert manifest_data["feasibility_wow"] == "feasibility_wow.json"
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


def test_create_support_bundle_includes_comfort_evaluation_input(tmp_path):
    comfort = tmp_path / "comfort.csv"
    comfort.write_text(
        "eye_strain,headache,nausea,disorientation,depth_realism,ui_readability,crosstalk\n"
        "1,1,1,1,5,5,1\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "bundle"

    manifest = support_bundle.create_support_bundle(
        output_dir=out_dir,
        comfort_csv=comfort,
    )

    data = json.loads((out_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert data["comfort"]["quality"] == "GOOD"
    assert manifest.evaluation_path == out_dir / "evaluation.json"


def test_create_support_bundle_includes_overlay_timings_when_log_has_samples(tmp_path, monkeypatch):
    overlay_log = tmp_path / "overlay.log"
    overlay_log.write_text(
        "[15:26:00.000] Frame#10 acq[ok=10 timeout=0 lost=0 other=0] "
        "shm[LIVE reads=10 changes=1 (1/s) ts=1] depth[total=1 1Hz] "
        "head=(0.00,0.00,60.00) rest=(0.00,0.00) rel=(0.00,0.00) "
        "wobble=0.00 strength=1.00 depth=30.00 hasFrame=1\n"
        "[15:26:01.000] Frame#70 acq[ok=70 timeout=0 lost=0 other=0] "
        "shm[LIVE reads=70 changes=1 (1/s) ts=2] depth[total=2 1Hz] "
        "head=(0.00,0.00,60.00) rest=(0.00,0.00) rel=(0.00,0.00) "
        "wobble=0.00 strength=1.00 depth=30.00 hasFrame=1\n",
        encoding="utf-8",
    )
    report = DiagnosticsReport(
        project_root=tmp_path,
        python_executable=tmp_path / "python.exe",
        overlay_exe=tmp_path / "Glassless3DOverlay.exe",
        depth_model=tmp_path / "depth.onnx",
        config_path=tmp_path / "config.yaml",
        config_loaded=True,
        ready=True,
        problems=[],
        overlay_log=overlay_log,
    )
    monkeypatch.setattr(support_bundle, "collect_diagnostics", lambda _config: report)
    out_dir = tmp_path / "bundle"

    manifest = support_bundle.create_support_bundle(output_dir=out_dir)

    assert (out_dir / "overlay_timings.csv").exists()
    manifest_data = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_data["overlay_timings"] == "overlay_timings.csv"
    assert manifest.overlay_timings_path == out_dir / "overlay_timings.csv"


def test_main_writes_bundle(tmp_path, capsys):
    out_dir = tmp_path / "bundle"

    code = support_bundle.main(["--output-dir", str(out_dir)])

    assert code == 0
    assert (out_dir / "manifest.json").exists()
    assert "wrote support bundle" in capsys.readouterr().out
