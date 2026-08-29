import numpy as np
import pytest

from tracker.camera_geometry import rotation_matrix_from_euler_degrees
from tracker.face_tracker import matrix_to_euler_degrees


def _homogeneous(rotation: np.ndarray) -> np.ndarray:
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = rotation
    return matrix


@pytest.mark.parametrize(
    ("yaw", "pitch", "roll"),
    [
        (30.0, 20.0, 15.0),
        (45.0, -25.0, 35.0),
        (60.0, 40.0, -30.0),
        (-35.0, 18.0, -22.0),
    ],
)
def test_media_pipe_rotation_uses_camera_geometry_euler_convention(
    yaw: float,
    pitch: float,
    roll: float,
):
    matrix = _homogeneous(
        rotation_matrix_from_euler_degrees(
            yaw_deg=yaw,
            pitch_deg=pitch,
            roll_deg=roll,
        )
    )

    measured = matrix_to_euler_degrees(matrix)

    assert measured[0] == pytest.approx(yaw, abs=1e-6)
    assert measured[1] == pytest.approx(pitch, abs=1e-6)
    assert measured[2] == pytest.approx(roll, abs=1e-6)


def test_media_pipe_rotation_normalization_removes_axis_scale_before_decomposition():
    rotation = rotation_matrix_from_euler_degrees(
        yaw_deg=42.0,
        pitch_deg=-17.0,
        roll_deg=28.0,
    )
    scaled = rotation @ np.diag((1.12, 0.91, 1.04))
    matrix = _homogeneous(scaled)

    measured = matrix_to_euler_degrees(matrix)

    assert measured[0] == pytest.approx(42.0, abs=1e-5)
    assert measured[1] == pytest.approx(-17.0, abs=1e-5)
    assert measured[2] == pytest.approx(28.0, abs=1e-5)


def test_invalid_media_pipe_transform_falls_back_to_neutral_orientation():
    malformed = np.zeros((4, 4), dtype=np.float64)

    assert matrix_to_euler_degrees(malformed) == (0.0, 0.0, 0.0)
    assert matrix_to_euler_degrees(object()) == (0.0, 0.0, 0.0)
