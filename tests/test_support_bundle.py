import json
import numpy as np

from launcher import support_bundle
from launcher.diagnostics import DiagnosticsReport
from tests.test_depth_fixtures import _write_fixture


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


def test_create_support_bundle_accepts_depth_fixture(tmp_path):
    fixture_root = tmp_path / "fixtures"
    _write_fixture(fixture_root)
    out_dir = tmp_path / "bundle"

    manifest = support_bundle.create_support_bundle(
        output_dir=out_dir,
        depth_fixture="stable",
        depth_fixture_root=fixture_root,
    )

    data = json.loads((out_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert data["depth"]["quality"] == "GOOD"
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


def test_create_support_bundle_includes_display_quality_input(tmp_path):
    display_quality = tmp_path / "display_quality.csv"
    display_quality.write_text(
        "x_cm,z_cm,crosstalk_percent,view_locked\n"
        "-10,60,8,true\n"
        "10,60,7,true\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "bundle"

    manifest = support_bundle.create_support_bundle(
        output_dir=out_dir,
        display_quality_csv=display_quality,
    )

    data = json.loads((out_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert data["display_quality"]["quality"] == "GOOD"
    assert manifest.evaluation_path == out_dir / "evaluation.json"


def test_create_support_bundle_includes_latency_input(tmp_path):
    latency = tmp_path / "latency.csv"
    latency.write_text(
        "timestamp_ms,tracking_to_display_ms\n"
        "0,10\n"
        "16,12\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "bundle"

    manifest = support_bundle.create_support_bundle(
        output_dir=out_dir,
        latency_csv=latency,
        latency_target_ms=20.0,
    )

    data = json.loads((out_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert data["latency"]["quality"] == "GOOD"
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
    monkeypatch.setattr(
        support_bundle,
        "collect_diagnostics",
        lambda _config, require_live_runtime=False: report,
    )
    out_dir = tmp_path / "bundle"

    manifest = support_bundle.create_support_bundle(output_dir=out_dir)

    assert (out_dir / "overlay_timings.csv").exists()
    manifest_data = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_data["overlay_timings"] == "overlay_timings.csv"
    assert manifest.overlay_timings_path == out_dir / "overlay_timings.csv"


def test_create_support_bundle_includes_display_acceptance_when_observation_provided(tmp_path, monkeypatch):
    observation = tmp_path / "hardware_observation.yaml"
    observation.write_text(
        "eye_order_correct: true\n"
        "depth_direction_correct: true\n"
        "ui_readable: true\n"
        "head_tracking_stable: true\n"
        "crosstalk_percent: 8.0\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "bundle"

    manifest = support_bundle.create_support_bundle(
        output_dir=out_dir,
        hardware_observation=observation,
        require_live_runtime=True,
    )

    acceptance_path = out_dir / "display_acceptance" / "acceptance_report.json"
    assert acceptance_path.exists()
    assert (out_dir / "display_acceptance" / "hardware_observation.yaml").exists()
    manifest_data = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    acceptance_data = json.loads(acceptance_path.read_text(encoding="utf-8"))
    assert manifest_data["display_acceptance"] == "display_acceptance/acceptance_report.json"
    assert manifest_data["display_acceptance_ready"] is acceptance_data["ready"]
    assert manifest_data["display_acceptance_problems"] == acceptance_data["problems"]
    assert manifest.display_acceptance_path == acceptance_path


def test_create_support_bundle_includes_display_acceptance_when_live_runtime_required(tmp_path, monkeypatch):
    calls = []

    class FakeAcceptance:
        def __init__(self, report_path):
            self.report_path = report_path

    def fake_write_acceptance_report(output_dir, **kwargs):
        calls.append((output_dir, kwargs))
        output = tmp_path / output_dir
        output.mkdir(parents=True, exist_ok=True)
        report_path = output / "acceptance_report.json"
        report_path.write_text('{"ready": false, "problems": ["not target hardware"]}\n', encoding="utf-8")
        return FakeAcceptance(report_path)

    monkeypatch.setattr(support_bundle, "write_acceptance_report", fake_write_acceptance_report)
    out_dir = tmp_path / "bundle"

    manifest = support_bundle.create_support_bundle(
        output_dir=out_dir,
        require_live_runtime=True,
        crosstalk_limit_percent=7.5,
        source_stereo_path="geo11",
        source_stereo_notes="external full SBS source",
    )

    acceptance_path = out_dir / "display_acceptance" / "acceptance_report.json"
    assert acceptance_path.exists()
    manifest_data = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_data["display_acceptance"] == "display_acceptance/acceptance_report.json"
    assert manifest_data["display_acceptance_ready"] is False
    assert manifest_data["display_acceptance_problems"] == ["not target hardware"]
    assert manifest_data["source_stereo"] == {
        "path": "geo11",
        "notes": "external full SBS source",
    }
    assert manifest.display_acceptance_path == acceptance_path
    assert calls[0][1]["diagnostics_report"] is not None
    calls[0][1]["diagnostics_report"] = None
    assert calls == [
        (
            out_dir / "display_acceptance",
            {
                "config_path": "config.yaml",
                "require_live_runtime": True,
                "hardware_observation_path": None,
                "crosstalk_limit_percent": 7.5,
                "source_stereo_path": "geo11",
                "source_stereo_notes": "external full SBS source",
                "diagnostics_report": None,
            },
        )
    ]


def test_create_support_bundle_passes_live_runtime_requirement_to_diagnostics(tmp_path, monkeypatch):
    calls = []

    class FakeAcceptance:
        report_path = tmp_path / "bundle" / "display_acceptance" / "acceptance_report.json"

    def fake_collect(config, require_live_runtime=False):
        calls.append((config, require_live_runtime))
        return DiagnosticsReport(
            project_root=tmp_path,
            python_executable=tmp_path / "python.exe",
            overlay_exe=tmp_path / "Glassless3DOverlay.exe",
            depth_model=tmp_path / "depth.onnx",
            config_path=tmp_path / "config.yaml",
            config_loaded=True,
            ready=True,
            problems=[],
        )

    monkeypatch.setattr(support_bundle, "collect_diagnostics", fake_collect)
    monkeypatch.setattr(
        support_bundle,
        "write_acceptance_report",
        lambda *args, **kwargs: FakeAcceptance(),
    )

    support_bundle.create_support_bundle(
        output_dir=tmp_path / "bundle",
        config_path="config.yaml",
        require_live_runtime=True,
    )

    assert calls == [("config.yaml", True)]


def test_main_writes_bundle(tmp_path, capsys):
    out_dir = tmp_path / "bundle"

    code = support_bundle.main(["--output-dir", str(out_dir)])

    assert code == 0
    assert (out_dir / "manifest.json").exists()
    assert "wrote support bundle" in capsys.readouterr().out


def test_main_accepts_depth_fixture(tmp_path, capsys):
    fixture_root = tmp_path / "fixtures"
    _write_fixture(fixture_root)
    out_dir = tmp_path / "bundle"

    code = support_bundle.main([
        "--output-dir",
        str(out_dir),
        "--depth-fixture",
        "stable",
        "--depth-fixture-root",
        str(fixture_root),
    ])

    assert code == 0
    assert (out_dir / "evaluation.json").exists()
    assert "wrote support bundle" in capsys.readouterr().out


def test_main_accepts_hardware_observation(tmp_path, capsys):
    observation = tmp_path / "hardware_observation.yaml"
    observation.write_text(
        "eye_order_correct: true\n"
        "depth_direction_correct: true\n"
        "ui_readable: true\n"
        "head_tracking_stable: true\n"
        "crosstalk_percent: 8.0\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "bundle"

    code = support_bundle.main([
        "--output-dir",
        str(out_dir),
        "--hardware-observation",
        str(observation),
        "--crosstalk-limit-percent",
        "7.5",
        "--require-live-runtime",
    ])

    assert code == 0
    assert (out_dir / "display_acceptance" / "acceptance_report.json").exists()
    data = json.loads((out_dir / "display_acceptance" / "acceptance_report.json").read_text(encoding="utf-8"))
    assert data["checklist"]["crosstalk_limit_percent"] == 7.5
    assert data["checklist"]["hardware_observation_passed"] is False
    output = capsys.readouterr().out
    assert "wrote support bundle" in output
    assert "display acceptance: NOT READY" in output


def test_main_can_require_display_acceptance_ready(tmp_path, capsys):
    observation = tmp_path / "hardware_observation.yaml"
    observation.write_text(
        "eye_order_correct: true\n"
        "depth_direction_correct: true\n"
        "ui_readable: true\n"
        "head_tracking_stable: true\n"
        "crosstalk_percent: 8.0\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "bundle"

    code = support_bundle.main([
        "--output-dir",
        str(out_dir),
        "--hardware-observation",
        str(observation),
        "--require-live-runtime",
        "--require-display-acceptance-ready",
    ])

    assert code == 1
    assert (out_dir / "display_acceptance" / "acceptance_report.json").exists()
    output = capsys.readouterr().out
    assert "display acceptance: NOT READY" in output
    assert "display acceptance is required to be READY" in output


def test_main_requires_display_acceptance_report_when_strict(tmp_path, capsys):
    out_dir = tmp_path / "bundle"

    code = support_bundle.main([
        "--output-dir",
        str(out_dir),
        "--require-display-acceptance-ready",
    ])

    assert code == 1
    assert (out_dir / "manifest.json").exists()
    output = capsys.readouterr().out
    assert "display acceptance is required but was not generated" in output


def test_main_strict_display_acceptance_allows_ready_report(tmp_path, monkeypatch, capsys):
    class FakeAcceptance:
        def __init__(self, report_path):
            self.report_path = report_path

    def fake_write_acceptance_report(output_dir, **kwargs):
        output = tmp_path / output_dir
        output.mkdir(parents=True, exist_ok=True)
        report_path = output / "acceptance_report.json"
        report_path.write_text('{"ready": true, "problems": []}\n', encoding="utf-8")
        return FakeAcceptance(report_path)

    monkeypatch.setattr(support_bundle, "write_acceptance_report", fake_write_acceptance_report)
    out_dir = tmp_path / "bundle"

    code = support_bundle.main([
        "--output-dir",
        str(out_dir),
        "--require-live-runtime",
        "--require-display-acceptance-ready",
    ])

    assert code == 0
    output = capsys.readouterr().out
    assert "display acceptance: READY" in output
