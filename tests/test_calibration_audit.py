import math
from unittest.mock import MagicMock, patch

import pytest

from launcher import calibration


@pytest.mark.parametrize(
    ("ipd_mm", "camera_fov_deg"),
    [
        (0.0, 90.0),
        (-1.0, 90.0),
        (float("nan"), 90.0),
        (float("inf"), 90.0),
        (64.0, 0.0),
        (64.0, 180.0),
        (64.0, float("nan")),
        (64.0, float("inf")),
        ("invalid", 90.0),
        (64.0, "invalid"),
    ],
)
def test_invalid_projection_calibration_is_rejected(ipd_mm, camera_fov_deg):
    assert calibration._validated_camera_parameters(
        ipd_mm,
        camera_fov_deg,
    ) is None


def test_valid_projection_calibration_is_normalized_to_floats():
    assert calibration._validated_camera_parameters("64", "90") == (64.0, 90.0)


def test_invalid_calibration_does_not_open_camera():
    mock_cv2 = MagicMock()
    with patch.dict("sys.modules", {"cv2": mock_cv2}):
        result = calibration.measure_head_distance_or_none(
            ipd_mm=64.0,
            camera_fov_deg=180.0,
        )

    assert result is None
    mock_cv2.VideoCapture.assert_not_called()


def test_invalid_calibration_uses_public_fallback_distance():
    assert calibration.measure_head_distance(
        ipd_mm=float("nan"),
        camera_fov_deg=90.0,
    ) == 60.0


def test_invalid_calibration_short_circuits_before_optional_dependencies():
    result = calibration._detect_face_distance_with_landmarker(
        object(),
        ipd_mm=64.0,
        landmarker=object(),
        camera_fov_deg=0.0,
    )
    assert result is None


def test_projection_math_remains_finite_at_supported_fov_limits():
    for fov in (1.0, 90.0, 179.0):
        ipd, validated_fov = calibration._validated_camera_parameters(64.0, fov)
        focal_scale = 1.0 / (
            2.0 * math.tan(math.radians(validated_fov / 2.0))
        )
        assert ipd == 64.0
        assert math.isfinite(focal_scale)
        assert focal_scale > 0.0
