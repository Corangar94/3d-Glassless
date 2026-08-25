from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from tracker.camera_calibration import (
    CheckerboardObservation,
    calibrate_intrinsics,
    center_align_geometry,
    generated_checkerboard_image,
    parse_pattern_size,
    update_config_camera_geometry,
)
from tracker.camera_geometry import (
    CameraExtrinsics,
    CameraGeometry,
    CameraIntrinsics,
    euler_degrees_from_rotation_matrix,
    rotation_matrix_from_euler_degrees,
)


def test_intrinsics_scale_with_capture_resolution():
    intrinsics = CameraIntrinsics(
        width=1280,
        height=720,
        fx=800.0,
        fy=810.0,
        cx=640.0,
        cy=360.0,
    )

    scaled = intrinsics.scaled(640, 360)

    assert scaled.fx == pytest.approx(400.0)
    assert scaled.fy == pytest.approx(405.0)
    assert scaled.cx == pytest.approx(320.0)
    assert scaled.cy == pytest.approx(180.0)


def test_center_pixel_projects_to_camera_origin_translation():
    geometry = CameraGeometry(
        intrinsics=CameraIntrinsics(
            width=1280,
            height=720,
            fx=900.0,
            fy=900.0,
            cx=640.0,
            cy=360.0,
        ),
        extrinsics=CameraExtrinsics.from_euler_and_translation(
            translation_cm=(2.0, 18.0, -1.0),
        ),
    )

    position = geometry.pixel_depth_to_screen(
        640.0,
        360.0,
        60.0,
        image_width=1280,
        image_height=720,
    )

    assert position == pytest.approx((2.0, 18.0, 59.0))


def test_mount_rotation_is_applied_to_pose_orientation():
    geometry = CameraGeometry(
        intrinsics=None,
        extrinsics=CameraExtrinsics.from_euler_and_translation(
            yaw_deg=10.0,
            pitch_deg=-5.0,
            roll_deg=2.0,
        ),
    )

    yaw, pitch, roll = geometry.orientation_to_screen(0.0, 0.0, 0.0)

    assert yaw == pytest.approx(10.0, abs=0.1)
    assert pitch == pytest.approx(-5.0, abs=0.1)
    assert roll == pytest.approx(2.0, abs=0.1)


def test_euler_matrix_round_trip():
    matrix = rotation_matrix_from_euler_degrees(14.0, -7.0, 3.0)
    assert euler_degrees_from_rotation_matrix(matrix) == pytest.approx(
        (14.0, -7.0, 3.0), abs=0.1
    )


def test_center_alignment_estimates_screen_origin_translation():
    geometry = CameraGeometry(
        intrinsics=CameraIntrinsics(
            width=1280,
            height=720,
            fx=900.0,
            fy=900.0,
            cx=640.0,
            cy=360.0,
        ),
        extrinsics=CameraExtrinsics.from_euler_and_translation(pitch_deg=5.0),
    )
    samples = [
        (0.2, -17.8, 60.2),
        (-0.1, -18.1, 59.8),
        (0.0, -18.0, 60.0),
        (0.1, -17.9, 60.1),
    ]

    aligned = center_align_geometry(
        geometry,
        samples,
        viewer_distance_cm=60.0,
    )
    transformed = (
        aligned.extrinsics.rotation_matrix @ np.median(np.asarray(samples), axis=0)
        + aligned.extrinsics.translation_vector
    )

    assert transformed == pytest.approx((0.0, 0.0, 60.0), abs=1e-6)


def test_config_geometry_loads_intrinsics_and_extrinsics():
    config = {
        "camera": {"width": 1280, "height": 720},
        "tracking": {
            "camera_calibration": {
                "intrinsics": {
                    "width": 1280,
                    "height": 720,
                    "fx": 900.0,
                    "fy": 905.0,
                    "cx": 640.0,
                    "cy": 360.0,
                    "distortion": [0.1, -0.05, 0.0, 0.0, 0.01],
                },
                "extrinsics": {
                    "rotation_deg": {"yaw": 1.0, "pitch": -12.0, "roll": 0.5},
                    "translation_camera_origin_cm": [0.0, 18.0, 0.0],
                },
                "mirror_x": True,
            }
        },
    }

    geometry = CameraGeometry.from_config(config)

    assert geometry is not None
    assert geometry.intrinsics is not None
    assert geometry.intrinsics.fx == 900.0
    assert geometry.extrinsics.translation_camera_origin_cm == (0.0, 18.0, 0.0)


def test_geometry_persists_atomically_in_tracking_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("tracking:\n  ipd_cm: 6.4\n", encoding="utf-8")
    geometry = CameraGeometry(
        intrinsics=CameraIntrinsics(
            width=640,
            height=480,
            fx=500.0,
            fy=505.0,
            cx=320.0,
            cy=240.0,
        ),
        extrinsics=CameraExtrinsics.from_euler_and_translation(
            translation_cm=(0.0, 12.0, 0.0)
        ),
    )

    update_config_camera_geometry(config_path, geometry)

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["tracking"]["ipd_cm"] == 6.4
    assert saved["tracking"]["camera_calibration"]["intrinsics"]["fx"] == 500.0
    assert not Path(str(config_path) + ".tmp").exists()


def test_generated_board_has_requested_inner_corner_count():
    image = generated_checkerboard_image(
        pattern_size=(9, 6),
        square_pixels=48,
        margin_pixels=48,
    )
    found, corners = cv2.findChessboardCornersSB(image, (9, 6))

    assert found
    assert corners.shape[0] == 54


def test_synthetic_checkerboard_views_recover_intrinsics():
    pattern = parse_pattern_size("9x6")
    object_points = np.zeros((pattern[0] * pattern[1], 3), np.float32)
    object_points[:, :2] = np.mgrid[0 : pattern[0], 0 : pattern[1]].T.reshape(-1, 2)
    object_points[:, :2] *= 2.5
    matrix = np.array(
        ((820.0, 0.0, 640.0), (0.0, 825.0, 360.0), (0.0, 0.0, 1.0)),
        dtype=np.float64,
    )
    observations = []
    for index in range(12):
        rotation = np.array(
            (0.04 * ((index % 3) - 1), 0.05 * ((index % 4) - 1.5), 0.02 * index),
            dtype=np.float64,
        )
        translation = np.array(
            (-8.0 + index * 1.2, -5.0 + (index % 4) * 2.0, 55.0 + index),
            dtype=np.float64,
        )
        projected, _ = cv2.projectPoints(
            object_points,
            rotation,
            translation,
            matrix,
            np.zeros(5),
        )
        observations.append(
            CheckerboardObservation(
                image_size=(1280, 720),
                corners=projected.astype(np.float32),
                source=str(index),
            )
        )

    result = calibrate_intrinsics(
        observations,
        pattern_size=pattern,
        square_size_cm=2.5,
    )

    assert result.views_used >= 6
    assert result.intrinsics.fx == pytest.approx(820.0, rel=0.03)
    assert result.intrinsics.fy == pytest.approx(825.0, rel=0.03)
    assert result.mean_reprojection_error_px < 0.2
