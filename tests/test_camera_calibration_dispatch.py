from pathlib import Path


def _source(relative: str) -> str:
    return Path(relative).read_text(encoding="utf-8")


def test_launcher_exposes_public_wizard_and_private_child_modes():
    source = _source("launcher/__main__.py")

    assert '"--calibrate-camera"' in source
    assert '"--camera-calibration-child"' in source
    assert "camera_calibration_wizard" in source
    assert "scripts.calibrate_camera" in source
    assert source.index('"--tracker-child"') < source.index('"--camera-calibration-child"')


def test_cli_synchronizes_calibrated_projection_after_both_geometry_steps():
    source = _source("scripts/calibrate_camera.py")

    assert "from tracker.calibration_runtime_sync import synchronize_runtime_projection" in source
    assert source.count("synchronize_runtime_projection(") == 2
    assert "viewer_distance_cm=args.viewer_distance_cm" in source


def test_calibration_wizard_uses_separate_child_process_for_camera_work():
    source = _source("launcher/camera_calibration_wizard.py")

    assert "subprocess.run(" in source
    assert "threading.Thread(target=worker, daemon=True).start()" in source
    assert "build_intrinsics_command(" in source
    assert "build_center_command(" in source
    assert "build_board_command(" in source
    assert "Stop tracking" in source


def test_wizard_child_commands_support_frozen_application_without_python_install():
    source = _source("launcher/camera_calibration_process.py")

    assert 'return [exe, "--camera-calibration-child"]' in source
    assert 'return [exe, "-m", "launcher", "--camera-calibration-child"]' in source
