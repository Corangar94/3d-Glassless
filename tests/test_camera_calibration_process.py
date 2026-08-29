import pytest

from launcher.camera_calibration_process import (
    CameraCaptureConfig,
    build_board_command,
    build_center_command,
    build_intrinsics_command,
)


def test_source_intrinsics_command_dispatches_through_launcher_module():
    command = build_intrinsics_command(
        config_path="C:/cfg/config.yaml",
        camera=CameraCaptureConfig(index=2, width=1920, height=1080, fps=60.0),
        square_mm=24.5,
        executable="python.exe",
        frozen=False,
    )

    assert command[:4] == [
        "python.exe",
        "-m",
        "launcher",
        "--camera-calibration-child",
    ]
    assert command[4] == "intrinsics"
    assert command[command.index("--camera-index") + 1] == "2"
    assert command[command.index("--width") + 1] == "1920"
    assert command[command.index("--height") + 1] == "1080"
    assert command[command.index("--fps") + 1] == "60"
    assert command[command.index("--square-mm") + 1] == "24.5"


def test_frozen_center_command_reuses_the_standalone_executable():
    command = build_center_command(
        config_path="config.yaml",
        camera=CameraCaptureConfig(),
        viewer_distance_cm=68.0,
        ipd_cm=6.35,
        executable="Glassless3D.exe",
        frozen=True,
    )

    assert command[:3] == [
        "Glassless3D.exe",
        "--camera-calibration-child",
        "center",
    ]
    assert command[command.index("--viewer-distance-cm") + 1] == "68"
    assert command[command.index("--ipd-cm") + 1] == "6.35"


def test_board_command_works_in_source_and_frozen_modes():
    source = build_board_command(
        "board.png",
        executable="python.exe",
        frozen=False,
    )
    frozen = build_board_command(
        "board.png",
        executable="Glassless3D.exe",
        frozen=True,
    )

    assert source[-4:] == ["board", "board.png", "--pattern", "9x6"]
    assert frozen[-4:] == ["board", "board.png", "--pattern", "9x6"]


def test_invalid_capture_and_physical_values_fail_before_spawning():
    with pytest.raises(ValueError):
        CameraCaptureConfig(width=0)
    with pytest.raises(ValueError):
        build_intrinsics_command(
            config_path="config.yaml",
            camera=CameraCaptureConfig(),
            square_mm=0.0,
        )
    with pytest.raises(ValueError):
        build_center_command(
            config_path="config.yaml",
            camera=CameraCaptureConfig(),
            viewer_distance_cm=-1.0,
            ipd_cm=6.4,
        )
