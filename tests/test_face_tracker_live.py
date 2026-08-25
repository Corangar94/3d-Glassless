import math

import numpy as np
import pytest

from tracker.face_tracker import (
    estimate_z_cm,
    matrix_to_euler_degrees,
)


def _rotation_y(degrees: float) -> np.ndarray:
    radians = math.radians(degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = np.array(
        [
            [cosine, 0.0, sine],
            [0.0, 1.0, 0.0],
            [-sine, 0.0, cosine],
        ]
    )
    return matrix


def test_facial_transform_extracts_yaw_even_with_uniform_scale():
    matrix = _rotation_y(30.0)
    matrix[:3, :3] *= 1.8

    yaw, pitch, roll = matrix_to_euler_degrees(matrix)

    assert yaw == pytest.approx(30.0, abs=0.1)
    assert pitch == pytest.approx(0.0, abs=0.1)
    assert roll == pytest.approx(0.0, abs=0.1)


def test_yaw_correction_cancels_iris_foreshortening():
    front = estimate_z_cm(
        ipd_px=100.0,
        image_width=1280,
        real_ipd_cm=6.4,
        camera_fov_deg=90.0,
        yaw_deg=0.0,
    )
    turned = estimate_z_cm(
        ipd_px=100.0 * math.cos(math.radians(45.0)),
        image_width=1280,
        real_ipd_cm=6.4,
        camera_fov_deg=90.0,
        yaw_deg=45.0,
    )

    assert turned == pytest.approx(front, rel=0.01)


def test_malformed_transform_degrades_to_zero_orientation():
    assert matrix_to_euler_degrees(np.zeros((2, 2))) == (0.0, 0.0, 0.0)
