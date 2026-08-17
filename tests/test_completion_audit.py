import json
import os
import time
import subprocess
import sys

from scripts import audit_completion


def test_audit_completion_rejects_stale_saved_evidence(tmp_path):
    acceptance = tmp_path / "acceptance.json"
    support_dir = tmp_path / "support"
    support_dir.mkdir()
    support_acceptance = support_dir / "acceptance.json"
    support_manifest = support_dir / "manifest.json"

    acceptance.write_text(json.dumps({"ready": True, "problems": []}), encoding="utf-8")
    support_acceptance.write_text(json.dumps({"ready": True, "problems": []}), encoding="utf-8")
    support_manifest.write_text(
        json.dumps(
            {
                "display_acceptance_ready": True,
                "display_acceptance_problems": [],
                "display_acceptance": "acceptance.json",
            }
        ),
        encoding="utf-8",
    )
    stale = time.time() - 48 * 3600
    for path in (acceptance, support_acceptance, support_manifest):
        os.utime(path, (stale, stale))

    code = audit_completion.main(
        [
            "--desktop-acceptance",
            str(acceptance),
            "--desktop-support",
            str(support_manifest),
            "--max-evidence-age-hours",
            "24",
        ]
    )

    assert code == 1


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _hardware_observation(device_id="DISPLAY\\SPATIALLABS\\UID1", target_type="autostereo"):
    return {
        "target_display_device_id": device_id,
        "target_display_type": target_type,
        "eye_order_correct": True,
        "depth_direction_correct": True,
        "ui_readable": True,
        "head_tracking_stable": True,
        "crosstalk_percent": 8.0,
    }


def _ready_hardware_acceptance(device_id="DISPLAY\\SPATIALLABS\\UID1", target_type="autostereo"):
    return {
        "ready": True,
        "problems": [],
        "hardware_observation_path": "hardware_observation.yaml",
        "hardware_observation": _hardware_observation(device_id=device_id, target_type=target_type),
        "checklist": {
            "runtime_ready": True,
            "backend_match": True,
            "calibration_match": True,
            "hardware_observation_passed": True,
            "target_display_observation_matched": True,
        },
    }


def _ready_desktop_acceptance():
    return {
        "ready": True,
        "problems": [],
        "face_tracking_required": True,
        "checklist": {"face_tracking_active": True},
    }


def test_audit_completion_reports_ready_only_when_all_artifacts_pass(tmp_path, capsys):
    inventory = tmp_path / "inventory.json"
    desktop_acceptance = tmp_path / "desktop_acceptance.json"
    desktop_support = tmp_path / "desktop_support" / "manifest.json"
    stereo_acceptance = tmp_path / "stereo_acceptance.json"
    stereo_support = tmp_path / "stereo_support" / "manifest.json"
    quilt_acceptance = tmp_path / "quilt_acceptance.json"
    quilt_support = tmp_path / "quilt_support" / "manifest.json"

    _write_json(
        inventory,
        {
            "display_inventory": [
                {
                    "name": "Acer SpatialLabs Display",
                    "device_id": "DISPLAY\\SPATIALLABS\\UID1",
                    "product_code": "SpatialLabs",
                }
            ]
        },
    )
    _write_json(desktop_acceptance, _ready_desktop_acceptance())
    _write_json(
        desktop_support,
        {
            "display_acceptance": "display_acceptance/acceptance_report.json",
            "display_acceptance_ready": True,
            "display_acceptance_problems": [],
        },
    )
    _write_json(stereo_acceptance, _ready_hardware_acceptance())
    _write_json(
        stereo_support,
        {
            "display_acceptance": "display_acceptance/acceptance_report.json",
            "display_acceptance_ready": True,
            "display_acceptance_problems": [],
        },
    )
    _write_json(quilt_acceptance, _ready_hardware_acceptance(target_type="lightfield"))
    _write_json(
        quilt_support,
        {
            "display_acceptance": "display_acceptance/acceptance_report.json",
            "display_acceptance_ready": True,
            "display_acceptance_problems": [],
        },
    )
    _write_json(
        desktop_support.parent / "display_acceptance" / "acceptance_report.json",
        _ready_desktop_acceptance(),
    )
    for manifest in (stereo_support, quilt_support):
        _write_json(
            manifest.parent / "display_acceptance" / "acceptance_report.json",
                _ready_hardware_acceptance(target_type="lightfield" if manifest is quilt_support else "autostereo"),
            )

    code = audit_completion.main(
        [
            "--require-target-display-hardware",
            "--inventory",
            str(inventory),
            "--desktop-acceptance",
            str(desktop_acceptance),
            "--desktop-support",
            str(desktop_support),
            "--stereo-acceptance",
            str(stereo_acceptance),
            "--stereo-support",
            str(stereo_support),
            "--quilt-acceptance",
            str(quilt_acceptance),
            "--quilt-support",
            str(quilt_support),
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "completion audit: READY" in output


def test_audit_completion_accepts_leiasr_target_inventory_token(tmp_path, capsys):
    inventory = tmp_path / "inventory.json"
    desktop_acceptance = tmp_path / "desktop_acceptance.json"
    desktop_support = tmp_path / "desktop_support" / "manifest.json"
    stereo_acceptance = tmp_path / "stereo_acceptance.json"
    stereo_support = tmp_path / "stereo_support" / "manifest.json"
    quilt_acceptance = tmp_path / "quilt_acceptance.json"
    quilt_support = tmp_path / "quilt_support" / "manifest.json"

    _write_json(
        inventory,
        {
            "display_inventory": [
                {
                    "name": "Generic PnP Monitor",
                    "device_id": "DISPLAY\\LEIASR\\UID1",
                    "product_code": "LeiaSR",
                }
            ]
        },
    )
    _write_json(desktop_acceptance, _ready_desktop_acceptance())
    _write_json(
        desktop_support,
        {
            "display_acceptance": "display_acceptance/acceptance_report.json",
            "display_acceptance_ready": True,
            "display_acceptance_problems": [],
        },
    )
    _write_json(stereo_acceptance, _ready_hardware_acceptance(device_id="DISPLAY\\LEIASR\\UID1"))
    _write_json(
        quilt_acceptance,
        _ready_hardware_acceptance(device_id="DISPLAY\\LEIASR\\UID1", target_type="lightfield"),
    )
    for manifest in (desktop_support, stereo_support, quilt_support):
        _write_json(
            manifest,
            {
                "display_acceptance": "display_acceptance/acceptance_report.json",
                "display_acceptance_ready": True,
                "display_acceptance_problems": [],
            },
        )
        acceptance_payload = _ready_desktop_acceptance()
        if manifest is not desktop_support:
            acceptance_payload = {
                **acceptance_payload,
                **_ready_hardware_acceptance(
                    device_id="DISPLAY\\LEIASR\\UID1",
                    target_type="lightfield" if manifest is quilt_support else "autostereo",
                ),
            }
        _write_json(manifest.parent / "display_acceptance" / "acceptance_report.json", acceptance_payload)

    code = audit_completion.main(
        [
            "--require-target-display-hardware",
            "--inventory",
            str(inventory),
            "--desktop-acceptance",
            str(desktop_acceptance),
            "--desktop-support",
            str(desktop_support),
            "--stereo-acceptance",
            str(stereo_acceptance),
            "--stereo-support",
            str(stereo_support),
            "--quilt-acceptance",
            str(quilt_acceptance),
            "--quilt-support",
            str(quilt_support),
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "completion audit: READY" in output


def test_audit_completion_accepts_camera_tracked_desktop_without_target_display(tmp_path, capsys):
    inventory = tmp_path / "inventory.json"
    desktop_acceptance = tmp_path / "desktop_acceptance.json"
    desktop_support = tmp_path / "desktop_support" / "manifest.json"
    stereo_acceptance = tmp_path / "stereo_acceptance.json"
    stereo_support = tmp_path / "stereo_support" / "manifest.json"
    quilt_acceptance = tmp_path / "quilt_acceptance.json"
    quilt_support = tmp_path / "quilt_support" / "manifest.json"

    _write_json(
        inventory,
        {
            "display_inventory": [
                {
                    "name": "Generic PnP Monitor",
                    "device_id": "DISPLAY\\SAM71AC\\UID4352",
                    "product_code": "71AC",
                }
            ]
        },
    )
    _write_json(
        desktop_acceptance,
        {
            "ready": True,
            "problems": [],
            "face_tracking_required": True,
            "checklist": {
                "face_tracking_active": True,
                "runtime_ready": True,
                "backend_match": True,
                "calibration_match": True,
                "hardware_observation_required": False,
                "validation_assets_generated": True,
            },
        },
    )
    _write_json(
        desktop_support,
        {
            "display_acceptance": "display_acceptance/acceptance_report.json",
            "display_acceptance_ready": True,
            "display_acceptance_problems": [],
        },
    )
    _write_json(
        desktop_support.parent / "display_acceptance" / "acceptance_report.json",
        _ready_desktop_acceptance(),
    )
    _write_json(stereo_acceptance, {"ready": False, "problems": ["optional stereo hardware unavailable"]})
    _write_json(stereo_support, {"display_acceptance_ready": False, "display_acceptance_problems": []})
    _write_json(quilt_acceptance, {"ready": False, "problems": ["optional quilt hardware unavailable"]})
    _write_json(quilt_support, {"display_acceptance_ready": False, "display_acceptance_problems": []})

    code = audit_completion.main(
        [
            "--inventory",
            str(inventory),
            "--desktop-acceptance",
            str(desktop_acceptance),
            "--desktop-support",
            str(desktop_support),
            "--stereo-acceptance",
            str(stereo_acceptance),
            "--stereo-support",
            str(stereo_support),
            "--quilt-acceptance",
            str(quilt_acceptance),
            "--quilt-support",
            str(quilt_support),
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "completion audit: READY" in output


def test_audit_completion_blocks_without_target_display_or_non_desktop_acceptance(tmp_path, capsys):
    inventory = tmp_path / "inventory.json"
    desktop_acceptance = tmp_path / "desktop_acceptance.json"
    desktop_support = tmp_path / "desktop_support" / "manifest.json"
    stereo_acceptance = tmp_path / "stereo_acceptance.json"
    stereo_support = tmp_path / "stereo_support" / "manifest.json"
    quilt_acceptance = tmp_path / "quilt_acceptance.json"
    quilt_support = tmp_path / "quilt_support" / "manifest.json"

    _write_json(
        inventory,
        {
            "display_inventory": [
                {
                    "name": "Generic PnP Monitor",
                    "device_id": "DISPLAY\\SAM71AC\\UID4352",
                    "product_code": "71AC",
                }
            ]
        },
    )
    _write_json(desktop_acceptance, {"ready": True, "problems": []})
    _write_json(desktop_support, {"display_acceptance_ready": True, "display_acceptance_problems": []})
    _write_json(
        stereo_acceptance,
        {
            "ready": False,
            "problems": [
                "target display inventory missing for stereo_autostereo acceptance",
                "hardware observation required for stereo_autostereo acceptance",
            ],
        },
    )
    _write_json(
        stereo_support,
        {
            "display_acceptance_ready": False,
            "display_acceptance_problems": ["target display inventory missing for stereo_autostereo acceptance"],
        },
    )
    _write_json(
        quilt_acceptance,
        {
            "ready": False,
            "problems": [
                "target display inventory missing for lightfield_quilt acceptance",
                "hardware observation required for lightfield_quilt acceptance",
            ],
        },
    )
    _write_json(
        quilt_support,
        {
            "display_acceptance_ready": False,
            "display_acceptance_problems": ["target display inventory missing for lightfield_quilt acceptance"],
        },
    )

    code = audit_completion.main(
        [
            "--require-target-display-hardware",
            "--inventory",
            str(inventory),
            "--desktop-acceptance",
            str(desktop_acceptance),
            "--desktop-support",
            str(desktop_support),
            "--stereo-acceptance",
            str(stereo_acceptance),
            "--stereo-support",
            str(stereo_support),
            "--quilt-acceptance",
            str(quilt_acceptance),
            "--quilt-support",
            str(quilt_support),
        ]
    )

    output = capsys.readouterr().out
    assert code == 1
    assert "completion audit: NOT READY" in output
    assert "target display inventory does not include a known glassless/autostereo/light-field panel" in output
    assert "stereo acceptance is not ready" in output
    assert "quilt support bundle display acceptance is not ready" in output
    assert "next step: follow docs/HARDWARE_ACCEPTANCE_CHECKLIST.md with a connected target display" in output


def test_audit_completion_blocks_ready_artifacts_that_still_report_problems(tmp_path, capsys):
    inventory = tmp_path / "inventory.json"
    desktop_acceptance = tmp_path / "desktop_acceptance.json"
    desktop_support = tmp_path / "desktop_manifest.json"
    stereo_acceptance = tmp_path / "stereo_acceptance.json"
    stereo_support = tmp_path / "stereo_manifest.json"
    quilt_acceptance = tmp_path / "quilt_acceptance.json"
    quilt_support = tmp_path / "quilt_manifest.json"

    _write_json(
        inventory,
        {
            "display_inventory": [
                {
                    "name": "Looking Glass 16",
                    "device_id": "DISPLAY\\LOOKINGGLASS\\UID1",
                    "product_code": "LookingGlass",
                }
            ]
        },
    )
    _write_json(desktop_acceptance, {"ready": True, "problems": []})
    _write_json(desktop_support, {"display_acceptance_ready": True, "display_acceptance_problems": []})
    _write_json(stereo_acceptance, {"ready": True, "problems": ["stale stereo warning"]})
    _write_json(
        stereo_support,
        {"display_acceptance_ready": True, "display_acceptance_problems": ["stale support warning"]},
    )
    _write_json(quilt_acceptance, {"ready": True, "problems": []})
    _write_json(quilt_support, {"display_acceptance_ready": True, "display_acceptance_problems": []})

    code = audit_completion.main(
        [
            "--require-target-display-hardware",
            "--inventory",
            str(inventory),
            "--desktop-acceptance",
            str(desktop_acceptance),
            "--desktop-support",
            str(desktop_support),
            "--stereo-acceptance",
            str(stereo_acceptance),
            "--stereo-support",
            str(stereo_support),
            "--quilt-acceptance",
            str(quilt_acceptance),
            "--quilt-support",
            str(quilt_support),
        ]
    )

    output = capsys.readouterr().out
    assert code == 1
    assert "stereo acceptance reports problems despite ready=true: stale stereo warning" in output
    assert (
        "stereo support bundle reports display acceptance problems despite ready=true: stale support warning"
        in output
    )


def test_audit_completion_requires_non_desktop_hardware_checklist_evidence(tmp_path, capsys):
    inventory = tmp_path / "inventory.json"
    desktop_acceptance = tmp_path / "desktop_acceptance.json"
    desktop_support = tmp_path / "desktop_manifest.json"
    stereo_acceptance = tmp_path / "stereo_acceptance.json"
    stereo_support = tmp_path / "stereo_manifest.json"
    quilt_acceptance = tmp_path / "quilt_acceptance.json"
    quilt_support = tmp_path / "quilt_manifest.json"

    _write_json(
        inventory,
        {
            "display_inventory": [
                {
                    "name": "Acer SpatialLabs Display",
                    "device_id": "DISPLAY\\SPATIALLABS\\UID1",
                    "product_code": "SpatialLabs",
                }
            ]
        },
    )
    _write_json(desktop_acceptance, {"ready": True, "problems": []})
    _write_json(desktop_support, {"display_acceptance_ready": True, "display_acceptance_problems": []})
    _write_json(stereo_acceptance, {"ready": True, "problems": [], "checklist": {"runtime_ready": True}})
    _write_json(stereo_support, {"display_acceptance_ready": True, "display_acceptance_problems": []})
    _write_json(
        quilt_acceptance,
        _ready_hardware_acceptance(target_type="lightfield"),
    )
    _write_json(quilt_support, {"display_acceptance_ready": True, "display_acceptance_problems": []})

    code = audit_completion.main(
        [
            "--require-target-display-hardware",
            "--inventory",
            str(inventory),
            "--desktop-acceptance",
            str(desktop_acceptance),
            "--desktop-support",
            str(desktop_support),
            "--stereo-acceptance",
            str(stereo_acceptance),
            "--stereo-support",
            str(stereo_support),
            "--quilt-acceptance",
            str(quilt_acceptance),
            "--quilt-support",
            str(quilt_support),
        ]
    )

    output = capsys.readouterr().out
    assert code == 1
    assert "stereo acceptance missing hardware_observation_path" in output
    assert "stereo acceptance checklist.backend_match is not true" in output
    assert "stereo acceptance checklist.hardware_observation_passed is not true" in output
    assert "stereo acceptance checklist.target_display_observation_matched is not true" in output
    assert "quilt acceptance" not in output


def test_audit_completion_requires_embedded_hardware_observation_payload(tmp_path, capsys):
    inventory = tmp_path / "inventory.json"
    desktop_acceptance = tmp_path / "desktop_acceptance.json"
    desktop_support = tmp_path / "desktop_support" / "manifest.json"
    stereo_acceptance = tmp_path / "stereo_acceptance.json"
    stereo_support = tmp_path / "stereo_support" / "manifest.json"
    quilt_acceptance = tmp_path / "quilt_acceptance.json"
    quilt_support = tmp_path / "quilt_support" / "manifest.json"

    ready_acceptance_without_payload = {
        "ready": True,
        "problems": [],
        "hardware_observation_path": "hardware_observation.yaml",
        "checklist": {
            "runtime_ready": True,
            "backend_match": True,
            "calibration_match": True,
            "hardware_observation_passed": True,
            "target_display_observation_matched": True,
        },
    }
    ready_support = {
        "display_acceptance": "display_acceptance/acceptance_report.json",
        "display_acceptance_ready": True,
        "display_acceptance_problems": [],
    }

    _write_json(
        inventory,
        {
            "display_inventory": [
                {
                    "name": "Acer SpatialLabs Display",
                    "device_id": "DISPLAY\\SPATIALLABS\\UID1",
                    "product_code": "SpatialLabs",
                }
            ]
        },
    )
    _write_json(desktop_acceptance, {"ready": True, "problems": []})
    _write_json(desktop_support, ready_support)
    _write_json(desktop_support.parent / "display_acceptance" / "acceptance_report.json", {"ready": True, "problems": []})
    _write_json(stereo_acceptance, ready_acceptance_without_payload)
    _write_json(stereo_support, ready_support)
    _write_json(stereo_support.parent / "display_acceptance" / "acceptance_report.json", ready_acceptance_without_payload)
    _write_json(quilt_acceptance, ready_acceptance_without_payload)
    _write_json(quilt_support, ready_support)
    _write_json(quilt_support.parent / "display_acceptance" / "acceptance_report.json", ready_acceptance_without_payload)

    code = audit_completion.main(
        [
            "--require-target-display-hardware",
            "--inventory",
            str(inventory),
            "--desktop-acceptance",
            str(desktop_acceptance),
            "--desktop-support",
            str(desktop_support),
            "--stereo-acceptance",
            str(stereo_acceptance),
            "--stereo-support",
            str(stereo_support),
            "--quilt-acceptance",
            str(quilt_acceptance),
            "--quilt-support",
            str(quilt_support),
        ]
    )

    output = capsys.readouterr().out
    assert code == 1
    assert "stereo acceptance missing hardware_observation payload" in output
    assert "stereo support bundle display_acceptance missing hardware_observation payload" in output
    assert "quilt acceptance missing hardware_observation payload" in output
    assert "quilt support bundle display_acceptance missing hardware_observation payload" in output


def test_audit_completion_rejects_placeholder_hardware_observation_payload(tmp_path, capsys):
    inventory = tmp_path / "inventory.json"
    desktop_acceptance = tmp_path / "desktop_acceptance.json"
    desktop_support = tmp_path / "desktop_support" / "manifest.json"
    stereo_acceptance = tmp_path / "stereo_acceptance.json"
    stereo_support = tmp_path / "stereo_support" / "manifest.json"
    quilt_acceptance = tmp_path / "quilt_acceptance.json"
    quilt_support = tmp_path / "quilt_support" / "manifest.json"

    ready_acceptance = {
        "ready": True,
        "problems": [],
        "hardware_observation_path": "hardware_observation.yaml",
        "hardware_observation": {
            "target_display_device_id": "DISPLAY\\REPLACE_WITH_TARGET_DEVICE_ID",
            "target_display_type": "autostereo",
            "eye_order_correct": True,
            "depth_direction_correct": True,
            "ui_readable": True,
            "head_tracking_stable": True,
            "crosstalk_percent": 8.0,
        },
        "checklist": {
            "runtime_ready": True,
            "backend_match": True,
            "calibration_match": True,
            "hardware_observation_passed": True,
            "target_display_observation_matched": True,
        },
    }
    ready_support = {
        "display_acceptance": "display_acceptance/acceptance_report.json",
        "display_acceptance_ready": True,
        "display_acceptance_problems": [],
    }

    _write_json(
        inventory,
        {
            "display_inventory": [
                {
                    "name": "Acer SpatialLabs Display",
                    "device_id": "DISPLAY\\SPATIALLABS\\UID1",
                    "product_code": "SpatialLabs",
                }
            ]
        },
    )
    _write_json(desktop_acceptance, {"ready": True, "problems": []})
    _write_json(desktop_support, ready_support)
    _write_json(desktop_support.parent / "display_acceptance" / "acceptance_report.json", {"ready": True, "problems": []})
    _write_json(stereo_acceptance, ready_acceptance)
    _write_json(stereo_support, ready_support)
    _write_json(stereo_support.parent / "display_acceptance" / "acceptance_report.json", ready_acceptance)
    _write_json(quilt_acceptance, {**ready_acceptance, "hardware_observation": {**ready_acceptance["hardware_observation"], "target_display_type": "lightfield"}})
    _write_json(quilt_support, ready_support)
    _write_json(
        quilt_support.parent / "display_acceptance" / "acceptance_report.json",
        {**ready_acceptance, "hardware_observation": {**ready_acceptance["hardware_observation"], "target_display_type": "lightfield"}},
    )

    code = audit_completion.main(
        [
            "--require-target-display-hardware",
            "--inventory",
            str(inventory),
            "--desktop-acceptance",
            str(desktop_acceptance),
            "--desktop-support",
            str(desktop_support),
            "--stereo-acceptance",
            str(stereo_acceptance),
            "--stereo-support",
            str(stereo_support),
            "--quilt-acceptance",
            str(quilt_acceptance),
            "--quilt-support",
            str(quilt_support),
        ]
    )

    output = capsys.readouterr().out
    assert code == 1
    assert "stereo acceptance hardware observation target_display_device_id is still a placeholder" in output
    assert "stereo support bundle display_acceptance hardware observation target_display_device_id is still a placeholder" in output
    assert "quilt acceptance hardware observation target_display_device_id is still a placeholder" in output
    assert "quilt support bundle display_acceptance hardware observation target_display_device_id is still a placeholder" in output


def test_audit_completion_requires_support_manifest_display_acceptance_path(tmp_path, capsys):
    inventory = tmp_path / "inventory.json"
    desktop_acceptance = tmp_path / "desktop_acceptance.json"
    desktop_support = tmp_path / "desktop_manifest.json"
    stereo_acceptance = tmp_path / "stereo_acceptance.json"
    stereo_support = tmp_path / "stereo_manifest.json"
    quilt_acceptance = tmp_path / "quilt_acceptance.json"
    quilt_support = tmp_path / "quilt_manifest.json"

    ready_acceptance = _ready_hardware_acceptance(
        device_id="DISPLAY\\LOOKINGGLASS\\UID1",
        target_type="lightfield",
    )
    ready_support = {
        "display_acceptance": "display_acceptance/acceptance_report.json",
        "display_acceptance_ready": True,
        "display_acceptance_problems": [],
    }

    _write_json(
        inventory,
        {
            "display_inventory": [
                {
                    "name": "Looking Glass 16",
                    "device_id": "DISPLAY\\LOOKINGGLASS\\UID1",
                    "product_code": "LookingGlass",
                }
            ]
        },
    )
    _write_json(desktop_acceptance, {"ready": True, "problems": []})
    _write_json(desktop_support, {"display_acceptance_ready": True, "display_acceptance_problems": []})
    _write_json(stereo_acceptance, ready_acceptance)
    _write_json(stereo_support, ready_support)
    _write_json(quilt_acceptance, ready_acceptance)
    _write_json(quilt_support, ready_support)

    code = audit_completion.main(
        [
            "--inventory",
            str(inventory),
            "--desktop-acceptance",
            str(desktop_acceptance),
            "--desktop-support",
            str(desktop_support),
            "--stereo-acceptance",
            str(stereo_acceptance),
            "--stereo-support",
            str(stereo_support),
            "--quilt-acceptance",
            str(quilt_acceptance),
            "--quilt-support",
            str(quilt_support),
        ]
    )

    output = capsys.readouterr().out
    assert code == 1
    assert "desktop support bundle missing display_acceptance path" in output


def test_audit_completion_requires_support_manifest_acceptance_report_to_exist(tmp_path, capsys):
    inventory = tmp_path / "inventory.json"
    desktop_acceptance = tmp_path / "desktop_acceptance.json"
    desktop_support = tmp_path / "desktop_support" / "manifest.json"
    stereo_acceptance = tmp_path / "stereo_acceptance.json"
    stereo_support = tmp_path / "stereo_support" / "manifest.json"
    quilt_acceptance = tmp_path / "quilt_acceptance.json"
    quilt_support = tmp_path / "quilt_support" / "manifest.json"

    ready_acceptance = _ready_hardware_acceptance(
        device_id="DISPLAY\\LOOKINGGLASS\\UID1",
        target_type="lightfield",
    )
    ready_support = {
        "display_acceptance": "display_acceptance/acceptance_report.json",
        "display_acceptance_ready": True,
        "display_acceptance_problems": [],
    }

    _write_json(
        inventory,
        {
            "display_inventory": [
                {
                    "name": "Looking Glass 16",
                    "device_id": "DISPLAY\\LOOKINGGLASS\\UID1",
                    "product_code": "LookingGlass",
                }
            ]
        },
    )
    _write_json(desktop_acceptance, {"ready": True, "problems": []})
    _write_json(desktop_support, ready_support)
    _write_json(stereo_acceptance, ready_acceptance)
    _write_json(stereo_support, ready_support)
    _write_json(quilt_acceptance, ready_acceptance)
    _write_json(quilt_support, ready_support)
    (desktop_support.parent / "display_acceptance").mkdir()
    _write_json(desktop_support.parent / "display_acceptance" / "acceptance_report.json", {"ready": True})

    code = audit_completion.main(
        [
            "--require-target-display-hardware",
            "--inventory",
            str(inventory),
            "--desktop-acceptance",
            str(desktop_acceptance),
            "--desktop-support",
            str(desktop_support),
            "--stereo-acceptance",
            str(stereo_acceptance),
            "--stereo-support",
            str(stereo_support),
            "--quilt-acceptance",
            str(quilt_acceptance),
            "--quilt-support",
            str(quilt_support),
        ]
    )

    output = capsys.readouterr().out
    assert code == 1
    assert "stereo support bundle display_acceptance artifact missing" in output
    assert "quilt support bundle display_acceptance artifact missing" in output


def test_audit_completion_rejects_support_manifest_acceptance_path_outside_bundle(tmp_path, capsys):
    inventory = tmp_path / "inventory.json"
    desktop_acceptance = tmp_path / "desktop_acceptance.json"
    desktop_support = tmp_path / "desktop_support" / "manifest.json"
    stereo_acceptance = tmp_path / "stereo_acceptance.json"
    stereo_support = tmp_path / "stereo_support" / "manifest.json"
    quilt_acceptance = tmp_path / "quilt_acceptance.json"
    quilt_support = tmp_path / "quilt_support" / "manifest.json"
    external_acceptance = tmp_path / "external" / "acceptance_report.json"

    ready_acceptance = {
        "ready": True,
        "problems": [],
        "hardware_observation_path": "hardware_observation.yaml",
        "checklist": {
            "runtime_ready": True,
            "backend_match": True,
            "calibration_match": True,
            "hardware_observation_passed": True,
            "target_display_observation_matched": True,
        },
    }
    ready_support = {
        "display_acceptance": "display_acceptance/acceptance_report.json",
        "display_acceptance_ready": True,
        "display_acceptance_problems": [],
    }

    _write_json(
        inventory,
        {
            "display_inventory": [
                {
                    "name": "Looking Glass 16",
                    "device_id": "DISPLAY\\LOOKINGGLASS\\UID1",
                    "product_code": "LookingGlass",
                }
            ]
        },
    )
    _write_json(external_acceptance, {"ready": True})
    _write_json(desktop_acceptance, {"ready": True, "problems": []})
    _write_json(
        desktop_support,
        {
            "display_acceptance": str(external_acceptance),
            "display_acceptance_ready": True,
            "display_acceptance_problems": [],
        },
    )
    _write_json(stereo_acceptance, ready_acceptance)
    _write_json(stereo_support, ready_support)
    _write_json(quilt_acceptance, ready_acceptance)
    _write_json(quilt_support, ready_support)
    for manifest in (stereo_support, quilt_support):
        _write_json(manifest.parent / "display_acceptance" / "acceptance_report.json", {"ready": True})

    code = audit_completion.main(
        [
            "--inventory",
            str(inventory),
            "--desktop-acceptance",
            str(desktop_acceptance),
            "--desktop-support",
            str(desktop_support),
            "--stereo-acceptance",
            str(stereo_acceptance),
            "--stereo-support",
            str(stereo_support),
            "--quilt-acceptance",
            str(quilt_acceptance),
            "--quilt-support",
            str(quilt_support),
        ]
    )

    output = capsys.readouterr().out
    assert code == 1
    assert "desktop support bundle display_acceptance path escapes support bundle" in output


def test_audit_completion_requires_support_manifest_acceptance_report_to_be_ready(tmp_path, capsys):
    inventory = tmp_path / "inventory.json"
    desktop_acceptance = tmp_path / "desktop_acceptance.json"
    desktop_support = tmp_path / "desktop_support" / "manifest.json"
    stereo_acceptance = tmp_path / "stereo_acceptance.json"
    stereo_support = tmp_path / "stereo_support" / "manifest.json"
    quilt_acceptance = tmp_path / "quilt_acceptance.json"
    quilt_support = tmp_path / "quilt_support" / "manifest.json"

    ready_acceptance = {
        "ready": True,
        "problems": [],
        "hardware_observation_path": "hardware_observation.yaml",
        "checklist": {
            "runtime_ready": True,
            "backend_match": True,
            "calibration_match": True,
            "hardware_observation_passed": True,
            "target_display_observation_matched": True,
        },
    }
    ready_support = {
        "display_acceptance": "display_acceptance/acceptance_report.json",
        "display_acceptance_ready": True,
        "display_acceptance_problems": [],
    }

    _write_json(
        inventory,
        {
            "display_inventory": [
                {
                    "name": "Looking Glass 16",
                    "device_id": "DISPLAY\\LOOKINGGLASS\\UID1",
                    "product_code": "LookingGlass",
                }
            ]
        },
    )
    _write_json(desktop_acceptance, {"ready": True, "problems": []})
    _write_json(desktop_support, ready_support)
    _write_json(stereo_acceptance, ready_acceptance)
    _write_json(stereo_support, ready_support)
    _write_json(quilt_acceptance, ready_acceptance)
    _write_json(quilt_support, ready_support)
    _write_json(desktop_support.parent / "display_acceptance" / "acceptance_report.json", {"ready": True, "problems": []})
    _write_json(
        stereo_support.parent / "display_acceptance" / "acceptance_report.json",
        {"ready": False, "problems": ["stale support acceptance"]},
    )
    _write_json(quilt_support.parent / "display_acceptance" / "acceptance_report.json", {"ready": True, "problems": []})

    code = audit_completion.main(
        [
            "--require-target-display-hardware",
            "--inventory",
            str(inventory),
            "--desktop-acceptance",
            str(desktop_acceptance),
            "--desktop-support",
            str(desktop_support),
            "--stereo-acceptance",
            str(stereo_acceptance),
            "--stereo-support",
            str(stereo_support),
            "--quilt-acceptance",
            str(quilt_acceptance),
            "--quilt-support",
            str(quilt_support),
        ]
    )

    output = capsys.readouterr().out
    assert code == 1
    assert "stereo support bundle display_acceptance artifact is not ready: stale support acceptance" in output


def test_audit_completion_requires_non_desktop_support_acceptance_hardware_evidence(tmp_path, capsys):
    inventory = tmp_path / "inventory.json"
    desktop_acceptance = tmp_path / "desktop_acceptance.json"
    desktop_support = tmp_path / "desktop_support" / "manifest.json"
    stereo_acceptance = tmp_path / "stereo_acceptance.json"
    stereo_support = tmp_path / "stereo_support" / "manifest.json"
    quilt_acceptance = tmp_path / "quilt_acceptance.json"
    quilt_support = tmp_path / "quilt_support" / "manifest.json"

    ready_acceptance = _ready_hardware_acceptance(
        device_id="DISPLAY\\LOOKINGGLASS\\UID1",
        target_type="lightfield",
    )
    ready_support = {
        "display_acceptance": "display_acceptance/acceptance_report.json",
        "display_acceptance_ready": True,
        "display_acceptance_problems": [],
    }

    _write_json(
        inventory,
        {
            "display_inventory": [
                {
                    "name": "Looking Glass 16",
                    "device_id": "DISPLAY\\LOOKINGGLASS\\UID1",
                    "product_code": "LookingGlass",
                }
            ]
        },
    )
    _write_json(desktop_acceptance, {"ready": True, "problems": []})
    _write_json(desktop_support, ready_support)
    _write_json(stereo_acceptance, ready_acceptance)
    _write_json(stereo_support, ready_support)
    _write_json(quilt_acceptance, ready_acceptance)
    _write_json(quilt_support, ready_support)
    _write_json(desktop_support.parent / "display_acceptance" / "acceptance_report.json", {"ready": True, "problems": []})
    _write_json(
        stereo_support.parent / "display_acceptance" / "acceptance_report.json",
        {"ready": True, "problems": []},
    )
    _write_json(quilt_support.parent / "display_acceptance" / "acceptance_report.json", ready_acceptance)

    code = audit_completion.main(
        [
            "--require-target-display-hardware",
            "--inventory",
            str(inventory),
            "--desktop-acceptance",
            str(desktop_acceptance),
            "--desktop-support",
            str(desktop_support),
            "--stereo-acceptance",
            str(stereo_acceptance),
            "--stereo-support",
            str(stereo_support),
            "--quilt-acceptance",
            str(quilt_acceptance),
            "--quilt-support",
            str(quilt_support),
        ]
    )

    output = capsys.readouterr().out
    assert code == 1
    assert "stereo support bundle display_acceptance missing hardware_observation_path" in output
    assert "stereo support bundle display_acceptance missing checklist" in output
    assert "quilt support bundle display_acceptance" not in output


def test_audit_completion_rejects_non_string_support_manifest_acceptance_path(tmp_path, capsys):
    inventory = tmp_path / "inventory.json"
    desktop_acceptance = tmp_path / "desktop_acceptance.json"
    desktop_support = tmp_path / "desktop_support" / "manifest.json"
    stereo_acceptance = tmp_path / "stereo_acceptance.json"
    stereo_support = tmp_path / "stereo_support" / "manifest.json"
    quilt_acceptance = tmp_path / "quilt_acceptance.json"
    quilt_support = tmp_path / "quilt_support" / "manifest.json"

    ready_acceptance = {
        "ready": True,
        "problems": [],
        "hardware_observation_path": "hardware_observation.yaml",
        "checklist": {
            "runtime_ready": True,
            "backend_match": True,
            "calibration_match": True,
            "hardware_observation_passed": True,
            "target_display_observation_matched": True,
        },
    }
    ready_support = {
        "display_acceptance": "display_acceptance/acceptance_report.json",
        "display_acceptance_ready": True,
        "display_acceptance_problems": [],
    }

    _write_json(
        inventory,
        {
            "display_inventory": [
                {
                    "name": "Looking Glass 16",
                    "device_id": "DISPLAY\\LOOKINGGLASS\\UID1",
                    "product_code": "LookingGlass",
                }
            ]
        },
    )
    _write_json(desktop_acceptance, {"ready": True, "problems": []})
    _write_json(
        desktop_support,
        {
            "display_acceptance": ["display_acceptance", "acceptance_report.json"],
            "display_acceptance_ready": True,
            "display_acceptance_problems": [],
        },
    )
    _write_json(stereo_acceptance, ready_acceptance)
    _write_json(stereo_support, ready_support)
    _write_json(quilt_acceptance, ready_acceptance)
    _write_json(quilt_support, ready_support)
    for manifest in (stereo_support, quilt_support):
        _write_json(manifest.parent / "display_acceptance" / "acceptance_report.json", {"ready": True, "problems": []})

    code = audit_completion.main(
        [
            "--inventory",
            str(inventory),
            "--desktop-acceptance",
            str(desktop_acceptance),
            "--desktop-support",
            str(desktop_support),
            "--stereo-acceptance",
            str(stereo_acceptance),
            "--stereo-support",
            str(stereo_support),
            "--quilt-acceptance",
            str(quilt_acceptance),
            "--quilt-support",
            str(quilt_support),
        ]
    )

    output = capsys.readouterr().out
    assert code == 1
    assert "desktop support bundle display_acceptance path must be a string" in output


def test_audit_completion_script_runs_directly_from_repo_root():
    result = subprocess.run(
        [sys.executable, "scripts/audit_completion.py", "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Audit Glassless3D completion artifacts" in result.stdout
