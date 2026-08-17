import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from launcher import diagnostics
from tracker import display_acceptance


def test_write_acceptance_report_creates_validation_assets_and_report(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "overlay": {
                    "display_backend": "stereo_autostereo",
                    "display_calibration": {
                        "stereo_layout": "half_sbs",
                        "eye_order": "right_left",
                        "panel_width_px": 3840,
                        "panel_height_px": 1080,
                        "ipd_mm": 63.5,
                        "focus_plane_cm": 12.0,
                        "tracking_mode": "glassless3d_managed",
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "Glassless3DOverlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=config_path,
        config_loaded=True,
        ready=True,
        problems=[],
        configured_backend_id="stereo_autostereo",
        runtime_backend_id="stereo_autostereo",
        display_calibration={
            "stereo_layout": "half_sbs",
            "eye_order": "right_left",
            "panel_width_px": 3840,
            "panel_height_px": 1080,
            "ipd_mm": 63.5,
            "focus_plane_cm": 12.0,
            "tracking_mode": "glassless3d_managed",
        },
        overlay_summary=diagnostics.OverlayRuntimeSummary(
            frame_count=240,
            acq_ok=240,
            acq_timeout=0,
            acq_lost=0,
            acq_other=0,
            shm_status="LIVE",
            shm_changes_per_sec=32,
            depth_total=12,
            depth_hz=5,
            head_z_cm=60.0,
            has_frame=True,
            backend=1,
            stereo_layout=1,
            eye_order=1,
            ipd_cm=6.35,
            focus_plane_cm=12.0,
            panel_width_px=3840,
            panel_height_px=1080,
            tracking_mode=0,
        ),
        display_inventory=[
            diagnostics.DisplayInventoryItem(
                name="Generic PnP Monitor",
                device_id="DISPLAY\\SAM71AC",
                manufacturer="SAM",
                product_code="71AC",
                width_px=5120,
                height_px=1440,
                primary=True,
            )
        ],
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        width=64,
        height=32,
        require_live_runtime=True,
    )

    assert manifest.report_path.is_file()
    assert manifest.validation_assets.output_path.is_file()
    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["ready"] is False
    assert data["configured_backend_id"] == "stereo_autostereo"
    assert data["runtime_backend_id"] == "stereo_autostereo"
    assert data["display_inventory"] == [
        {
            "name": "Generic PnP Monitor",
            "device_id": "DISPLAY\\SAM71AC",
            "manufacturer": "SAM",
            "product_code": "71AC",
            "width_px": 5120,
            "height_px": 1440,
            "primary": True,
        }
    ]
    assert data["diagnostics_path"] == "diagnostics.json"
    assert manifest.diagnostics_path.is_file()
    assert manifest.validation_assets.image_path.is_file()
    assert manifest.validation_assets.depth_path.is_file()
    assert data["validation"] == {
        "depth_path": "validation/validation_depth.npy",
        "image_path": "validation/validation_source.png",
        "output_path": "validation/stereo_autostereo_validation.png",
    }


def test_write_acceptance_report_records_source_stereo_path_metadata(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: desktop_overlay\n", encoding="utf-8")
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "Glassless3DOverlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=config_path,
        config_loaded=True,
        ready=True,
        problems=[],
        configured_backend_id="desktop_overlay",
        runtime_backend_id="desktop_overlay",
        overlay_summary=diagnostics.OverlayRuntimeSummary(
            frame_count=1,
            acq_ok=1,
            acq_timeout=0,
            acq_lost=0,
            acq_other=0,
            shm_status="LIVE",
            shm_changes_per_sec=1,
            depth_total=1,
            depth_hz=1,
            head_z_cm=60.0,
            has_frame=True,
        ),
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        source_stereo_path="3dgamebridge",
        source_stereo_notes="SBS converted for SR panel",
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["source_stereo"] == {
        "path": "3dgamebridge",
        "notes": "SBS converted for SR panel",
    }


def test_acceptance_report_flags_missing_live_runtime(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "Glassless3DOverlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=config_path,
        config_loaded=True,
        ready=False,
        problems=["fresh overlay runtime summary missing"],
        configured_backend_id="stereo_autostereo",
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["runtime_ready"] is False
    assert data["checklist"]["backend_match"] is False


def test_acceptance_report_rejects_runtime_without_captured_frame(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}", encoding="utf-8")
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "Glassless3DOverlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=config_path,
        config_loaded=True,
        ready=True,
        problems=[],
        configured_backend_id="desktop_overlay",
        runtime_backend_id="desktop_overlay",
        overlay_summary=diagnostics.OverlayRuntimeSummary(
            frame_count=240,
            acq_ok=240,
            acq_timeout=0,
            acq_lost=0,
            acq_other=0,
            shm_status="LIVE",
            shm_changes_per_sec=32,
            depth_total=12,
            depth_hz=5,
            head_z_cm=60.0,
            has_frame=False,
            backend=0,
        ),
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["runtime_ready"] is False
    assert "overlay runtime has no captured frame" in data["problems"]


def test_acceptance_template_uses_custom_crosstalk_limit(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "Glassless3DOverlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=config_path,
        config_loaded=True,
        ready=False,
        problems=["fresh overlay runtime summary missing"],
        configured_backend_id="stereo_autostereo",
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        crosstalk_limit_percent=7.5,
    )

    template = yaml.safe_load((manifest.output_dir / "hardware_observation_template.yaml").read_text(encoding="utf-8"))
    assert isinstance(template, dict)
    assert template["crosstalk_limit_percent"] == 7.5


def test_acceptance_template_defaults_to_lightfield_type_for_quilt(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: lightfield_quilt\n", encoding="utf-8")
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "Glassless3DOverlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=config_path,
        config_loaded=True,
        ready=False,
        problems=["fresh overlay runtime summary missing"],
        configured_backend_id="lightfield_quilt",
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
    )

    template = yaml.safe_load((manifest.output_dir / "hardware_observation_template.yaml").read_text(encoding="utf-8"))
    assert isinstance(template, dict)
    assert template["target_display_type"] == "lightfield"


def test_acceptance_payload_requires_all_validation_assets(tmp_path):
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "Glassless3DOverlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=tmp_path / "config.yaml",
        config_loaded=True,
        ready=True,
        problems=[],
        configured_backend_id="desktop_overlay",
        runtime_backend_id="desktop_overlay",
        overlay_summary=diagnostics.OverlayRuntimeSummary(
            frame_count=240,
            acq_ok=240,
            acq_timeout=0,
            acq_lost=0,
            acq_other=0,
            shm_status="LIVE",
            shm_changes_per_sec=32,
            depth_total=12,
            depth_hz=5,
            head_z_cm=60.0,
            has_frame=True,
            backend=0,
        ),
    )
    output_path = tmp_path / "validation.png"
    output_path.write_bytes(b"png")
    assets = display_acceptance.ValidationAssets(
        image_path=tmp_path / "missing_source.png",
        depth_path=tmp_path / "missing_depth.npy",
        output_path=output_path,
    )

    payload = display_acceptance._acceptance_payload(report, assets)
    checklist = payload["checklist"]
    problems = payload["problems"]
    assert isinstance(checklist, dict)
    assert isinstance(problems, list)

    assert checklist["validation_assets_generated"] is False
    assert payload["ready"] is False
    assert "validation source image missing: missing_source.png" in problems
    assert "validation depth map missing: missing_depth.npy" in problems


def test_acceptance_report_uses_diagnostics_backend_when_config_has_no_backend(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}", encoding="utf-8")
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "Glassless3DOverlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=config_path,
        config_loaded=True,
        ready=True,
        problems=[],
        configured_backend_id="desktop_overlay",
        runtime_backend_id="desktop_overlay",
        overlay_summary=diagnostics.OverlayRuntimeSummary(
            frame_count=240,
            acq_ok=240,
            acq_timeout=0,
            acq_lost=0,
            acq_other=0,
            shm_status="LIVE",
            shm_changes_per_sec=32,
            depth_total=12,
            depth_hz=5,
            head_z_cm=60.0,
            has_frame=True,
            backend=0,
        ),
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        width=64,
        height=32,
        require_live_runtime=True,
    )

    assert manifest.validation_assets.output_path.name == "desktop_overlay_validation.png"


def test_acceptance_report_can_use_precollected_diagnostics_report(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}", encoding="utf-8")
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "Glassless3DOverlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=config_path,
        config_loaded=True,
        ready=True,
        problems=[],
        configured_backend_id="desktop_overlay",
        runtime_backend_id="desktop_overlay",
        overlay_summary=diagnostics.OverlayRuntimeSummary(
            frame_count=240,
            acq_ok=240,
            acq_timeout=0,
            acq_lost=0,
            acq_other=0,
            shm_status="LIVE",
            shm_changes_per_sec=32,
            depth_total=12,
            depth_hz=5,
            head_z_cm=60.0,
            has_frame=True,
            backend=0,
        ),
    )
    monkeypatch.setattr(
        display_acceptance.diagnostics,
        "collect_diagnostics",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not collect twice")),
    )

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        width=64,
        height=32,
        require_live_runtime=True,
        diagnostics_report=report,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is True
    assert data["runtime_backend_id"] == "desktop_overlay"


def test_acceptance_report_includes_passing_hardware_observation(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\ACRSPATIAL\\UID0",
                "target_display_type": "autostereo",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
                "notes": "view locks across the sweet spot",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is True
    assert data["checklist"]["hardware_observation_passed"] is True
    assert data["hardware_observation_path"] == "hardware_observation.yaml"
    assert data["hardware_observation"]["crosstalk_percent"] == 8.0
    assert data["hardware_observation"]["notes"] == "view locks across the sweet spot"
    assert (manifest.output_dir / "hardware_observation.yaml").read_text(encoding="utf-8") == observation_path.read_text(
        encoding="utf-8"
    )


def test_acceptance_report_requires_target_display_inventory_for_hardware_backends(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
            }
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    report = diagnostics.DiagnosticsReport(**{**report.__dict__, "display_inventory": []})
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["target_display_detected"] is False
    assert "hardware observation target_display_device_id required for stereo_autostereo acceptance" in data["problems"]
    assert "fix hardware_observation.yaml so every hardware checklist field passes" in data["next_steps"]


def test_acceptance_report_requires_observation_target_display_id_even_for_known_inventory(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
            }
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    report = diagnostics.DiagnosticsReport(
        **{
            **report.__dict__,
            "display_inventory": [
                diagnostics.DisplayInventoryItem(
                    name="Acer SpatialLabs Display",
                    manufacturer="ACR",
                    product_code="SpatialLabs",
                    width_px=3840,
                    height_px=2160,
                    primary=True,
                )
            ],
        }
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["target_display_detected"] is True
    assert data["checklist"]["target_display_observation_matched"] is False
    assert "connect or select the target glassless/autostereo/light-field display" not in data["next_steps"]
    assert "fill hardware_observation.yaml after viewing validation output on the target display" in data["next_steps"]
    assert "hardware observation target_display_device_id required for stereo_autostereo acceptance" in data["problems"]


def test_acceptance_report_rejects_observation_target_display_id_match_without_known_target_inventory(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\ABC123\\UID0",
                "target_display_type": "autostereo",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
            }
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    report = diagnostics.DiagnosticsReport(
        **{
            **report.__dict__,
            "display_inventory": [
                diagnostics.DisplayInventoryItem(
                    name="Generic PnP Monitor",
                    device_id="DISPLAY\\ABC123\\UID0",
                    manufacturer="ABC",
                    product_code="123",
                    width_px=3840,
                    height_px=2160,
                    primary=True,
                )
            ],
        }
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["target_display_detected"] is False
    assert data["checklist"]["target_display_observation_matched"] is False
    assert "target display inventory missing for stereo_autostereo acceptance" in data["problems"]


def test_acceptance_report_accepts_observation_target_display_id_match_on_known_target_inventory(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\ACRSPATIAL\\UID0",
                "target_display_type": "autostereo",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
            }
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is True
    assert data["checklist"]["target_display_detected"] is True
    assert data["checklist"]["target_display_observation_matched"] is True
    assert data["problems"] == []


def test_acceptance_report_rejects_lightfield_type_for_stereo_backend(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\ACRSPATIAL\\UID0",
                "target_display_type": "lightfield",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
            }
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["target_display_observation_matched"] is False
    assert any(
        "target_display_type lightfield is not compatible with stereo_autostereo" in problem
        for problem in data["problems"]
    )
    assert "fix hardware_observation.yaml so every hardware checklist field passes" in data["next_steps"]
    assert "fill hardware_observation.yaml after viewing validation output on the target display" not in data["next_steps"]


def test_acceptance_report_rejects_autostereo_type_for_quilt_backend(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: lightfield_quilt\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\LOOKINGGLASS\\UID0",
                "target_display_type": "autostereo",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
            }
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    report = diagnostics.DiagnosticsReport(
        **{
            **report.__dict__,
            "configured_backend_id": "lightfield_quilt",
            "runtime_backend_id": "lightfield_quilt",
            "display_inventory": [
                diagnostics.DisplayInventoryItem(
                    name="Looking Glass Portrait",
                    device_id="DISPLAY\\LOOKINGGLASS\\UID0",
                    manufacturer="Looking Glass",
                    product_code="Portrait",
                    width_px=3840,
                    height_px=2160,
                    primary=True,
                )
            ],
        }
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["target_display_observation_matched"] is False
    assert any(
        "target_display_type autostereo is not compatible with lightfield_quilt" in problem
        for problem in data["problems"]
    )
    assert "fix hardware_observation.yaml so every hardware checklist field passes" in data["next_steps"]
    assert "fill hardware_observation.yaml after viewing validation output on the target display" not in data["next_steps"]


def test_acceptance_report_accepts_compact_lookingglass_inventory_id(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: lightfield_quilt\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\LOOKINGGLASS\\UID0",
                "target_display_type": "lightfield",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
            }
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    report = diagnostics.DiagnosticsReport(
        **{
            **report.__dict__,
            "configured_backend_id": "lightfield_quilt",
            "runtime_backend_id": "lightfield_quilt",
            "display_inventory": [
                diagnostics.DisplayInventoryItem(
                    name="Generic PnP Monitor",
                    device_id="DISPLAY\\LOOKINGGLASS\\UID0",
                    manufacturer="LKG",
                    product_code="Portrait",
                    width_px=3840,
                    height_px=2160,
                    primary=True,
                )
            ],
        }
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is True
    assert data["checklist"]["target_display_detected"] is True
    assert data["checklist"]["target_display_observation_matched"] is True


def test_acceptance_report_accepts_punctuated_thinkvision_inventory_id(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\THINKVISION27-3D\\UID0",
                "target_display_type": "autostereo",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
            }
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    report = diagnostics.DiagnosticsReport(
        **{
            **report.__dict__,
            "display_inventory": [
                diagnostics.DisplayInventoryItem(
                    name="Generic PnP Monitor",
                    device_id="DISPLAY\\THINKVISION27-3D\\UID0",
                    manufacturer="LEN",
                    product_code="ThinkVision27-3D",
                    width_px=3840,
                    height_px=2160,
                    primary=True,
                )
            ],
        }
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is True
    assert data["checklist"]["target_display_detected"] is True
    assert data["checklist"]["target_display_observation_matched"] is True


def test_acceptance_report_accepts_leiasr_inventory_id(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\LEIASR\\UID0",
                "target_display_type": "autostereo",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
            }
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    report = diagnostics.DiagnosticsReport(
        **{
            **report.__dict__,
            "display_inventory": [
                diagnostics.DisplayInventoryItem(
                    name="Generic PnP Monitor",
                    device_id="DISPLAY\\LEIASR\\UID0",
                    manufacturer="Leia",
                    product_code="LeiaSR",
                    width_px=2560,
                    height_px=1600,
                    primary=True,
                )
            ],
        }
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is True
    assert data["checklist"]["target_display_detected"] is True
    assert data["checklist"]["target_display_observation_matched"] is True


def test_acceptance_report_accepts_lume_pad_2_inventory_name(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\LUMEPAD2\\UID0",
                "target_display_type": "autostereo",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
            }
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    report = diagnostics.DiagnosticsReport(
        **{
            **report.__dict__,
            "display_inventory": [
                diagnostics.DisplayInventoryItem(
                    name="Lume Pad 2",
                    device_id="DISPLAY\\LUMEPAD2\\UID0",
                    manufacturer="Leia",
                    product_code="LumePad2",
                    width_px=2560,
                    height_px=1600,
                    primary=True,
                )
            ],
        }
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is True
    assert data["checklist"]["target_display_detected"] is True
    assert data["checklist"]["target_display_observation_matched"] is True


def test_acceptance_report_rejects_placeholder_observation_device_id(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\REPLACE_WITH_TARGET_DEVICE_ID",
                "target_display_type": "autostereo",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
            }
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    report = diagnostics.DiagnosticsReport(
        **{
            **report.__dict__,
            "display_inventory": [
                diagnostics.DisplayInventoryItem(
                    name="Acer SpatialLabs Display",
                    device_id="DISPLAY\\REPLACE_WITH_TARGET_DEVICE_ID",
                    manufacturer="ACR",
                    product_code="SpatialLabs",
                    width_px=3840,
                    height_px=2160,
                    primary=True,
                )
            ],
        }
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert "hardware observation target_display_device_id is still a placeholder" in data["problems"]


def test_acceptance_report_rejects_observation_target_display_id_mismatch(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\MISSING\\UID0",
                "target_display_type": "autostereo",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
            }
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    report = diagnostics.DiagnosticsReport(
        **{
            **report.__dict__,
            "display_inventory": [
                diagnostics.DisplayInventoryItem(
                    name="Generic PnP Monitor",
                    device_id="DISPLAY\\ABC123\\UID0",
                    manufacturer="ABC",
                    product_code="123",
                    width_px=3840,
                    height_px=2160,
                    primary=True,
                )
            ],
        }
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["target_display_detected"] is False
    assert data["checklist"]["target_display_observation_matched"] is False
    assert "target display observation device_id does not match connected display inventory" in data["problems"]


def test_acceptance_report_rejects_target_display_id_without_valid_type(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\ABC123\\UID0",
                "target_display_type": "ordinary_monitor",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
            }
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    report = diagnostics.DiagnosticsReport(
        **{
            **report.__dict__,
            "display_inventory": [
                diagnostics.DisplayInventoryItem(
                    name="Generic PnP Monitor",
                    device_id="DISPLAY\\ABC123\\UID0",
                    manufacturer="ABC",
                    product_code="123",
                    width_px=3840,
                    height_px=2160,
                    primary=True,
                )
            ],
        }
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["target_display_detected"] is False
    assert "target_display_type must be one of: autostereo, glassless, lightfield, simulated_reality, spatial, sr" in data["problems"]


def test_acceptance_report_rejects_target_display_id_without_type(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\ABC123\\UID0",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
            }
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    report = diagnostics.DiagnosticsReport(
        **{
            **report.__dict__,
            "display_inventory": [
                diagnostics.DisplayInventoryItem(
                    name="Generic PnP Monitor",
                    device_id="DISPLAY\\ABC123\\UID0",
                    manufacturer="ABC",
                    product_code="123",
                    width_px=3840,
                    height_px=2160,
                    primary=True,
                )
            ],
        }
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert "target_display_type must be one of: autostereo, glassless, lightfield, simulated_reality, spatial, sr" in data["problems"]


def test_acceptance_report_rejects_invalid_target_display_type_without_device_id(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_type": "ordinary_monitor",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
            }
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert "target_display_type must be one of: autostereo, glassless, lightfield, simulated_reality, spatial, sr" in data["problems"]
    assert "hardware observation target_display_device_id required for stereo_autostereo acceptance" in data["problems"]


def test_acceptance_report_fails_bad_hardware_observation(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\ACRSPATIAL\\UID0",
                "target_display_type": "ordinary_monitor",
                "eye_order_correct": False,
                "depth_direction_correct": True,
                "ui_readable": False,
                "head_tracking_stable": True,
                "crosstalk_percent": 18.0,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["hardware_observation_passed"] is False
    assert "hardware eye order is incorrect" in data["problems"]
    assert "hardware UI readability failed" in data["problems"]
    assert "hardware crosstalk 18.0% exceeds limit 10.0%" in data["problems"]


def test_acceptance_report_uses_observation_crosstalk_limit(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "hardware.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\ACRSPATIAL\\UID0",
                "target_display_type": "autostereo",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
                "crosstalk_limit_percent": 7.5,
            }
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["crosstalk_limit_percent"] == 7.5
    assert "hardware crosstalk 8.0% exceeds limit 7.5%" in data["problems"]


def test_acceptance_report_cli_crosstalk_limit_overrides_observation(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "hardware.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\ACRSPATIAL\\UID0",
                "target_display_type": "autostereo",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
                "crosstalk_limit_percent": 7.5,
            }
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        crosstalk_limit_percent=10.0,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is True
    assert data["checklist"]["crosstalk_limit_percent"] == 10.0
    assert data["problems"] == []


def test_acceptance_report_fails_incomplete_hardware_observation(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "eye_order_correct": True,
                "depth_direction_correct": True,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["hardware_observation_passed"] is False
    assert "hardware observation missing required field: ui_readable" in data["problems"]
    assert "hardware observation missing required field: head_tracking_stable" in data["problems"]
    assert "hardware observation missing required field: crosstalk_percent" in data["problems"]


def test_acceptance_report_fails_invalid_hardware_observation_types(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "eye_order_correct": "unknown",
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": "not measured",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["hardware_observation_passed"] is False
    assert "hardware observation field must be true/false: eye_order_correct" in data["problems"]
    assert "hardware crosstalk_percent must be numeric" in data["problems"]


def test_acceptance_report_records_non_mapping_hardware_observation(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    report = _ready_report(tmp_path, config_path)
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["hardware_observation"] is None
    assert data["hardware_observation_path"] == "hardware_observation.yaml"
    assert "hardware observation could not be loaded: hardware observation must be a mapping" in data["problems"]


def test_acceptance_report_extra_hardware_observation_problem_blocks_ready(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "Glassless3DOverlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=config_path,
        config_loaded=True,
        ready=True,
        problems=[],
        configured_backend_id="desktop_overlay",
        runtime_backend_id="desktop_overlay",
        overlay_summary=diagnostics.OverlayRuntimeSummary(
            frame_count=240,
            acq_ok=240,
            acq_timeout=0,
            acq_lost=0,
            acq_other=0,
            shm_status="LIVE",
            shm_changes_per_sec=32,
            depth_total=12,
            depth_hz=5,
            head_z_cm=60.0,
            has_frame=True,
            backend=0,
        ),
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["hardware_observation_passed"] is False
    assert "hardware observation could not be loaded: hardware observation must be a mapping" in data["problems"]


def test_desktop_acceptance_rejects_supplied_observation_device_id_mismatch(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\MISSING\\UID0",
                "target_display_type": "autostereo",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
            }
        ),
        encoding="utf-8",
    )
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "Glassless3DOverlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=config_path,
        config_loaded=True,
        ready=True,
        problems=[],
        configured_backend_id="desktop_overlay",
        runtime_backend_id="desktop_overlay",
        display_inventory=[
            diagnostics.DisplayInventoryItem(
                name="Generic PnP Monitor",
                device_id="DISPLAY\\PRESENT\\UID0",
                manufacturer="SAM",
                product_code="71AC",
                width_px=5120,
                height_px=1440,
                primary=True,
            )
        ],
        overlay_summary=diagnostics.OverlayRuntimeSummary(
            frame_count=240,
            acq_ok=240,
            acq_timeout=0,
            acq_lost=0,
            acq_other=0,
            shm_status="LIVE",
            shm_changes_per_sec=32,
            depth_total=12,
            depth_hz=5,
            head_z_cm=60.0,
            has_frame=True,
            backend=0,
        ),
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["hardware_observation_passed"] is False
    assert "target display observation device_id does not match connected display inventory" in data["problems"]
    assert "fix hardware_observation.yaml so every hardware checklist field passes" in data["next_steps"]


def test_acceptance_report_fails_negative_crosstalk(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\ACRSPATIAL\\UID0",
                "target_display_type": "autostereo",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": -1.0,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["hardware_observation_passed"] is False
    assert "hardware crosstalk_percent must be non-negative" in data["problems"]


def test_acceptance_report_fails_non_finite_crosstalk(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "target_display_device_id": "DISPLAY\\ACRSPATIAL\\UID0",
                "target_display_type": "autostereo",
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": "nan",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["hardware_observation_passed"] is False
    assert "hardware crosstalk_percent must be finite" in data["problems"]


def test_acceptance_report_fails_invalid_crosstalk_limit(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        yaml.safe_dump(
            {
                "eye_order_correct": True,
                "depth_direction_correct": True,
                "ui_readable": True,
                "head_tracking_stable": True,
                "crosstalk_percent": 8.0,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
        crosstalk_limit_percent=float("nan"),
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["hardware_observation_passed"] is False
    assert "hardware crosstalk limit must be finite and non-negative" in data["problems"]


def test_acceptance_report_fails_invalid_crosstalk_limit_without_observation(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}", encoding="utf-8")
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "Glassless3DOverlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=config_path,
        config_loaded=True,
        ready=True,
        problems=[],
        configured_backend_id="desktop_overlay",
        runtime_backend_id="desktop_overlay",
        overlay_summary=diagnostics.OverlayRuntimeSummary(
            frame_count=240,
            acq_ok=240,
            acq_timeout=0,
            acq_lost=0,
            acq_other=0,
            shm_status="LIVE",
            shm_changes_per_sec=32,
            depth_total=12,
            depth_hz=5,
            head_z_cm=60.0,
            has_frame=True,
            backend=0,
        ),
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        require_live_runtime=True,
        crosstalk_limit_percent=float("nan"),
    )

    data = json.loads(manifest.report_path.read_text(encoding="utf-8"))
    assert data["ready"] is False
    assert data["checklist"]["crosstalk_limit_percent"] is None
    assert "hardware crosstalk limit must be finite and non-negative" in data["problems"]


def test_acceptance_report_writes_strict_json_for_non_finite_values(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("overlay:\n  display_backend: stereo_autostereo\n", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text(
        "eye_order_correct: true\n"
        "depth_direction_correct: true\n"
        "ui_readable: true\n"
        "head_tracking_stable: true\n"
        "crosstalk_percent: .nan\n",
        encoding="utf-8",
    )
    report = _ready_report(tmp_path, config_path)
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    manifest = display_acceptance.write_acceptance_report(
        tmp_path / "acceptance",
        config_path=config_path,
        hardware_observation_path=observation_path,
        require_live_runtime=True,
        crosstalk_limit_percent=float("nan"),
    )

    text = manifest.report_path.read_text(encoding="utf-8")
    assert "NaN" not in text
    data = json.loads(text)
    assert data["ready"] is False
    assert data["checklist"]["crosstalk_limit_percent"] is None
    assert data["hardware_observation"]["crosstalk_percent"] == "nan"
    assert "hardware crosstalk_percent must be finite" in data["problems"]
    assert "hardware crosstalk limit must be finite and non-negative" in data["problems"]


def test_main_returns_success_when_acceptance_ready(tmp_path, monkeypatch):
    report_path = tmp_path / "acceptance_report.json"
    report_path.write_text('{"ready": true}\n', encoding="utf-8")

    monkeypatch.setattr(
        display_acceptance,
        "write_acceptance_report",
        lambda *args, **kwargs: SimpleNamespace(report_path=report_path),
    )

    assert display_acceptance.main(["out"]) == 0


def test_main_returns_failure_when_acceptance_not_ready(tmp_path, monkeypatch):
    report_path = tmp_path / "acceptance_report.json"
    report_path.write_text('{"ready": false}\n', encoding="utf-8")

    monkeypatch.setattr(
        display_acceptance,
        "write_acceptance_report",
        lambda *args, **kwargs: SimpleNamespace(report_path=report_path),
    )

    assert display_acceptance.main(["out"]) == 1


def test_main_returns_failure_for_malformed_hardware_observation(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}", encoding="utf-8")
    observation_path = tmp_path / "observation.yaml"
    observation_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    report = diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "Glassless3DOverlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=config_path,
        config_loaded=True,
        ready=True,
        problems=[],
        configured_backend_id="desktop_overlay",
        runtime_backend_id="desktop_overlay",
        overlay_summary=diagnostics.OverlayRuntimeSummary(
            frame_count=240,
            acq_ok=240,
            acq_timeout=0,
            acq_lost=0,
            acq_other=0,
            shm_status="LIVE",
            shm_changes_per_sec=32,
            depth_total=12,
            depth_hz=5,
            head_z_cm=60.0,
            has_frame=True,
            backend=0,
        ),
    )
    monkeypatch.setattr(display_acceptance.diagnostics, "collect_diagnostics", lambda config, require_live_runtime=False: report)

    code = display_acceptance.main([
        str(tmp_path / "acceptance"),
        "--config",
        str(config_path),
        "--require-live-runtime",
        "--hardware-observation",
        str(observation_path),
    ])

    assert code == 1


def _ready_report(tmp_path: Path, config_path: Path) -> diagnostics.DiagnosticsReport:
    return diagnostics.DiagnosticsReport(
        project_root=tmp_path,
        python_executable=Path("python"),
        overlay_exe=tmp_path / "Glassless3DOverlay.exe",
        depth_model=tmp_path / "model.onnx",
        config_path=config_path,
        config_loaded=True,
        ready=True,
        problems=[],
        configured_backend_id="stereo_autostereo",
        runtime_backend_id="stereo_autostereo",
        display_inventory=[
            diagnostics.DisplayInventoryItem(
                name="Acer SpatialLabs Display",
                device_id="DISPLAY\\ACRSPATIAL\\UID0",
                manufacturer="ACR",
                product_code="SpatialLabs",
                width_px=3840,
                height_px=2160,
                primary=True,
            )
        ],
        overlay_summary=diagnostics.OverlayRuntimeSummary(
            frame_count=240,
            acq_ok=240,
            acq_timeout=0,
            acq_lost=0,
            acq_other=0,
            shm_status="LIVE",
            shm_changes_per_sec=32,
            depth_total=12,
            depth_hz=5,
            head_z_cm=60.0,
            has_frame=True,
            backend=1,
        ),
    )
